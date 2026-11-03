from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.crypto import SecretsError, decrypt_secret, encrypt_secret
from app.integrations.erp_port import ErpAuthError, ErpInvalidRequestError
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ErpConnection, ProductReference
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
    monkeypatch.setattr("app.api.erp_deps.SysordemErpAdapter", FakeSysordem)


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
def test_only_admin_manages_connection(client, world):
    hm = auth(client, "manager@a.com")
    assert client.get("/api/v1/erp/connection", headers=hm).status_code == 403
    assert client.put("/api/v1/erp/connection", json={"token": TOKEN_A}, headers=hm).status_code == 403
    assert client.delete("/api/v1/erp/connection", headers=hm).status_code == 403
    assert client.get("/api/v1/erp/connection").status_code == 401


def test_token_is_encrypted_at_rest_never_returned_and_not_audited(client, world, db):
    r = put_conn(client, "admin@a.com", TOKEN_A)
    assert r.status_code == 200 and r.json()["configured"] is True
    assert TOKEN_A not in r.text
    assert TOKEN_A not in client.get("/api/v1/erp/connection", headers=auth(client, "admin@a.com")).text
    raw = db.execute(text("select token_encrypted from erp_connections")).scalar_one()
    assert TOKEN_A not in raw and decrypt_secret(raw) == TOKEN_A
    for ev in db.scalars(select(AuditEvent)):
        assert TOKEN_A not in str(ev.details) and TOKEN_A not in str(ev.entity_id)
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.created")).one()


def test_connection_is_per_organization_and_replace_resets_verification(client, world, db):
    put_conn(client, "admin@a.com", TOKEN_A)
    assert client.get("/api/v1/erp/connection", headers=auth(client, "admin@b.com")).json() == {
        "configured": False, "is_active": False, "last_verified_at": None}
    put_conn(client, "admin@b.com", TOKEN_B)
    assert db.query(ErpConnection).count() == 2
    conn_a = db.scalar(select(ErpConnection).where(ErpConnection.organization_id == world["org_a"].id))
    from app.db.base import utcnow
    conn_a.last_verified_at = utcnow()
    db.commit()
    put_conn(client, "admin@a.com", "token-novo-da-rede-A")
    db.refresh(conn_a)
    assert conn_a.last_verified_at is None
    assert db.query(ErpConnection).count() == 2  # substitui, não duplica
    assert decrypt_secret(conn_a.token_encrypted) == "token-novo-da-rede-A"


def test_short_token_rejected_and_delete(client, world, db):
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
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "erp_connection.tested")).one()


def test_connection_test_failure_maps_error_and_audits(client, world, monkeypatch, sysordem_mode, db):
    class Rejecting(FakeSysordem):
        def check_connection(self):
            raise ErpAuthError("x")

    monkeypatch.setattr("app.api.erp_deps.SysordemErpAdapter", Rejecting)
    put_conn(client, "admin@a.com", TOKEN_A)
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

    monkeypatch.setattr("app.api.erp_deps.SysordemErpAdapter", NeedsLocation)
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
