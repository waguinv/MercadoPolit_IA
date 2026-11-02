from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.integrations.erp_port import (
    ErpNotAvailableError,
    ErpProduct,
    ErpTimeoutError,
    ErpUnit,
)
from app.integrations.factory import get_erp_adapter
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ProductReference, StockLocation, UnitReference
from tests.conftest import auth


def use_adapter(client, adapter):
    client.app.dependency_overrides[get_erp_adapter] = lambda: adapter


def count(db, model, **flt):
    q = select(func.count()).select_from(model)
    for k, v in flt.items():
        q = q.where(getattr(model, k) == v)
    return db.scalar(q)


# ---------- sincronização ----------
def test_sync_requires_admin(client, world):
    use_adapter(client, MockErpAdapter())
    assert client.post("/api/v1/catalog/sync", headers=auth(client, "manager@a.com")).status_code == 403
    assert client.post("/api/v1/catalog/sync").status_code == 401


def test_sync_creates_catalog_and_is_idempotent(client, world, db):
    use_adapter(client, MockErpAdapter())
    h = auth(client, "admin@a.com")
    r1 = client.post("/api/v1/catalog/sync", headers=h).json()
    assert r1["units_created"] == 3 and r1["products_created"] == 5
    r2 = client.post("/api/v1/catalog/sync", headers=h).json()
    assert r2["units_created"] == r2["products_created"] == 0
    assert r2["products_updated"] == r2["products_deactivated"] == 0
    assert r2["products_unchanged"] == 5
    assert count(db, ProductReference) == 5


def test_sync_updates_and_deactivates_missing_products(client, world, db):
    erp = MockErpAdapter()
    use_adapter(client, erp)
    h = auth(client, "admin@a.com")
    client.post("/api/v1/catalog/sync", headers=h)
    erp.products = [p for p in erp.products if p.external_id != "1002"]
    erp.products[0] = ErpProduct("1001", "Arroz Tipo 1 5kg (novo nome)", "UN", sku="ARZ5",
                                 barcode="7890000000011")
    r = client.post("/api/v1/catalog/sync", headers=h).json()
    assert r["products_updated"] == 1 and r["products_deactivated"] == 1
    gone = db.scalar(select(ProductReference).where(ProductReference.external_id == "1002"))
    assert gone is not None and gone.is_active is False  # nunca apagado
    # volta no ERP -> reativa
    erp.products.append(ErpProduct("1002", "Feijão Carioca 1kg", "UN", sku="FEJ1",
                                   barcode="7890000000028"))
    client.post("/api/v1/catalog/sync", headers=h)
    db.refresh(gone)
    assert gone.is_active is True


def test_sync_is_isolated_per_organization(client, world, db):
    use_adapter(client, MockErpAdapter())
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    assert count(db, ProductReference, organization_id=world["org_b"].id) == 0
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@b.com"))  # mesmos external_id
    assert count(db, ProductReference, organization_id=world["org_a"].id) == 5
    assert count(db, ProductReference, organization_id=world["org_b"].id) == 5
    # desativar produto na org A não afeta a org B
    erp = MockErpAdapter(products=[])
    use_adapter(client, erp)
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    assert count(db, ProductReference, organization_id=world["org_b"].id, is_active=True) == 5


def test_sync_audited_with_counts(client, world, db):
    use_adapter(client, MockErpAdapter())
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "catalog.synced")).one()
    assert ev.organization_id == world["org_a"].id and ev.details["products_created"] == 5


class _Failing:
    def __init__(self, exc):
        self.exc = exc

    def list_units(self):
        raise self.exc

    list_products = list_units

    def get_stock_balance(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize(("exc", "code"), [(ErpTimeoutError("t"), 504), (ErpNotAvailableError("n"), 503)])
def test_sync_erp_failure_changes_nothing_and_is_audited(client, world, db, exc, code):
    use_adapter(client, MockErpAdapter())
    h = auth(client, "admin@a.com")
    client.post("/api/v1/catalog/sync", headers=h)
    use_adapter(client, _Failing(exc))
    assert client.post("/api/v1/catalog/sync", headers=h).status_code == code
    assert count(db, ProductReference, is_active=True) == 5  # nada foi desativado
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "catalog.sync_failed")).one()


