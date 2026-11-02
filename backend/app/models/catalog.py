import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class UnitReference(IdMixin, TimestampMixin, Base):
    """Unidade de medida espelhada do ERP (fonte oficial)."""

    __tablename__ = "unit_references"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_unit_org_code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    allows_fraction: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProductReference(IdMixin, TimestampMixin, Base):
    """Produto espelhado do ERP. Somente leitura para os usuários: criado/atualizado pela sincronização."""

    __tablename__ = "product_references"
    __table_args__ = (UniqueConstraint("organization_id", "external_id", name="uq_product_org_ext"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StockLocation(IdMixin, TimestampMixin, Base):
    """Local de estoque de uma loja (ex.: depósito, loja, padaria, câmara fria)."""

    __tablename__ = "stock_locations"
    __table_args__ = (UniqueConstraint("store_id", "code", name="uq_location_store_code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    # Código do local no ERP; necessário para consultar saldo por local.
    external_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
