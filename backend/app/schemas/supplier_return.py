from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.consumption import Qty, RequestId

Reason = Literal["damaged", "expired", "near_expiry", "quality", "incorrect_item", "other"]
Modality = Literal["exchange", "credit", "no_replacement"]


def _strip(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    return v or None


class ReturnData(BaseModel):
    product_id: UUID
    quantity: Qty
    reason: Reason
    modality: Modality
    supplier_name: str | None = Field(default=None, max_length=200)
    receiving_id: UUID | None = None
    origin_document: str | None = Field(default=None, max_length=20)
    lot: str | None = Field(default=None, max_length=64)
    expiry_date: date | None = None
    note: str | None = Field(default=None, max_length=500)
    expected_pickup_date: date | None = None
    expected_replacement_date: date | None = None

    _clean = field_validator("supplier_name", "origin_document", "lot", "note")(_strip)


class ReturnCreate(ReturnData):
    client_request_id: RequestId
    location_id: UUID


class PickupIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)

    _clean = field_validator("note")(_strip)


class ReplacementIn(BaseModel):
    receiving_id: UUID
    quantity: Qty


class ReplacementOut(BaseModel):
    id: UUID
    receiving_id: UUID
    document_number: str | None
    quantity: Decimal
    registered_by_name: str
    created_at: datetime


class ReturnOut(BaseModel):
    id: UUID
    store_id: UUID
    location_id: UUID
    product_id: UUID
    unit_id: UUID
    quantity: Decimal
    reason: str
    modality: str
    supplier_name: str
    supplier_document: str | None
    receiving_id: UUID | None
    origin_document: str | None
    lot: str | None
    expiry_date: date | None
    note: str | None
    expected_pickup_date: date | None
    expected_replacement_date: date | None
    registered_by: UUID
    status: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    picked_up_at: datetime | None
    pickup_note: str | None
    completed_at: datetime | None
    completion_note: str | None
    erp_status: str
    created_at: datetime
    updated_at: datetime
    product_name: str
    unit_code: str
    store_name: str
    location_name: str
    registered_by_name: str
    decided_by_name: str | None
    picked_up_by_name: str | None
    completed_by_name: str | None
    replaced_quantity: Decimal
    replacements: list[ReplacementOut]