@pytest.mark.parametrize("bad", [
    {"products": [ErpProduct("1", "A", "UN"), ErpProduct("1", "B", "UN")]},      # id duplicado
    {"products": [ErpProduct("1", "A", "XX")]},                                   # unidade inexistente
    {"units": [ErpUnit("UN", "a"), ErpUnit("UN", "b")], "products": []},          # unidade duplicada
])
def test_sync_rejects_inconsistent_erp_data_without_touching_db(client, world, db, bad):
    use_adapter(client, MockErpAdapter())
    h = auth(client, "admin@a.com")
    client.post("/api/v1/catalog/sync", headers=h)
    args = {"units": [ErpUnit("UN", "Unidade")], **bad}
    use_adapter(client, MockErpAdapter(**args))
    assert client.post("/api/v1/catalog/sync", headers=h).status_code == 502
    assert count(db, ProductReference, is_active=True) == 5
    assert count(db, UnitReference, is_active=True) == 3


def test_sysordem_adapter_not_available_returns_503(client, world, monkeypatch):
    from app.core import config
    monkeypatch.setenv("ERP_ADAPTER", "sysordem")
    config.get_settings.cache_clear()
    try:
        r = client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
        assert r.status_code == 503
    finally:
        monkeypatch.delenv("ERP_ADAPTER")
        config.get_settings.cache_clear()


# ---------- consulta ----------
@pytest.fixture()
def synced(client, world):
    use_adapter(client, MockErpAdapter())
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@b.com"))


def test_products_search_and_filters(client, world, synced):
    h = auth(client, "op@a.com")
    names = [p["name"] for p in client.get("/api/v1/products?q=arroz", headers=h).json()]
    assert names == ["Arroz Tipo 1 5kg"]
    by_code = client.get("/api/v1/products?barcode=7890000000028", headers=h).json()
    assert [p["external_id"] for p in by_code] == ["1002"]
    assert client.get("/api/v1/products?q=%25", headers=h).json() == []  # % é literal, não curinga
    page = client.get("/api/v1/products?limit=2&offset=1", headers=h).json()
    assert len(page) == 2
    assert client.get("/api/v1/products?limit=0", headers=h).status_code == 422
    assert client.get("/api/v1/products").status_code == 401


def test_products_and_units_are_tenant_scoped(client, world, synced, db):
    ha = auth(client, "admin@a.com")
    pid_b = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == world["org_b"].id))
    assert client.get(f"/api/v1/products/{pid_b}", headers=ha).status_code == 404
    ids_a = {u["id"] for u in client.get("/api/v1/units", headers=ha).json()}
    ids_b = {u["id"] for u in client.get("/api/v1/units", headers=auth(client, "admin@b.com")).json()}
    assert ids_a and ids_a.isdisjoint(ids_b)


def test_inactive_products_hidden_by_default(client, world, synced, db):
    erp = MockErpAdapter(products=[])
    use_adapter(client, erp)
    client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    h = auth(client, "admin@a.com")
    assert client.get("/api/v1/products", headers=h).json() == []
    assert len(client.get("/api/v1/products?include_inactive=true", headers=h).json()) == 5


# ---------- saldo (leitura do ERP) ----------
def test_stock_balance_read_from_erp(client, world, synced, db):
    h = auth(client, "manager@a.com")
    pid = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == world["org_a"].id, ProductReference.external_id == "2001"))
    r = client.get(f"/api/v1/products/{pid}/stock", headers=h).json()
    assert Decimal(r["quantity"]) == Decimal("35.500") and r["unit_code"] == "KG"
    assert r["source"] == "erp"


def test_stock_by_location_needs_erp_code_and_tenant_access(client, world, synced, db):
    ha = auth(client, "admin@a.com")
    pid = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == world["org_a"].id, ProductReference.external_id == "1001"))
    sid = world["store_a1"].id
    no_code = client.post(f"/api/v1/stores/{sid}/locations", json={"code": "L", "name": "Sem código"},
                          headers=ha).json()
    assert client.get(f"/api/v1/products/{pid}/stock?location_id={no_code['id']}",
                      headers=ha).status_code == 422
    dep = client.post(f"/api/v1/stores/{sid}/locations",
                      json={"code": "DEP", "name": "Depósito", "external_code": "DEP"}, headers=ha).json()
    r = client.get(f"/api/v1/products/{pid}/stock?location_id={dep['id']}", headers=ha).json()
    assert Decimal(r["quantity"]) == Decimal("100")
    # local de outro tenant -> 404
    other = StockLocation(organization_id=world["org_b"].id, store_id=world["store_b1"].id,
                          code="X", name="X", external_code="DEP")
    db.add(other)
    db.commit()
    assert client.get(f"/api/v1/products/{pid}/stock?location_id={other.id}", headers=ha).status_code == 404


def test_stock_erp_timeout_maps_to_504(client, world, synced, db):
    h = auth(client, "admin@a.com")
    pid = db.scalar(select(ProductReference.id).where(ProductReference.organization_id == world["org_a"].id))
    use_adapter(client, _Failing(ErpTimeoutError("t")))
    assert client.get(f"/api/v1/products/{pid}/stock", headers=h).status_code == 504
