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
    products_skipped: int


class StockBalanceOut(BaseModel):
    product_id: UUID
    location_id: UUID | None
    quantity: Decimal
    unit_code: str  # unidade espelhada do ERP para o produto
    reserved_quantity: Decimal | None = None
    as_of: datetime  # quando lemos o saldo (UTC)
    last_changed_at: datetime | None = None  # última alteração no ERP (fuso não informado)
    source: str = "erp"


class ErpStockLocationOut(BaseModel):
    code: str
    name: str
    reference: str | None
    active: bool


class ErpConnectionIn(BaseModel):
    token: str = Field(min_length=8, max_length=512)


class ErpConnectionOut(BaseModel):
    configured: bool
    is_active: bool = False
    last_verified_at: datetime | None = None
