"""Porta (contrato) do adaptador de ERP.

Regras:
- Nenhum módulo fora de ``app/integrations`` fala HTTP com o ERP.
- Nesta etapa a porta é SOMENTE LEITURA. Métodos de escrita só serão adicionados após
  confirmação em Swagger + homologação (ver docs/MERCADOPILOT_AI_ARQUITETURA.md, seção 6).
- ``list_units`` e ``list_products`` devem devolver o conjunto COMPLETO ou levantar ``ErpError``:
  a sincronização desativa o que não vier na lista, então uma lista parcial seria perigosa.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class ErpError(Exception):
    """Falha ao falar com o ERP (leitura: pode ser repetida com segurança)."""


class ErpTimeoutError(ErpError):
    pass


class ErpNotFoundError(ErpError):
    pass


class ErpDataError(ErpError):
    """O ERP respondeu, mas os dados são inconsistentes (duplicados, unidade desconhecida...)."""


class ErpNotAvailableError(ErpError):
    """Adaptador não implementado/habilitado neste ambiente."""


@dataclass(frozen=True)
class ErpUnit:
    code: str
    name: str
    allows_fraction: bool = False


@dataclass(frozen=True)
class ErpProduct:
    external_id: str
    name: str
    unit_code: str
    sku: str | None = None
    barcode: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ErpStockBalance:
    product_external_id: str
    location_code: str | None
    quantity: Decimal
    unit_code: str
    as_of: datetime


class ErpAdapter(Protocol):
    def list_units(self) -> list[ErpUnit]: ...

    def list_products(self) -> list[ErpProduct]: ...

    def get_stock_balance(
        self, product_external_id: str, location_code: str | None = None
    ) -> ErpStockBalance: ...
