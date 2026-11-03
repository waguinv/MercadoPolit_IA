"""Inventário colaborativo. Nenhuma escrita no ERP: o resultado final são PROPOSTAS de ajuste."""
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow

QTY = Numeric(18, 3)


class SessionStatus(enum.StrEnum):
    OPEN = "open"            # contando
    REVIEW = "review"        # contagem encerrada; resolvendo divergências
    FINALIZED = "finalized"  # resultado fechado (propostas geradas; NADA enviado ao ERP)
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (SessionStatus.OPEN.value, SessionStatus.REVIEW.value)


class DiscrepancyKind(enum.StrEnum):
    CONFLICTING_COUNTS = "conflicting_counts"            # contadores diferentes discordam
    ERP_CHANGED = "erp_changed_since_baseline"           # saldo do ERP mudou após a abertura
    NO_ERP_BALANCE = "no_erp_balance"                    # ERP sem registro de saldo (≠ zero)
    DIFFERS_FROM_ERP = "differs_from_erp"                # contagem difere do saldo do ERP


class DiscrepancyStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"  # substituída por recontagem


class InventorySession(IdMixin, TimestampMixin, Base):
    __tablename__ = "inventory_sessions"
    __table_args__ = (
        # No máximo UMA sessão ativa por local (vale para SQLite e PostgreSQL).
        Index("uq_inventory_active_location", "location_id", unique=True,
              sqlite_where=text("status in ('open','review')"),
              postgresql_where=text("status in ('open','review')")),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=SessionStatus.OPEN.value, index=True)
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    baseline_unmapped_count: Mapped[int] = mapped_column(Integer, default=0)
    counting_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Etapa 3 NUNCA escreve no ERP. Valor fixo até existir homologação do inventário nativo.
    erp_application_status: Mapped[str] = mapped_column(String(20), default="not_applied")


class InventoryBaseline(IdMixin, Base):
    """Saldo do ERP lido quando a sessão (ou a recontagem) começou. quantity NULL = ERP sem registro."""

    __tablename__ = "inventory_baselines"
    __table_args__ = (UniqueConstraint("session_id", "round_no", "product_id", name="uq_baseline"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    erp_last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryRecountRequest(IdMixin, Base):
    __tablename__ = "inventory_recount_requests"
    __table_args__ = (UniqueConstraint("session_id", "round_no", "product_id", name="uq_recount"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryCount(IdMixin, Base):
    """Contagem append-only: quem, quando, quanto e em qual unidade. Nunca atualizada nem apagada."""

    __tablename__ = "inventory_counts"
    __table_args__ = (
        UniqueConstraint("session_id", "counted_by", "client_request_id", name="uq_count_idempotency"),
        Index("ix_count_lookup", "session_id", "product_id", "round_no"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"))
    round_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    counted_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    client_request_id: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class InventoryDiscrepancy(IdMixin, Base):
    __tablename__ = "inventory_discrepancies"

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    round_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default=DiscrepancyStatus.OPEN.value)
    baseline_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    counted_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)  # NULL se conflitante
    erp_current_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    accepted_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryAdjustmentProposal(IdMixin, Base):
    """Ajuste PROPOSTO (revisado por humano). Só irá ao ERP após homologação do inventário nativo."""

    __tablename__ = "inventory_adjustment_proposals"
    __table_args__ = (UniqueConstraint("session_id", "product_id", name="uq_proposal"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    baseline_quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    counted_quantity: Mapped[Decimal] = mapped_column(QTY)
    delta: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(Session, "before_flush")
def _forbid_mutation_of_append_only(session, flush_context, instances):
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, (InventoryCount, InventoryBaseline)):
            raise RuntimeError(f"{type(obj).__name__} é append-only: atualização/remoção proibida.")
