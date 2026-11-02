from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent

_FORBIDDEN_KEYS = {"password", "password_hash", "token", "access_token", "secret"}


def record(
    db: Session,
    *,
    action: str,
    organization_id: UUID | None,
    actor_user_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: object | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    clean = {k: v for k, v in (details or {}).items() if k.lower() not in _FORBIDDEN_KEYS}
    db.add(
        AuditEvent(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=clean or None,
        )
    )
