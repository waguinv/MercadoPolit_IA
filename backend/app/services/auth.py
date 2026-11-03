from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.services import audit, refresh_tokens


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Devolve a pessoa em caso de sucesso. Falhas são auditadas sem expor o motivo."""
    user = db.scalar(select(User).where(User.email == email.lower()))
    ok = verify_password(password, user.password_hash if user else None)
    if not (user and ok and user.is_active):
        audit.record(db, action="auth.login_failed", organization_id=None,
                     details={"email": email.lower()})
        db.commit()
        return None
    audit.record(db, action="auth.login", organization_id=user.organization_id,
                 actor_user_id=user.id, entity_type="user", entity_id=user.id)
    return user


def change_password(db: Session, user: User, current: str, new: str) -> str | None:
    """Troca a própria senha e encerra os outros logins. Devolve o novo token de renovação deste aparelho."""
    if not verify_password(current, user.password_hash):
        audit.record(db, action="auth.password_change_failed", organization_id=user.organization_id,
                     actor_user_id=user.id, entity_type="user", entity_id=user.id)
        db.commit()
        return None
    user.password_hash = hash_password(new)
    refresh_tokens.revoke_user(db, user.id)
    raw = refresh_tokens.issue(db, user)
    audit.record(db, action="auth.password_changed", organization_id=user.organization_id,
                 actor_user_id=user.id, entity_type="user", entity_id=user.id)
    db.commit()
    return raw
