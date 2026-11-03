"""Testes do adaptador SysOrdem com transporte HTTP simulado: NENHUMA chamada de rede real."""
from decimal import Decimal

import httpx
import pytest

from app.integrations.erp_port import (
    ErpAuthError,
    ErpDataError,
    ErpError,
    ErpInvalidRequestError,
    ErpNotFoundError,
    ErpRateLimitedError,
    ErpTimeoutError,
)
from app.integrations.sysordem.adapter import SysordemErpAdapter

TOKEN = "tok-super-secreto-123"
BASE = "https://api.meuerponline.com.br/publica"


def make(handler, **kw):
    sleeps: list[float] = []
    calls: list[httpx.Request] = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    a = SysordemErpAdapter(BASE, TOKEN, transport=httpx.MockTransport(wrapped),
                           sleep=sleeps.append, **kw)
    a.sleeps, a.calls = sleeps, calls
    return a


def page(items, has_next=False):
    return httpx.Response(200, json={"page": 1, "limit": 100, "total": len(items),
                                     "totalPages": 1, "hasNext": has_next, "items": items})


def test_auth_header_url_and_only_get_requests():
    a = make(lambda r: page([]))
    a.list_units()
    a.list_products()
    a.list_stock_locations()
    assert all(c.method == "GET" for c in a.calls)
    assert all(c.headers["Authorization"] == f"Authentication {TOKEN}" for c in a.calls)
    assert a.calls[0].url.path == "/publica/api/embalagem/v1"
    assert a.calls[1].url.path == "/publica/api/mercadoria/v1"
    assert a.calls[2].url.path == "/publica/api/local-estoque/v1"


def test_adapter_exposes_no_write_methods():
    a = make(lambda r: page([]))
    public = {n for n in dir(a) if not n.startswith("_") and callable(getattr(a, n))}
    assert public == {"check_connection", "list_units", "list_products", "list_stock_locations",
                      "get_stock_balance", "close"}


def test_check_connection_calls_empresa():
    a = make(lambda r: httpx.Response(200, json={"razaoSocial": "X"}))
    a.check_connection()
    assert a.calls[0].url.path == "/publica/api/empresa/v1"


def test_pagination_follows_has_next_and_sends_page_and_limit():
    def handler(r):
        p = int(r.url.params["page"])
        assert r.url.params["limit"] == "100"
        return page([{"sigla": f"U{p}", "descricao": f"Un {p}", "fracionado": "N", "ativo": "S"}],
                    has_next=p < 3)

    a = make(handler)
    assert [u.code for u in a.list_units()] == ["U1", "U2", "U3"]
    assert len(a.calls) == 3


def test_units_mapping_fracionado_and_inactive_filtered():
    a = make(lambda r: page([
        {"sigla": "KG", "descricao": "Quilo", "fracionado": "S", "ativo": "S"},
        {"sigla": "UN", "descricao": "Unidade", "fracionado": "N", "ativo": "S"},
        {"sigla": "OLD", "descricao": "Antiga", "fracionado": "N", "ativo": "N"},
        {"sigla": None, "descricao": "sem sigla"},
    ]))
    units = {u.code: u for u in a.list_units()}
    assert set(units) == {"KG", "UN"} and units["KG"].allows_fraction and not units["UN"].allows_fraction


def test_products_mapping():
    a = make(lambda r: page([
        {"codigoMercadoriaVariacao": 501, "codigoMercadoria": 50, "descricao": " Arroz 5kg ",
         "referenciaVariacao": "ARZ5", "referenciaMercadoria": "REF", "codigoBarras": "789",
         "embalagem": "UN", "ativo": "S"},
        {"codigoMercadoriaVariacao": 502, "descricao": None, "referenciaVariacao": "",
         "referenciaMercadoria": "R2", "codigoBarras": " ", "embalagem": None, "ativo": "N"},
    ]))
    p1, p2 = a.list_products()
    assert (p1.external_id, p1.name, p1.sku, p1.barcode, p1.unit_code, p1.active) == (
        "501", "Arroz 5kg", "ARZ5", "789", "UN", True)
    assert (p2.name, p2.sku, p2.barcode, p2.unit_code, p2.active) == (
        "Mercadoria 502", "R2", None, None, False)


