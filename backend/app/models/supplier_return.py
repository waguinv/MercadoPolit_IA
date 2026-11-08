"""Devoluções e trocas com fornecedor (Etapa 5.3 da Central de Movimentações). Nenhuma escrita no ERP."""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

QTY = Numeric(18, 3)


class ReturnStatus(enum.StrEnum):
    PENDING_APPROVAL = "pending_approval"
    NEEDS_CORRECTION = "needs_correction"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PICKED_UP = "picked_up"  # a mercadoria saiu da loja
    COMPLETED = "completed"


class ReturnReason(enum.StrEnum):
    DAMAGED = "damaged"
    EXPIRED = "expired"
    NEAR_EXPIRY = "near_expiry"
    QUALITY = "quality"
    INCORRECT_ITEM = "incorrect_item"
    OTHER = "other"


class ReturnModality(enum.StrEnum):
    EXCHANGE = "exchange"
    CREDIT = "credit"
    NO_REPLACEMENT = "no_replacement"


class SupplierReturn(IdMixin, TimestampMixin, Base):
    __tablename__ = "supplier_returns"
    __table_args__ = (
        UniqueConstraint("organization_id", "registered_by", "client_request_id",
                         name="uq_supplier_return_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    reason: Mapped[str] = mapped_column(String(20))
    modality: Mapped[str] = mapped_column(String(20))
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_document: Mapped[str | None] = mapped_column(String(14), nullable=True)
    # Recebimento de origem, quando a mercadoria passou pelo recebimento do sistema.
    receiving_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("receivings.id"), nullable=True, index=True)
    origin_document: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expected_pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_replacement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registered_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default=ReturnStatus.PENDING_APPROVAL.value, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Saída devolvida: evento próprio, distinto da aprovação e da reposição.
    picked_up_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pickup_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(64))
    # Etapa 5.3 NUNCA escreve no ERP: não há contrato homologado para devoluções.
    erp_status: Mapped[str] = mapped_column(String(20), default="not_sent")


class SupplierReturnReplacement(IdMixin, TimestampMixin, Base):
    """Reposição recebida: sempre aponta para um recebimento finalizado (conferido)."""
    __tablename__ = "supplier_return_replacements"
    __table_args__ = (
        UniqueConstraint("return_id", "receiving_id", name="uq_return_replacement_receiving"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    return_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("supplier_returns.id"), index=True)
    receiving_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("receivings.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    registered_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
