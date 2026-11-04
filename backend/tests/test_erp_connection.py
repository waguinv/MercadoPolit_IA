from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.crypto import SecretsError, decrypt_secret, encrypt_secret
from app.integrations.erp_port import ErpAuthError, ErpDataError, ErpInvalidRequestError, ErpTimeoutError
from app.integrations.mock_erp import MockErpAdapter
from app.integrations.registry import CONNECTORS
from app.models import AuditEvent, ErpConnection, Organization, ProductReference
from tests.conftest import auth

TOKEN_A = "token-da-rede-A-abcdef"
TOKEN_B = "token-da-rede-B-uvwxyz"


class FakeSysordem:
    """Substitui o adaptador real: registra com qual token foi criado e delega a um mock."""

    created: list[str] = []

    def __init__(self, base_url, token, **kw):
        FakeSysordem.created.append(token)
        self.token = token
        self.inner = MockErpAdapter()
        self.closed = False

    def close(self):
        self.closed = True

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture()
def fake_sysordem(monkeypatch, sysordem_mode):
    FakeSysordem.created = []
    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", FakeSysordem)


def put_conn(client, email, token):
    return client.put("/api/v1/erp/connection", json={"token": token}, headers=auth(client, email))


# ---------- criptografia ----------
def test_crypto_roundtrip_and_ciphertext_differs():
    c = encrypt_secret("segredo")
    assert c != "segredo" and "segredo" not in c and decrypt_secret(c) == "segredo"


def test_crypto_wrong_key_fails(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core import config
    c = encrypt_secret("segredo")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SecretsError):
            decrypt_secret(c)
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


def test_crypto_missing_key_fails(monkeypatch):
    from app.core import config
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SecretsError):
            encrypt_secret("x")
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


# ---------- API de conexão ----------
def test_only_admin_manages_connection(client, world, fake_sysordem):
    hm = auth(client, "manager@a.com")
    assert client.get("/api/v1/erp/connection", headers=hm).status_code == 403
    assert client.put("/api/v1/erp/connection", json={"token": TOKEN_A}, headers=hm).status_code == 403
    assert client.delete("/api/v1/erp/connection", headers=hm).status_code == 403
    assert client.get("/api/v1/erp/connection").status_code == 401


def test_token_is_encrypted_at_rest_never_returned_and_not_audited(client, world, fake_sysordem, db):
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 200 and r.json()["configured"] is True
    assert TOKEN_A not in r.text
    assert TOKEN_A not in client.get("/api/v1/erp/connection", headers=auth(client, "admin@a.com")).text
    raw = db.execute(text("select token_encrypted from erp_connections")).scalar_one()
    assert TOKEN_A not in raw and decrypt_secret(raw) == TOKEN_A
    for ev in db.scalars(select(AuditEvent)):
        assert TOKEN_A not in str(ev.details) and TOKEN_A not in str(ev.entity_id)
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.created")).one()


