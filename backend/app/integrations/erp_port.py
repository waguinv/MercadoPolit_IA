"""Porta (contrato) do adaptador de ERP.

Regras:
- Nenhum módulo fora de ``app/integrations`` fala HTTP com o ERP.
- A porta é SOMENTE LEITURA. Métodos de escrita só serão adicionados após confirmação em
  Swagger + homologação (ver docs/SYSORDEM_CONTRATOS.md e a seção 6 da arquitetura).
- ``list_units`` e ``list_products`` devem devolver o conjunto COMPLETO ou levantar ``ErpError``:
  a sincronização desativa o que não vier na lista, então uma lista parcial seria perigosa.
- Leituras podem ser repetidas com segurança; escritas (futuras) NUNCA terão retry automático.
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
    """O ERP respondeu, mas os dados são inconsistentes (duplicados, formato inesperado...)."""


class ErpNotAvailableError(ErpError):
    """Adaptador não habilitado/configurado para esta organização."""


class ErpAuthError(ErpError):
    """O ERP rejeitou as credenciais (token inválido, revogado ou sem permissão)."""


class ErpRateLimitedError(ErpError):
    """Limite de requisições do ERP excedido mesmo após novas tentativas."""


class ErpInvalidRequestError(ErpError):
    """Pedido inválido para este ERP (ex.: local de estoque obrigatório)."""


@dataclass(frozen=True)
class ErpUnit:
    code: str
    name: str
    allows_fraction: bool = False


@dataclass(frozen=True)
class ErpProduct:
    external_id: str
    name: str
    unit_code: str | None
    sku: str | None = None
    barcode: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ErpStockLocation:
    code: str
    name: str
    reference: str | None = None
    active: bool = True


@dataclass(frozen=True)
class ErpStockBalance:
    """Saldo lido do ERP. A unidade vem do espelho local do produto, não desta resposta."""

    product_external_id: str
    location_code: str | None
    quantity: Decimal
    as_of: datetime  # quando LEMOS o saldo (UTC)
    reserved_quantity: Decimal | None = None
    last_changed_at: datetime | None = None  # última alteração no ERP; fuso não informado pelo ERP


class ErpAdapter(Protocol):
    def check_connection(self) -> None: ...

    def list_units(self) -> list[ErpUnit]: ...

    def list_products(self) -> list[ErpProduct]: ...

    def list_stock_locations(self) -> list[ErpStockLocation]: ...

    def get_stock_balance(
        self, product_external_id: str, location_code: str | None = None
    ) -> ErpStockBalance: ...

    def list_location_balances(self, location_code: str) -> list[ErpStockBalance]:
        """Saldos de TODOS os produtos com registro de estoque no local (conjunto completo ou erro)."""
        ...
