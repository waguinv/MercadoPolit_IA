from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Qty = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=3)]


class SessionCreate(BaseModel):
    store_id: UUID
    location_id: UUID
    name: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    store_id: UUID
    location_id: UUID
    name: str
    status: str
    current_round: int
    created_by: UUID
    baseline_unmapped_count: int
    counting_closed_at: datetime | None
    finalized_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    erp_application_status: str
    created_at: datetime


class SessionProgress(SessionOut):
    baseline_items: int
    counts_total: int
    products_counted: int
    discrepancies_open: int


class CountCreate(BaseModel):
    product_id: UUID
    quantity: Qty
    client_request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    note: str | None = Field(default=None, max_length=500)


class CountOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    session_id: UUID
    round_no: int
    product_id: UUID
    unit_id: UUID
    quantity: Decimal
    counted_by: UUID
    counted_at: datetime
    client_request_id: str
    note: str | None


class CloseCountingOut(BaseModel):
    products_checked: int
    clean: int
    discrepancies_created: int
    baseline_items_not_counted: int


class DiscrepancyOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    session_id: UUID
    product_id: UUID
    round_no: int
    kind: str
    status: str
    baseline_quantity: Decimal | None
    counted_quantity: Decimal | None
    erp_current_quantity: Decimal | None
    details: dict[str, Any] | None
    decision: str | None
    accepted_quantity: Decimal | None
    resolved_by: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None


class ResolveIn(BaseModel):
    decision: Literal["accept_count", "keep_erp"]
    accepted_quantity: Qty | None = None
    note: str = Field(min_length=5, max_length=1000)


class RecountIn(BaseModel):
    discrepancy_ids: list[UUID] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=5, max_length=500)


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class ProposalOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    session_id: UUID
    product_id: UUID
    unit_id: UUID
    baseline_quantity: Decimal | None
    counted_quantity: Decimal
    delta: Decimal | None
    status: str
