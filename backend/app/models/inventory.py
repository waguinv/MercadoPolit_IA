import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow


class SessionStatus(enum.StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    CLOSED = "closed"  # contagem aprovada; NADA foi enviado ao ERP
    CANCELLED = "cancelled"


class ItemStatus(enum.StrEnum):
    COUNTED = "counted"
    NEEDS_REVIEW = "needs_review"
    RECOUNT_REQUESTED = "recount_requested"
    RESOLVED = "resolved"


class InventorySession(IdMixin, TimestampMixin, Base):
    """Sessão de contagem de um local (opcionalmente de um setor) de uma loja."""

    __tablename__ = "inventory_sessions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"), index=True)
    # Setor nulo = o local inteiro.
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=20, validate_strings=True),
        default=SessionStatus.OPEN, index=True,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InventoryAssignment(IdMixin, TimestampMixin, Base):
    """Atribuição opcional: se a sessão tiver alguma, só os atribuídos (e gestores) contam."""

    __tablename__ = "inventory_assignments"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_inv_assignment_session_user"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))


class InventoryItem(IdMixin, TimestampMixin, Base):
    """Estado consolidado de um produto na sessão. Só existe se o produto foi contado:
    produto não contado NÃO significa saldo zero."""

    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("session_id", "product_id", name="uq_inv_item_session_product"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    # Unidade do produto quando o item nasceu; contagens são comparadas a ela, nunca convertidas.
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, native_enum=False, length=20, validate_strings=True),
        default=ItemStatus.COUNTED, index=True,
    )
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    review_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Sempre o valor de UMA contagem escolhida; contagens nunca são somadas.
    accepted_count_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class InventoryCount(IdMixin, Base):
    """Uma contagem física. Append-only: correção é uma nova contagem/rodada."""

    __tablename__ = "inventory_counts"
    __table_args__ = (
        UniqueConstraint("session_id", "counted_by_id", "client_key", name="uq_inv_count_client_key"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_items.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    # "unit_mismatch" ou "fraction_not_allowed": a contagem fica salva, mas não pode ser aceita.
    unit_issue: Mapped[str | None] = mapped_column(String(30), nullable=True)
    counted_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Gerada pelo aparelho: reenvio após falha de rede não vira contagem duplicada.
    client_key: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class InventoryRecount(IdMixin, Base):
    """Pedido de reconferência: abre uma nova rodada de contagem do item."""

    __tablename__ = "inventory_recounts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_items.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryErpCheck(IdMixin, Base):
    """Leitura do saldo no ERP durante a revisão. Só leitura: nada é gravado no ERP."""

    __tablename__ = "inventory_erp_checks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_sessions.id"), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory_items.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    erp_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    erp_reserved_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Como veio do ERP; o fuso ainda não foi confirmado em homologação.
    erp_last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    checked_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))


_APPEND_ONLY = (InventoryCount, InventoryRecount, InventoryErpCheck)


@event.listens_for(Session, "before_flush")
def _forbid_history_mutation(session, flush_context, instances):
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, _APPEND_ONLY):
            raise RuntimeError(f"{type(obj).__name__} é append-only: atualização/remoção proibida.")
