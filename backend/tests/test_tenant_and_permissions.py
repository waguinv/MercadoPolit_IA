from sqlalchemy import select

from app.models import AuditEvent, User
from tests.conftest import auth, login


def test_list_stores_only_own_organization(client, world):
    h = auth(client, "admin@a.com")
    codes = {s["code"] for s in client.get("/api/v1/stores", headers=h).json()}
    assert codes == {"A1", "A2"}


def test_store_restricted_user_sees_only_own_store(client, world):
    h = auth(client, "op@a.com")
    assert [s["code"] for s in client.get("/api/v1/stores", headers=h).json()] == ["A1"]
    assert client.get(f"/api/v1/stores/{world["store_a2"].id}", headers=h).status_code == 404


def test_cannot_read_other_tenant_store_by_id(client, world):
    h = auth(client, "admin@a.com")
    assert client.get(f"/api/v1/stores/{world["store_b1"].id}", headers=h).status_code == 404


def test_client_cannot_choose_organization(client, world):
    h = auth(client, "admin@a.com")
    body = {"code": "NOVA", "name": "Nova", "organization_id": str(world["org_b"].id)}
    r = client.post("/api/v1/stores", json=body, headers=h)
    assert r.status_code == 201
    assert r.json()["organization_id"] == str(world["org_a"].id)


def test_non_admin_cannot_create_store_or_users(client, world):
    h = auth(client, "manager@a.com")
    assert client.post("/api/v1/stores", json={"code": "X", "name": "X"}, headers=h).status_code == 403
    assert client.get("/api/v1/users", headers=h).status_code == 403


def test_duplicate_store_code_conflict_but_allowed_across_orgs(client, world):
    ha, hb = auth(client, "admin@a.com"), auth(client, "admin@b.com")
    assert client.post("/api/v1/stores", json={"code": "A1", "name": "dup"}, headers=ha).status_code == 409
    assert client.post("/api/v1/stores", json={"code": "A1", "name": "ok"}, headers=hb).status_code == 201


def test_admin_creates_user_in_own_org_and_audits(client, world, db):
    h = auth(client, "admin@a.com")
    body = {"email": "Novo@A.com", "full_name": "Novo", "password": "uma-senha-longa-1",
            "role": "operator", "store_id": str(world["store_a1"].id)}
    r = client.post("/api/v1/users", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["organization_id"] == str(world["org_a"].id)
    assert r.json()["email"] == "novo@a.com"
    assert login(client, "novo@a.com", "uma-senha-longa-1").status_code == 200
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.created")).one()
    assert ev.organization_id == world["org_a"].id
    assert "uma-senha" not in str(ev.details)
    assert db.scalar(select(User).where(User.email == "novo@a.com")).password_hash != "uma-senha-longa-1"


def test_cannot_assign_user_to_other_tenant_store(client, world):
    h = auth(client, "admin@a.com")
    body = {"email": "x@a.com", "full_name": "X", "password": "uma-senha-longa-1",
            "role": "operator", "store_id": str(world["store_b1"].id)}
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 404


def test_user_list_isolated_per_org(client, world):
    emails = {u["email"] for u in client.get("/api/v1/users", headers=auth(client, "admin@b.com")).json()}
    assert emails == {"admin@b.com"}


def test_weak_password_and_duplicate_email_rejected(client, world):
    h = auth(client, "admin@a.com")
    base = {"email": "y@a.com", "full_name": "Y", "role": "operator"}
    assert client.post("/api/v1/users", json={**base, "password": "curta"}, headers=h).status_code == 422
    dup = {**base, "email": "admin@b.com", "password": "uma-senha-longa-1"}
    assert client.post("/api/v1/users", json=dup, headers=h).status_code == 409
