import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

DEFAULT_STORE_TIMEZONE = "America/Sao_Paulo"


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Store(IdMixin, TimestampMixin, Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_store_org_code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(50))
    # O dia de uma operação (validade no recebimento, por exemplo) é o dia na loja, não em UTC.
    timezone: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_STORE_TIMEZONE, server_default=DEFAULT_STORE_TIMEZONE
    )
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
