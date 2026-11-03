"""Renovação do login com rotação: cada uso troca o token.

Usar um token já trocado indica roubo, e a família inteira é revogada. A exceção é a janela curta de
tolerância: em rede ruim a resposta da renovação se perde e o aparelho repete o pedido, e duas abas podem
renovar juntas. Sem ela, rede instável derrubaria o login no meio da contagem.
"""
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import audit

REUSE_GRACE = timedelta(seconds=60)


class InvalidRefreshToken(Exception):
    pass


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)  # SQLite devolve sem fuso


def _new_token(db: Session, user: User, family_id: uuid.UUID) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(RefreshToken(
        organization_id=user.organization_id, user_id=user.id, family_id=family_id, token_hash=_hash(raw),
        expires_at=utcnow() + timedelta(days=get_settings().refresh_token_expire_days)))
    return raw


def issue(db: Session, user: User) -> str:
    """Novo login. Quem chama faz o commit."""
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < utcnow())
               .execution_options(synchronize_session=False))
    return _new_token(db, user, uuid.uuid4())


def _revoke(db: Session, *condition) -> None:
    db.execute(update(RefreshToken).where(*condition, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def revoke_user(db: Session, user_id: uuid.UUID) -> None:
    """Encerra todos os logins da pessoa. Quem chama faz o commit."""
    _revoke(db, RefreshToken.user_id == user_id)


def revoke_login(db: Session, raw: str | None) -> None:
    """Sair: encerra só este login. Token desconhecido não é erro."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw))) if raw else None
    if row is not None:
        _revoke(db, RefreshToken.family_id == row.family_id)
        audit.record(db, action="auth.logout", organization_id=row.organization_id, actor_user_id=row.user_id,
                     entity_type="user", entity_id=row.user_id)
    db.commit()


def rotate(db: Session, raw: str | None) -> tuple[User, str]:
    """Troca o token por um novo e devolve a pessoa. A trava serializa pedidos com o mesmo token."""
    row = None
    if raw:
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw)).with_for_update())
    if row is None or row.revoked_at is not None or _as_utc(row.expires_at) <= utcnow():
        raise InvalidRefreshToken
    user = db.get(User, row.user_id)
    org = db.get(Organization, row.organization_id)
    if user is None or not user.is_active or org is None or not org.is_active:
        _revoke(db, RefreshToken.user_id == row.user_id)
        db.commit()
        raise InvalidRefreshToken
    if row.rotated_at is not None and utcnow() - _as_utc(row.rotated_at) > REUSE_GRACE:
        _revoke(db, RefreshToken.family_id == row.family_id)
        audit.record(db, action="auth.refresh_reuse_detected", organization_id=row.organization_id,
                     actor_user_id=row.user_id, entity_type="user", entity_id=row.user_id)
        db.commit()
        raise InvalidRefreshToken
    if row.rotated_at is None:
        row.rotated_at = utcnow()
    new_raw = _new_token(db, user, row.family_id)
    db.commit()
    return user, new_raw
