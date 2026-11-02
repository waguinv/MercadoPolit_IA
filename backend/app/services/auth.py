from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.services import audit


def authenticate(db: Session, email: str, password: str) -> str | None:
    """Retorna token em caso de sucesso. Falhas são auditadas sem expor o motivo."""
    user = db.scalar(select(User).where(User.email == email.lower()))
    ok = verify_password(password, user.password_hash if user else None)
    if not (user and ok and user.is_active):
        audit.record(db, action="auth.login_failed", organization_id=None,
                     details={"email": email.lower()})
        db.commit()
        return None
    audit.record(db, action="auth.login", organization_id=user.organization_id,
                 actor_user_id=user.id, entity_type="user", entity_id=user.id)
    db.commit()
    return create_access_token(user.id)
