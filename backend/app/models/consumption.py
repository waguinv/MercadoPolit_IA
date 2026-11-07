"""Consumo interno (Central de Movimentações, Etapa 5.1). Nenhuma escrita no ERP nesta etapa."""
import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

QTY = Numeric(18, 3)


class DepartmentMode(enum.StrEnum):
    SELF_SERVICE = "self_service"   # o próprio funcionário registra o que consumiu
    CENTRALIZED = "centralized"     # só o responsável (gerente/admin) registra


class ConsumptionStatus(enum.StrEnum):
    PENDING_APPROVAL = "pending_approval"
    NEEDS_CORRECTION = "needs_correction"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ConsumptionDepartment(IdMixin, TimestampMixin, Base):
    __tablename__ = "consumption_departments"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_consumption_department_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20), default=DepartmentMode.CENTRALIZED.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ConsumptionPurpose(IdMixin, TimestampMixin, Base):
    __tablename__ = "consumption_purposes"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_consumption_purpose_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InternalConsumption(IdMixin, TimestampMixin, Base):
    __tablename__ = "internal_consumptions"
    __table_args__ = (
        UniqueConstraint("organization_id", "registered_by", "client_request_id", name="uq_consumption_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stores.id"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_locations.id"))
    department_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("consumption_departments.id"))
    purpose_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("consumption_purposes.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_references.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("unit_references.id"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    # Consumidor, registrador e aprovador são registrados separadamente.
    consumer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    consumer_name: Mapped[str] = mapped_column(String(200))
    registered_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(
        String(20), default=ConsumptionStatus.PENDING_APPROVAL.value, index=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Motivo da rejeição, do pedido de correção ou do cancelamento.
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(64))
    # Etapa 5.1 NUNCA escreve no ERP: não há contrato homologado para consumo interno.
    erp_status: Mapped[str] = mapped_column(String(20), default="not_sent")
