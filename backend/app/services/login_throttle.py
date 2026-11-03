"""Limite de tentativas de login.

Cada tentativa é RESERVADA antes de conferir a senha, sob trava. Contar só as falhas, depois de conferir,
deixaria passar um lote de tentativas simultâneas: todas leriam o contador antes de a primeira falhar.

Três limites, para equilibrar proteção e bloqueio indevido:
- conta + IP (o menor): quem erra a senha não atrapalha a mesma pessoa em outro lugar;
- conta: ataque distribuído contra uma pessoa. Quem já está logado continua, pois a renovação não passa aqui;
- IP: tentar uma senha em muitas contas. Uma loja inteira pode sair pelo mesmo IP, por isso só falhas contam.
"""
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.login_throttle import LoginThrottle
from app.services import audit


class TooManyAttempts(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Muitas tentativas.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class Key:
    name: str
    value: str
    limit: int
    # False: um acerto só devolve a própria tentativa, sem apagar as falhas de outras pessoas.
    reset_on_success: bool = True

    @property
    def hash(self) -> str:
        return hashlib.sha256(f"{self.name}:{self.value}".encode()).hexdigest()


def login_keys(email: str, ip: str | None) -> list[Key]:
    s = get_settings()
    email, ip = email.strip().lower(), ip or "desconhecido"
    return [
        Key("conta_ip", f"{email}|{ip}", s.login_max_per_account_and_ip),
        Key("conta", email, s.login_max_per_account),
        Key("ip", ip, s.login_max_per_ip, reset_on_success=False),
    ]


def password_change_keys(user_id: object) -> list[Key]:
    return [Key("troca_senha", str(user_id), get_settings().login_max_per_account_and_ip)]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)  # SQLite devolve sem fuso


def _locked_row(db: Session, key: Key) -> LoginThrottle | None:
    return db.scalar(select(LoginThrottle).where(LoginThrottle.key_hash == key.hash).with_for_update()
                     .execution_options(populate_existing=True))


def _reserve_once(db: Session, keys: list[Key], window: timedelta, now: datetime, context: dict) -> None:
    # Ordem fixa: dois logins travando as mesmas linhas nunca entram em deadlock.
    ordered = sorted(keys, key=lambda k: k.hash)
    rows = []
    for key in ordered:
        row = _locked_row(db, key)
        if row is None:
            row = LoginThrottle(key_hash=key.hash, attempts=0, window_started_at=now)
            db.add(row)
            db.flush()  # Corrida na criação vira IntegrityError, e a tentativa recomeça.
        elif now - _as_utc(row.window_started_at) >= window:
            row.attempts, row.window_started_at = 0, now
        rows.append((key, row))
    blocked = [_as_utc(row.window_started_at) + window - now
               for key, row in rows if row.attempts >= key.limit]
    if blocked:
        db.commit()
        raise TooManyAttempts(max(1, int(max(blocked).total_seconds()) + 1))
    for key, row in rows:
        row.attempts += 1
        if row.attempts == key.limit:
            # Uma vez por bloqueio: tentativas barradas não escrevem nada, para não encher a auditoria.
            audit.record(db, action="auth.login_limit_reached", organization_id=None,
                         details={**context, "limit": key.name})
    db.commit()


def reserve(db: Session, keys: list[Key], **context) -> None:
    """Conta a tentativa ou levanta TooManyAttempts, sem conferir a senha."""
    window = timedelta(minutes=get_settings().login_window_minutes)
    for attempt in range(3):
        try:
            _reserve_once(db, keys, window, utcnow(), context)
            return
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise


def succeeded(db: Session, keys: list[Key]) -> None:
    """Acerto. Quem chama faz o commit."""
    ordered = sorted(keys, key=lambda k: k.hash)
    for key in ordered:
        row = _locked_row(db, key)
        if row is None:
            continue
        if key.reset_on_success:
            db.delete(row)
        elif row.attempts > 0:
            row.attempts -= 1
    # Limpeza oportunista: janelas vencidas não têm mais utilidade.
    limit = utcnow() - timedelta(days=1)
    db.execute(delete(LoginThrottle).where(LoginThrottle.window_started_at < limit)
               .execution_options(synchronize_session=False))
