import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Qty = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=3)]
RequestId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")]
Reason = Literal["damaged", "incorrect_item", "expired", "quality", "other"]


def access_key_is_valid(key: str) -> bool:
    """Chave de acesso NF-e/NFC-e: 44 dígitos, DV módulo 11 (pesos 2..9 da direita para a esquerda)."""
    if not re.fullmatch(r"\d{44}", key):
        return False
    total, weight = 0, 2
    for digit in reversed(key[:43]):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    return int(key[43]) == (0 if rest in (0, 1) else 11 - rest)


class ReceivingCreate(BaseModel):
    client_request_id: RequestId
    store_id: UUID
    location_id: UUID
    supplier_name: str = Field(min_length=1, max_length=200)
    supplier_document: str | None = None
    document_number: str | None = Field(default=None, min_length=1, max_length=20)
    document_series: str = Field(default="", max_length=10)
    access_key: str | None = None
    received_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("supplier_document")
    @classmethod
    def _doc(cls, v):
        if v is None:
            return v
        digits = re.sub(r"[.\-/\s]", "", v)
        if not re.fullmatch(r"\d{11}|\d{14}", digits):
            raise ValueError("Documento do fornecedor deve ter 11 (CPF) ou 14 (CNPJ) dígitos.")
        return digits

    @field_validator("access_key")
    @classmethod
    def _key(cls, v):
        if v is None:
            return v
        key = re.sub(r"\s", "", v)
        if not access_key_is_valid(key):
            raise ValueError("Chave de acesso inválida (44 dígitos com dígito verificador correto).")
        return key


class ReceivingOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    store_id: UUID
    location_id: UUID
    supplier_name: str
    supplier_document: str | None
    document_number: str | None
    document_series: str
    access_key: str | None
    received_at: datetime
    status: str
    is_partial: bool | None
    note: str | None
    created_by: UUID
    finalized_by: UUID | None
    finalized_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    erp_application_status: str
    created_at: datetime


class ItemCreate(BaseModel):
    client_request_id: RequestId
    product_id: UUID
    expected_quantity: Qty | None = None
    received_quantity: Qty
    accepted_quantity: Qty | None = None  # padrão: recebido - rejeitado
    rejected_quantity: Qty = Decimal("0")
    rejection_reason: Reason | None = None
    lot: str = Field(default="", max_length=64)
    expiry_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


class ItemUpdate(BaseModel):
    expected_quantity: Qty | None = None
    received_quantity: Qty | None = None
    accepted_quantity: Qty | None = None
    rejected_quantity: Qty | None = None
    rejection_reason: Reason | None = None
    note: str | None = Field(default=None, max_length=500)


class ItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    receiving_id: UUID
    product_id: UUID
    product_name: str = ""
    unit_id: UUID
    expected_quantity: Decimal | None
    received_quantity: Decimal
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    rejection_reason: str | None
    lot: str
    expiry_date: date | None
    note: str | None
    shortage_quantity: Decimal | None = None  # falta (esperado > recebido)
    excess_quantity: Decimal | None = None    # excesso (recebido > esperado)


class ReceivingDetail(ReceivingOut):
    received_on: date  # dia do recebimento no fuso da loja: é contra ele que a validade é conferida
    items: list[ItemOut]


class FinalizeIn(BaseModel):
    partial: bool = False  # True: faltas ficam PENDENTES; False: faltas são definitivas
    note: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
