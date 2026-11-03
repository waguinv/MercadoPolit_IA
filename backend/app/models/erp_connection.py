import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class ErpConnection(IdMixin, TimestampMixin, Base):
    """Credencial do ERP de UMA organização. O token fica cifrado (Fernet); nunca é devolvido pela API."""

    __tablename__ = "erp_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="sysordem")
    token_encrypted: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
