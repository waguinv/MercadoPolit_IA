"""Adaptador SOMENTE LEITURA da API pública do SysOrdem ("Meu ERP Online").

Contrato conferido no swagger.json (OpenAPI 3.0.1, v1); ver docs/SYSORDEM_CONTRATOS.md.
Autenticação: header ``Authorization: Authentication {token}`` (um token por empresa).
Limite: 20 req/min por token (429 ao exceder) -> este adaptador espera e repete só LEITURAS.

Segurança:
- Só emite requisições GET. Não existe caminho de escrita aqui.
- O token nunca aparece em mensagens de exceção nem em logs.
- Identificadores são validados como inteiros antes de entrarem em um caminho de URL.
- Nunca usa ``/api/consulta/sql``.
"""
import logging
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.db.base import utcnow as utcnow_aware
from app.integrations.erp_port import (
    ErpAuthError,
    ErpDataError,
    ErpError,
    ErpInvalidRequestError,
    ErpNotFoundError,
    ErpProduct,
    ErpRateLimitedError,
    ErpStockBalance,
    ErpStockLocation,
    ErpTimeoutError,
    ErpUnit,
)

MAX_PAGES = 5000
# Filho do logger do uvicorn, para sair no mesmo terminal. Registra só caminho, página, status e tempo.
log = logging.getLogger("uvicorn.error.sysordem")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _decimal(value: Any, field: str) -> Decimal:
    """O ERP envia double. Converte pela representação textual (35.5 -> Decimal('35.5'))."""
    if value is None or isinstance(value, bool):
        raise ErpDataError(f"Campo numérico ausente ou inválido: {field}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ErpDataError(f"Campo numérico inválido: {field}") from exc


def _int_path(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ErpInvalidRequestError(f"{what} inválido para o SysOrdem (esperado número inteiro).")
    return str(int(value))


class SysordemErpAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        page_size: int = 100,
        max_rate_limit_retries: int = 3,
        # O ERP repõe 20 requisições por minuto: as esperas (15+30+45 s) cobrem uma reposição inteira.
        default_backoff_seconds: float = 15.0,
        # Páginas de listagem são mais lentas que as leituras simples, e repetir uma leitura é seguro.
        list_timeout: float = 60.0,
        list_timeout_retries: int = 2,
        timeout_backoff_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._page_size = page_size
        self._list_timeout = list_timeout
        self._list_timeout_retries = list_timeout_retries
        self._timeout_backoff = timeout_backoff_seconds
        self._max_retries = max_rate_limit_retries
        self._backoff = default_backoff_seconds
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Authentication {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    # ---------------- HTTP ----------------
    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        timeout_retries: int = 0,
    ) -> Any:
        attempt = 0
        timeouts = 0
        where = f"{path} página {params['page']}" if params and "page" in params else path
        while True:
            started = time.monotonic()
            try:
                resp = self._client.get(path, params=params,
                                        timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout)
            except httpx.TimeoutException as exc:
                log.warning("SysOrdem %s: sem resposta em %.1f s", where, time.monotonic() - started)
                timeouts += 1
                if timeouts > timeout_retries:
                    raise ErpTimeoutError("O SysOrdem não respondeu a tempo.") from exc
                self._sleep(self._timeout_backoff * timeouts)
                continue
            except httpx.HTTPError as exc:
                raise ErpError("Falha de rede ao consultar o SysOrdem.") from exc
            code = resp.status_code
            log.info("SysOrdem %s: status %s em %.1f s", where, code, time.monotonic() - started)
            if code == 429:
                attempt += 1
                if attempt > self._max_retries:
                    raise ErpRateLimitedError("Limite de requisições do SysOrdem excedido.")
                self._sleep(self._retry_delay(resp, attempt))
                continue
            if code == 401 or code == 403:
                raise ErpAuthError("O SysOrdem rejeitou as credenciais da organização.")
            if code == 404:
                raise ErpNotFoundError("Recurso não encontrado no SysOrdem.")
            if code != 200:
                raise ErpError(f"O SysOrdem respondeu com status {code}.")
            try:
                return resp.json()
            except ValueError as exc:
                raise ErpDataError("Resposta do SysOrdem não é JSON válido.") from exc

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        # Retry-After não é documentado no swagger; honra se vier numérico.
        header = resp.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), 60.0)
        return self._backoff * attempt

    def _paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(path, {**(params or {}), "page": page, "limit": self._page_size},
                             timeout=self._list_timeout, timeout_retries=self._list_timeout_retries)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ErpDataError("Resposta paginada do SysOrdem em formato inesperado.")
            if any(not isinstance(i, dict) for i in data["items"]):
                raise ErpDataError("Item de resposta do SysOrdem em formato inesperado.")
            items.extend(data["items"])
            if not data.get("hasNext"):
                return items
            page += 1
            if page > MAX_PAGES:
                raise ErpDataError("Paginação do SysOrdem não terminou (limite de segurança).")

    # ---------------- Porta ----------------
    def check_connection(self) -> None:
        """Leitura barata para validar o token (dados da empresa do token)."""
        self._get("/api/empresa/v1")

    def list_units(self) -> list[ErpUnit]:
        units = []
        for e in self._paged("/api/embalagem/v1"):
            code = _text(e.get("sigla"))
            if code is None or e.get("ativo") == "N":
                continue
            units.append(ErpUnit(code, _text(e.get("descricao")) or code, e.get("fracionado") == "S"))
        return units

    def list_products(self) -> list[ErpProduct]:
        products = []
        for m in self._paged("/api/mercadoria/v1"):
            ext = m.get("codigoMercadoriaVariacao")
            if not isinstance(ext, int) or isinstance(ext, bool) or ext <= 0:
                raise ErpDataError("Mercadoria do SysOrdem sem código de variação válido.")
            products.append(ErpProduct(
                external_id=str(ext),
                name=_text(m.get("descricao")) or f"Mercadoria {ext}",
                unit_code=_text(m.get("embalagem")),
                sku=_text(m.get("referenciaVariacao")) or _text(m.get("referenciaMercadoria")),
                barcode=_text(m.get("codigoBarras")),
                active=m.get("ativo") == "S",
            ))
        return products

    def list_stock_locations(self) -> list[ErpStockLocation]:
        out = []
        for loc in self._paged("/api/local-estoque/v1"):
            code = loc.get("codigo")
            if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
                raise ErpDataError("Local de estoque do SysOrdem sem código válido.")
            out.append(ErpStockLocation(
                code=str(code),
                name=_text(loc.get("descricao")) or f"Local {code}",
                reference=_text(loc.get("referencia")),
                active=loc.get("ativo") != "N",
            ))
        return out

    def get_stock_balance(
        self, product_external_id: str, location_code: str | None = None
    ) -> ErpStockBalance:
        if location_code is None:
            raise ErpInvalidRequestError("O SysOrdem exige um local de estoque para consultar saldo.")
        var_id = _int_path(product_external_id, "Código da mercadoria")
        loc_id = _int_path(location_code, "Código do local de estoque")
        data = self._get(f"/api/mercadoria/{var_id}/local-estoque/{loc_id}/estoque/v1")
        if not isinstance(data, dict):
            raise ErpDataError("Resposta de estoque do SysOrdem em formato inesperado.")
        reserved = data.get("qtdSaldoReserva")
        return ErpStockBalance(
            product_external_id=product_external_id,
            location_code=location_code,
            quantity=_decimal(data.get("qtdSaldo"), "qtdSaldo"),
            as_of=utcnow_aware(),
            reserved_quantity=None if reserved is None else _decimal(reserved, "qtdSaldoReserva"),
            last_changed_at=self._parse_dt(data.get("dataHora")),
        )

    def list_location_balances(self, location_code: str) -> list[ErpStockBalance]:
        """Uma leitura paginada do local inteiro (evita 1 chamada por produto sob o rate limit)."""
        loc_id = _int_path(location_code, "Código do local de estoque")
        now = utcnow_aware()
        out = []
        for row in self._paged(f"/api/local-estoque/{loc_id}/estoques/v1"):
            var = row.get("idMercadoriaVariacao")
            if not isinstance(var, int) or isinstance(var, bool) or var <= 0:
                raise ErpDataError("Estoque do SysOrdem sem código de variação válido.")
            reserved = row.get("qtdSaldoReserva")
            out.append(ErpStockBalance(
                product_external_id=str(var),
                location_code=location_code,
                quantity=_decimal(row.get("qtdSaldo"), "qtdSaldo"),
                as_of=now,
                reserved_quantity=None if reserved is None else _decimal(reserved, "qtdSaldoReserva"),
                last_changed_at=self._parse_dt(row.get("dataHora")),
            ))
        return out

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
