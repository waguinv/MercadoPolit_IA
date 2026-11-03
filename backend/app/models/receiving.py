"""Recebimento manual de mercadorias (sem pedido obrigatório). Nenhuma escrita no ERP nesta etapa."""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow

QTY = Numeric(18, 3)


class ReceivingStatus(enum.StrEnum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


class RejectionReason(enum.StrEnum):
    DAMAGED = "damaged"            # avaria / embalagem danificada
    INCORRECT_ITEM = "incorrect_item"
    EXPIRED = "expired"
    QUALITY = "quality"
    OTHER = "other"


_NOT_CANCELLED = text("status != 'cancelled'")


class Receiving(IdMixin, TimestampMixin, Base):
    __tablename__ = "receivings"
    __table_args__ = (
        # NF-e já recebida (chave de acesso) não pode ser recebida de novo, salvo se a anterior foi cancelada.
        Index("uq_receiving_access_key", "organization_id", "access_key", unique=True,
              sqlite_where=text("access_key is not null and status != 'cancelled'"),
              postgresql_where=text("access_key is not null and status != 'cancelled'")),
        Index("uq_receiving_supplier_doc", "organization_id", "supplier_document", "document_series",
              "document_number", unique=True,
              sqlite_where=text("supplier_document is not null and document_number is not null "
                                "and status != 'cancelled'"),
              postgresql_where=text("supplier_document is not null and document_number is not null "
                                    "and status != 'cancelled'")),
        UniqueConstraint("organization_id", "created_by", "client_request_id", name="uq_receiving_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"))
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_document: Mapped[str | None] = mapped_column(String(14), nullable=True)  # CNPJ/CPF, só dígitos
    document_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    document_series: Mapped[str] = mapped_column(String(10), default="")
    access_key: Mapped[str | None] = mapped_column(String(44), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default=ReceivingStatus.DRAFT.value, index=True)
    is_partial: Mapped[bool | None] = mapped_column(nullable=True)  # definido ao finalizar
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Etapa 4 NUNCA escreve no ERP (não há contrato confirmado de entrada de mercadoria).
    erp_application_status: Mapped[str] = mapped_column(String(20), default="not_applied")


class ReceivingItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "receiving_items"
    __table_args__ = (
        UniqueConstraint("receiving_id", "product_id", "lot", "expiry_date", name="uq_item_product_lot"),
        UniqueConstraint("receiving_id", "client_request_id", name="uq_item_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    receiving_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("receivings.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    expected_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    received_quantity: Mapped[Decimal] = mapped_column(QTY)
    accepted_quantity: Mapped[Decimal] = mapped_column(QTY)
    rejected_quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    rejection_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot: Mapped[str] = mapped_column(String(64), default="")
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
