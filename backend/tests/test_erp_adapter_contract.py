"""Contrato que TODO adaptador de ERP deve cumprir. O futuro adaptador SysOrdem (contra
homologação) deve ser adicionado à parametrização `adapter_factory`."""
from decimal import Decimal

import pytest

from app.integrations.erp_port import ErpNotFoundError
from app.integrations.mock_erp import MockErpAdapter


@pytest.fixture(params=[MockErpAdapter])
def adapter(request):
    return request.param()


def test_units_are_unique_and_complete(adapter):
    codes = [u.code for u in adapter.list_units()]
    assert codes and len(codes) == len(set(codes))


def test_products_reference_known_units_and_unique_ids(adapter):
    units = {u.code for u in adapter.list_units()}
    products = adapter.list_products()
    assert len({p.external_id for p in products}) == len(products)
    assert all(p.unit_code in units for p in products)


def test_balance_is_decimal_and_timestamped(adapter):
    p = adapter.list_products()[0]
    bal = adapter.get_stock_balance(p.external_id)
    assert isinstance(bal.quantity, Decimal)
    assert bal.as_of.tzinfo is not None


def test_unknown_product_raises_not_found(adapter):
    with pytest.raises(ErpNotFoundError):
        adapter.get_stock_balance("nao-existe")


def test_adapter_has_no_write_methods(adapter):
    # Etapa 2 é somente leitura: nada de métodos de escrita na porta.
    public = {n for n in dir(adapter) if not n.startswith("_") and callable(getattr(adapter, n))}
    assert public == {"check_connection", "list_units", "list_products",
                      "list_stock_locations", "get_stock_balance"}


def test_stock_locations_have_unique_codes(adapter):
    codes = [loc.code for loc in adapter.list_stock_locations()]
    assert codes and len(codes) == len(set(codes))


def test_check_connection_ok(adapter):
    assert adapter.check_connection() is None
