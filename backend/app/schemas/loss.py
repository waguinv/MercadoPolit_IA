from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.consumption import Qty, RequestId

Reason = Literal[
    "damaged_package", "spoilage", "expired", "breakage", "contamination", "operational", "other",
]


def _strip(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    return v or None


class LossData(BaseModel):
    product_id: UUID
    quantity: Qty
    reason: Reason
    lot: str | None = Field(default=None, max_length=64)
    expiry_date: date | None = None
    note: str | None = Field(default=None, max_length=500)

    _clean = field_validator("lot", "note")(_strip)


class LossCreate(LossData):
    client_request_id: RequestId
    location_id: UUID


class LossOut(BaseModel):
    id: UUID
    store_id: UUID
    location_id: UUID
    product_id: UUID
    unit_id: UUID
    quantity: Decimal
    reason: str
    lot: str | None
    expiry_date: date | None
    note: str | None
    registered_by: UUID
    status: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    erp_status: str
    created_at: datetime
    updated_at: datetime
    product_name: str
    unit_code: str
    store_name: str
    location_name: str
    registered_by_name: str
    decided_by_name: str | None
