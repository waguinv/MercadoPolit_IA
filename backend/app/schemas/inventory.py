from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.inventory import ItemStatus, SessionStatus


class SessionCreate(BaseModel):
    location_id: UUID
    name: str = Field(min_length=1, max_length=200)
    sector: str | None = Field(default=None, max_length=100)


class SessionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    store_id: UUID
    location_id: UUID
    sector: str | None
    name: str
    status: SessionStatus
    created_by_id: UUID
    created_at: datetime
    closed_by_id: UUID | None
    closed_at: datetime | None
    cancel_reason: str | None


class SessionDetailOut(SessionOut):
    items_total: int
    items_by_status: dict[str, int]


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class AssignmentIn(BaseModel):
    user_id: UUID


class AssignmentOut(BaseModel):
    model_config = {"from_attributes": True}

    user_id: UUID
    created_at: datetime


class CountIn(BaseModel):
    product_id: UUID
    unit_id: UUID  # unidade em que a pessoa contou; nunca é convertida
    quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=4, allow_inf_nan=False)
    client_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    note: str | None = Field(default=None, max_length=1000)


class CountOut(BaseModel):
    """Resposta a quem conta: sem saldo do ERP nem contagens de outras pessoas (contagem cega)."""

    id: UUID
    product_id: UUID
    round: int
    quantity: Decimal
    unit_id: UUID
    unit_issue: str | None
    counted_at: datetime
    client_key: str
    note: str | None


class PendingRecountOut(BaseModel):
    item_id: UUID
    product_id: UUID
    product_name: str
    round: int
    reason: str
    assigned_user_id: UUID | None


class ItemOut(BaseModel):
    id: UUID
    product_id: UUID
    product_name: str
    unit_id: UUID
    status: ItemStatus
    current_round: int
    review_reasons: list[str]
    accepted_count_id: UUID | None
    accepted_quantity: Decimal | None
    resolved_by_id: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None


class ReviewCountOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    round: int
    quantity: Decimal
    unit_id: UUID
    unit_issue: str | None
    counted_by_id: UUID
    counted_at: datetime
    note: str | None


class RecountOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    item_id: UUID
    round: int
    reason: str
    assigned_user_id: UUID | None
    requested_by_id: UUID
    requested_at: datetime


class ErpCheckOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    round: int
    erp_quantity: Decimal
    erp_reserved_quantity: Decimal | None
    read_at: datetime
    erp_last_changed_at: datetime | None  # como veio do ERP; fuso não confirmado
    checked_by_id: UUID


class ItemDetailOut(ItemOut):
    counts: list[ReviewCountOut]
    recounts: list[RecountOut]
    erp_checks: list[ErpCheckOut]


class ErpCheckResultOut(BaseModel):
    check: ErpCheckOut
    # Quantidade aceita antes desta leitura menos o saldo do ERP; nada é ajustado no ERP.
    difference: Decimal | None
    item_status: ItemStatus
    review_reasons: list[str]


class RecountIn(BaseModel):
    item_id: UUID
    reason: str = Field(min_length=3, max_length=1000)
    assigned_user_id: UUID | None = None


class ResolveIn(BaseModel):
    count_id: UUID  # a decisão escolhe uma contagem real; não aceita quantidade digitada
    justification: str = Field(min_length=3, max_length=1000)
