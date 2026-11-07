from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
RequestId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")]
Mode = Literal["self_service", "centralized"]
Name = Annotated[str, Field(min_length=2, max_length=100)]


def _strip(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    return v or None


class ConsumptionCreate(BaseModel):
    client_request_id: RequestId
    location_id: UUID
    department_id: UUID
    purpose_id: UUID
    product_id: UUID
    quantity: Qty
    consumer_user_id: UUID | None = None
    consumer_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    _clean = field_validator("consumer_name", "note")(_strip)


class ConsumptionCorrection(BaseModel):
    """Correção pedida pelo aprovador: só quem registrou altera, e volta para aprovação."""
    department_id: UUID
    purpose_id: UUID
    product_id: UUID
    quantity: Qty
    note: str | None = Field(default=None, max_length=500)

    _clean = field_validator("note")(_strip)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    _clean = field_validator("reason")(lambda v: v.strip())


class ConsumptionOut(BaseModel):
    id: UUID
    store_id: UUID
    location_id: UUID
    department_id: UUID
    purpose_id: UUID
    product_id: UUID
    unit_id: UUID
    quantity: Decimal
    consumer_user_id: UUID | None
    consumer_name: str
    registered_by: UUID
    status: str
    note: str | None
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    erp_status: str
    created_at: datetime
    updated_at: datetime
    # Nomes para a tela, resolvidos no servidor.
    product_name: str
    unit_code: str
    department_name: str
    purpose_name: str
    store_name: str
    location_name: str
    registered_by_name: str
    decided_by_name: str | None


class DepartmentIn(BaseModel):
    name: Name
    mode: Mode = "centralized"

    _clean = field_validator("name")(lambda v: v.strip())


class DepartmentUpdate(BaseModel):
    name: Name | None = None
    mode: Mode | None = None
    is_active: bool | None = None


class PurposeIn(BaseModel):
    name: Name

    _clean = field_validator("name")(lambda v: v.strip())


class PurposeUpdate(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class DepartmentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    name: str
    mode: str
    is_active: bool


class PurposeOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    name: str
    is_active: bool


class ConsumerOut(BaseModel):
    id: UUID
    name: str


class ConsumptionConfigOut(BaseModel):
    segregation: bool
    departments: list[DepartmentOut]
    purposes: list[PurposeOut]


class SettingsIn(BaseModel):
    segregation: bool