@pytest.mark.parametrize("bad", [{}, {"codigoMercadoriaVariacao": 0}, {"codigoMercadoriaVariacao": "7"},
                                 {"codigoMercadoriaVariacao": True}])
def test_product_without_valid_variation_code_is_data_error(bad):
    a = make(lambda r: page([bad]))
    with pytest.raises(ErpDataError):
        a.list_products()


def test_stock_locations_mapping():
    a = make(lambda r: page([{"codigo": 3, "descricao": "Depósito", "referencia": "DEP", "ativo": "S"}]))
    loc = a.list_stock_locations()[0]
    assert (loc.code, loc.name, loc.reference, loc.active) == ("3", "Depósito", "DEP", True)


def test_balance_uses_decimal_and_path_and_parses_fields():
    a = make(lambda r: httpx.Response(200, json={
        "idLocalEstoque": 3, "idMercadoriaVariacao": 501, "qtdSaldo": 35.5,
        "qtdSaldoReserva": 0.1, "dataHora": "2026-11-03T10:15:00"}))
    b = a.get_stock_balance("501", "3")
    assert a.calls[0].url.path == "/publica/api/mercadoria/501/local-estoque/3/estoque/v1"
    assert b.quantity == Decimal("35.5") and b.reserved_quantity == Decimal("0.1")
    assert b.last_changed_at.year == 2026 and b.as_of.tzinfo is not None


def test_balance_requires_location_and_rejects_non_numeric_ids():
    a = make(lambda r: httpx.Response(200, json={"qtdSaldo": 1}))
    for args in [("501", None), ("../../x", "3"), ("501", "3/../../sql"), ("5 01", "3"), ("-1", "3")]:
        with pytest.raises(ErpInvalidRequestError):
            a.get_stock_balance(*args)
    assert a.calls == []  # nada foi enviado ao ERP


@pytest.mark.parametrize("payload", [{}, {"qtdSaldo": None}, {"qtdSaldo": "abc"}, {"qtdSaldo": True}])
def test_balance_missing_or_invalid_quantity_is_data_error_never_zero(payload):
    a = make(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(ErpDataError):
        a.get_stock_balance("501", "3")


def test_http_status_mapping():
    cases = {401: ErpAuthError, 403: ErpAuthError, 404: ErpNotFoundError, 500: ErpError, 502: ErpError}
    for status, exc in cases.items():
        a = make(lambda r, s=status: httpx.Response(s, text="x"))
        with pytest.raises(exc):
            a.list_units()


def test_rate_limit_retries_then_succeeds_honoring_retry_after():
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
        return page([])

    a = make(handler)
    assert a.list_units() == []
    assert a.sleeps == [7.0, 7.0] and state["n"] == 3


def test_rate_limit_without_header_uses_growing_backoff_and_gives_up():
    a = make(lambda r: httpx.Response(429), max_rate_limit_retries=2, default_backoff_seconds=3)
    with pytest.raises(ErpRateLimitedError):
        a.list_units()
    assert a.sleeps == [3.0, 6.0] and len(a.calls) == 3


def test_timeout_and_network_errors_mapped_without_leaking_token():
    def boom(exc):
        def h(r):
            raise exc
        return h

    with pytest.raises(ErpTimeoutError) as t:
        make(boom(httpx.ReadTimeout("t"))).list_units()
    with pytest.raises(ErpError) as n:
        make(boom(httpx.ConnectError("dns"))).list_units()
    for e in (t.value, n.value):
        assert TOKEN not in str(e) and TOKEN not in repr(e)


def test_malformed_responses_are_data_errors():
    for resp in (httpx.Response(200, text="<html>"), httpx.Response(200, json=[1, 2]),
                 httpx.Response(200, json={"items": "x"}), httpx.Response(200, json={"items": [1]})):
        with pytest.raises(ErpDataError):
            make(lambda r, x=resp: x).list_units()


def test_redirects_are_not_followed():
    a = make(lambda r: httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(ErpError):
        a.list_units()
    assert len(a.calls) == 1
