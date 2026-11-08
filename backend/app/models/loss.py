"""Perdas e avarias (Etapa 5.2 da Central de Movimentações). Nenhuma escrita no ERP nesta etapa."""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.consumption import ConsumptionStatus

QTY = Numeric(18, 3)


class LossReason(enum.StrEnum):
    DAMAGED_PACKAGE = "damaged_package"
    SPOILAGE = "spoilage"
    EXPIRED = "expired"
    BREAKAGE = "breakage"
    CONTAMINATION = "contamination"
    OPERATIONAL = "operational"
    OTHER = "other"


class StockLoss(IdMixin, TimestampMixin, Base):
    __tablename__ = "stock_losses"
    __table_args__ = (
        UniqueConstraint("organization_id", "registered_by", "client_request_id", name="uq_stock_loss_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    reason: Mapped[str] = mapped_column(String(20))
    lot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Quem registrou é o responsável pela informação; o aprovador fica à parte.
    registered_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    # Mesmos estados do consumo interno.
    status: Mapped[str] = mapped_column(
        String(20), default=ConsumptionStatus.PENDING_APPROVAL.value, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(64))
    # Etapa 5.2 NUNCA escreve no ERP: não há contrato homologado para perdas.
    erp_status: Mapped[str] = mapped_column(String(20), default="not_sent")
