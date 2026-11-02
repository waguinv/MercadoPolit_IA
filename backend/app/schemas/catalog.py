from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class UnitOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    code: str
    name: str
    allows_fraction: bool
    is_active: bool


class ProductOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    external_id: str
    sku: str | None
    barcode: str | None
    name: str
    unit_id: UUID
    is_active: bool
    last_synced_at: datetime


class LocationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    external_code: str | None = Field(default=None, max_length=100)


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    external_code: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class LocationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    store_id: UUID
    code: str
    name: str
    external_code: str | None
    is_active: bool


class SyncResultOut(BaseModel):
    units_created: int
    units_updated: int
    units_deactivated: int
    products_created: int
    products_updated: int
    products_deactivated: int
    products_unchanged: int


class StockBalanceOut(BaseModel):
    product_id: UUID
    location_id: UUID | None
    quantity: Decimal
    unit_code: str
    as_of: datetime
    source: str = "erp"