def test_connection_is_per_organization_and_replace_verifies_new_token(client, world, fake_sysordem, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    assert client.get("/api/v1/erp/connection", headers=auth(client, "admin@b.com")).json() == {
        "adapter": "real", "configured": False, "kind": None, "is_active": False,
        "last_verified_at": None, "checks": []}
    put_conn(client, "admin@b.com", TOKEN_B)
    assert db.query(ErpConnection).count() == 2
    conn_a = db.scalar(select(ErpConnection).where(ErpConnection.organization_id == world["org_a"].id))
    put_conn(client, "admin@a.com", "token-novo-da-rede-A")
    db.refresh(conn_a)
    assert FakeSysordem.created[-1] == "token-novo-da-rede-A"
    assert conn_a.last_verified_at is not None
    assert db.query(ErpConnection).count() == 2  # substitui, não duplica
    assert decrypt_secret(conn_a.token_encrypted) == "token-novo-da-rede-A"


def test_short_token_rejected_and_delete(client, world, fake_sysordem, db):
    assert put_conn(client, "admin@a.com", "curto").status_code == 422
    put_conn(client, "admin@a.com", TOKEN_A)
    h = auth(client, "admin@a.com")
    assert client.delete("/api/v1/erp/connection", headers=h).status_code == 204
    assert client.delete("/api/v1/erp/connection", headers=h).status_code == 404
    assert db.query(ErpConnection).count() == 0


# ---------- resolução do adaptador por organização ----------
def test_sysordem_mode_without_connection_returns_503(client, world, sysordem_mode):
    h = auth(client, "admin@a.com")
    assert client.post("/api/v1/catalog/sync", headers=h).status_code == 503
    assert client.get("/api/v1/erp/stock-locations", headers=h).status_code == 503


def test_each_org_uses_its_own_token_and_adapter_is_closed(client, world, fake_sysordem, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    put_conn(client, "admin@b.com", TOKEN_B)
    FakeSysordem.created.clear()
    assert client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com")).status_code == 200
    assert FakeSysordem.created == [TOKEN_A]
    assert client.post("/api/v1/catalog/sync", headers=auth(client, "admin@b.com")).status_code == 200
    assert FakeSysordem.created == [TOKEN_A, TOKEN_B]
    assert db.query(ProductReference).filter_by(organization_id=world["org_a"].id).count() == 5
    assert db.query(ProductReference).filter_by(organization_id=world["org_b"].id).count() == 5


def test_inactive_connection_not_used(client, world, fake_sysordem, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    conn = db.scalar(select(ErpConnection))
    conn.is_active = False
    db.commit()
    FakeSysordem.created.clear()
    assert client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com")).status_code == 503
    assert FakeSysordem.created == []


def test_unreadable_token_after_key_change_returns_503(client, world, sysordem_mode, db, monkeypatch):
    from cryptography.fernet import Fernet

    from app.core import config
    put_conn(client, "admin@a.com", TOKEN_A)
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    r = client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com"))
    assert r.status_code == 503 and TOKEN_A not in r.text


def test_connection_test_updates_verification_and_audits(client, world, fake_sysordem, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    r = client.post("/api/v1/erp/connection/test", headers=auth(client, "admin@a.com"))
    assert r.status_code == 200 and r.json()["last_verified_at"] is not None
    assert [c["ok"] for c in r.json()["checks"]] == [True, True]
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.tested")).one()


def test_connection_test_failure_maps_error_and_audits(client, world, monkeypatch, fake_sysordem, db):
    class Rejecting(FakeSysordem):
        def check_connection(self):
            raise ErpAuthError("x")

    put_conn(client, "admin@a.com", TOKEN_A)
    db.scalar(select(ErpConnection)).last_verified_at = None
    db.commit()
    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", Rejecting)
    r = client.post("/api/v1/erp/connection/test", headers=auth(client, "admin@a.com"))
    assert r.status_code == 502 and "credenciais" in r.json()["detail"]
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.test_failed")).one()
    assert db.scalar(select(ErpConnection)).last_verified_at is None


def test_erp_stock_locations_listing(client, world, fake_sysordem):
    put_conn(client, "admin@a.com", TOKEN_A)
    codes = [x["code"] for x in client.get("/api/v1/erp/stock-locations",
                                           headers=auth(client, "admin@a.com")).json()]
    assert codes == ["DEP", "LOJA"]
    assert client.get("/api/v1/erp/stock-locations", headers=auth(client, "manager@a.com")).status_code == 403


def test_stock_endpoint_maps_invalid_request_to_422_and_unit_comes_from_mirror(
        client, world, monkeypatch, sysordem_mode, db):
    class NeedsLocation(FakeSysordem):
        def get_stock_balance(self, product_external_id, location_code=None):
            if location_code is None:
                raise ErpInvalidRequestError("O SysOrdem exige um local de estoque para consultar saldo.")
            return self.inner.get_stock_balance(product_external_id, location_code)

    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", NeedsLocation)
    put_conn(client, "admin@a.com", TOKEN_A)
    h = auth(client, "admin@a.com")
    client.post("/api/v1/catalog/sync", headers=h)
    pid = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == world["org_a"].id, ProductReference.external_id == "1001"))
    r = client.get(f"/api/v1/products/{pid}/stock", headers=h)
    assert r.status_code == 422 and "local de estoque" in r.json()["detail"]
    sid = world["store_a1"].id
    loc = client.post(f"/api/v1/stores/{sid}/locations",
                      json={"code": "D", "name": "Dep", "external_code": "DEP"}, headers=h).json()
    ok = client.get(f"/api/v1/products/{pid}/stock?location_id={loc['id']}", headers=h).json()
    assert Decimal(ok["quantity"]) == Decimal("100") and ok["unit_code"] == "UN"


# ---------- conectar em um passo ----------
class RejectsToken(FakeSysordem):
    def check_connection(self):
        raise ErpAuthError("x")


class TimesOut(FakeSysordem):
    def check_connection(self):
        raise ErpTimeoutError("x")


class BrokenLocations(FakeSysordem):
    def list_stock_locations(self):
        raise ErpDataError("Local de estoque do SysOrdem sem código válido.")


def test_connect_verifies_saves_and_reports_reads(client, world, fake_sysordem, db):
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "sysordem" and body["last_verified_at"] is not None
    assert body["checks"] == [
        {"name": "Unidades", "ok": True, "detail": "3 encontrado(s)"},
        {"name": "Locais de estoque", "ok": True, "detail": "2 encontrado(s)"},
    ]


def test_rejected_token_is_not_saved_nor_replaces_the_working_one(client, world, fake_sysordem, monkeypatch, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", RejectsToken)
    r = put_conn(client, "admin@a.com", TOKEN_B)
    assert r.status_code == 422 and "Nada foi salvo" in r.json()["detail"] and TOKEN_B not in r.text
    assert decrypt_secret(db.scalar(select(ErpConnection)).token_encrypted) == TOKEN_A
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.test_failed")).all()
    assert len(events) == 1 and TOKEN_B not in str(events[0].details)


def test_erp_timeout_while_connecting_saves_nothing(client, world, sysordem_mode, monkeypatch, db):
    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", TimesOut)
    assert put_conn(client, "admin@a.com", TOKEN_A).status_code == 504
    assert db.query(ErpConnection).count() == 0


def test_failed_read_keeps_connection_and_names_the_problem(client, world, sysordem_mode, monkeypatch, db):
    monkeypatch.setattr("app.integrations.registry.SysordemErpAdapter", BrokenLocations)
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 200 and db.query(ErpConnection).count() == 1
    assert r.json()["checks"][1] == {"name": "Locais de estoque", "ok": False,
                                     "detail": "Local de estoque do SysOrdem sem código válido."}


def test_connect_is_refused_while_server_uses_simulated_erp(client, world, db):
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 409 and "desligada" in r.json()["detail"]
    assert db.query(ErpConnection).count() == 0


def test_unknown_system_is_rejected(client, world, fake_sysordem, db):
    r = client.put("/api/v1/erp/connection", json={"kind": "outro", "token": TOKEN_A},
                   headers=auth(client, "admin@a.com"))
    assert r.status_code == 422 and db.query(ErpConnection).count() == 0
    assert FakeSysordem.created == []


def test_connectors_listing_is_admin_only_and_comes_from_the_registry(client, world):
    r = client.get("/api/v1/erp/connectors", headers=auth(client, "admin@a.com"))
    assert [c["kind"] for c in r.json()] == list(CONNECTORS)
    assert client.get("/api/v1/erp/connectors", headers=auth(client, "manager@a.com")).status_code == 403


@pytest.mark.parametrize("kind", list(CONNECTORS))
def test_every_connector_implements_the_read_only_port(kind):
    erp = CONNECTORS[kind].build("token-so-para-o-teste")
    try:
        for method in ("check_connection", "list_units", "list_products", "list_stock_locations",
                       "get_stock_balance", "list_location_balances", "close"):
            assert callable(getattr(erp, method))
    finally:
        erp.close()
    assert len(kind) <= 20  # cabe em erp_connections.kind


# ---------- origem do catálogo ----------
def test_simulated_catalog_blocks_real_connection_in_the_same_organization(client, world, fake_sysordem, db):
    db.get(Organization, world["org_a"].id).catalog_source = "mock"
    db.commit()
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 409 and "organização nova" in r.json()["detail"]
    assert db.query(ErpConnection).count() == 0 and FakeSysordem.created == []


def test_real_catalog_is_not_overwritten_by_simulated_erp(client, world, db):
    db.get(Organization, world["org_a"].id).catalog_source = "sysordem"
    db.commit()
    assert client.post("/api/v1/catalog/sync", headers=auth(client, "admin@a.com")).status_code == 409
    assert db.query(ProductReference).count() == 0


def test_sync_records_source_and_same_system_can_reconnect(client, world, fake_sysordem, db):
    h = auth(client, "admin@a.com")
    put_conn(client, "admin@a.com", TOKEN_A)
    assert client.post("/api/v1/catalog/sync", headers=h).status_code == 200
    db.expire_all()
    assert db.get(Organization, world["org_a"].id).catalog_source == "sysordem"
    assert db.get(Organization, world["org_b"].id).catalog_source is None
    assert client.delete("/api/v1/erp/connection", headers=h).status_code == 204
    assert put_conn(client, "admin@a.com", TOKEN_B).status_code == 200
    assert client.post("/api/v1/catalog/sync", headers=h).status_code == 200
