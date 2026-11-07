"""Consumo interno: aprovação, segregação, modos de registro, tenant e idempotência."""
import pytest
from sqlalchemy import select

from app.api.erp_deps import get_erp_adapter
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ProductReference, Role
from tests.conftest import auth, make_user

BASE = "/api/v1/internal-consumptions"
CFG = "/api/v1/consumption-config"


class Env:
    pass


@pytest.fixture()
def env(client, world, db):
    e = Env()
    e.client, e.world, e.db, e.n = client, world, db, 0
    client.app.dependency_overrides[get_erp_adapter] = lambda: MockErpAdapter()
    e.admin, e.manager, e.op = (auth(client, x) for x in ("admin@a.com", "manager@a.com", "op@a.com"))
    make_user(db, world["org_a"], "aud@a.com", Role.AUDITOR)
    make_user(db, world["org_a"], "op2@a.com", Role.OPERATOR, world["store_a1"])
    make_user(db, world["org_a"], "manager2@a.com", Role.MANAGER)
    e.aud, e.op2, e.manager2 = (auth(client, x) for x in ("aud@a.com", "op2@a.com", "manager2@a.com"))
    e.admin_b = auth(client, "admin@b.com")
    client.post("/api/v1/catalog/sync", headers=e.admin)
    e.loc = client.post(f"/api/v1/stores/{world['store_a1'].id}/locations",
                        json={"code": "LOJ", "name": "Loja"}, headers=e.admin).json()["id"]
    e.loc_a2 = client.post(f"/api/v1/stores/{world['store_a2'].id}/locations",
                           json={"code": "LOJ", "name": "Loja 2"}, headers=e.admin).json()["id"]
    e.p = {x.external_id: str(x.id) for x in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == world["org_a"].id))}
    e.self_dep = client.post(f"{CFG}/departments", json={"name": "Refeitório", "mode": "self_service"},
                             headers=e.admin).json()["id"]
    e.central_dep = client.post(f"{CFG}/departments", json={"name": "Limpeza", "mode": "centralized"},
                                headers=e.admin).json()["id"]
    e.purpose = client.post(f"{CFG}/purposes", json={"name": "Café/lanche"}, headers=e.admin).json()["id"]
    return e


def rid(e):
    e.n += 1
    return f"req-{e.n:08d}"


def new(e, headers=None, expect=201, **over):
    body = {"client_request_id": rid(e), "location_id": e.loc, "department_id": e.self_dep,
            "purpose_id": e.purpose, "product_id": e.p["1001"], "quantity": "2", **over}
    r = e.client.post(BASE, json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def act(e, item, action, headers, expect=200, **body):
    r = e.client.post(f"{BASE}/{item['id']}/{action}", json=body or None, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# ------------------------------------------------------------- fluxo principal
def test_operator_self_service_then_manager_approves_without_touching_erp(env):
    item = new(env)
    assert item["status"] == "pending_approval" and item["consumer_name"] == "op"
    assert item["registered_by_name"] == "op" and item["erp_status"] == "not_sent"
    assert item["product_name"] and item["unit_code"] == "UN" and item["department_name"] == "Refeitório"
    approved = act(env, item, "approve", env.manager)
    assert approved["status"] == "approved" and approved["decided_by_name"] == "manager"
    assert approved["erp_status"] == "not_sent"
    actions = {a for (a,) in env.db.execute(select(AuditEvent.action))}
    assert {"consumption.created", "consumption.approved"} <= actions


def test_reject_is_final_and_keeps_reason(env):
    item = new(env)
    act(env, item, "reject", env.manager, reason="Não era consumo")
    got = env.client.get(f"{BASE}/{item['id']}", headers=env.op).json()
    assert got["status"] == "rejected" and got["decision_note"] == "Não era consumo"
    act(env, item, "approve", env.manager, expect=409)
    act(env, item, "cancel", env.op, expect=409, reason="tarde demais")


def test_correction_cycle_only_by_registrant(env):
    item = new(env)
    act(env, item, "request-correction", env.manager, reason="Quantidade errada")
    fix = {"department_id": env.self_dep, "purpose_id": env.purpose, "product_id": env.p["1001"], "quantity": "1"}
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.manager)
    assert r.status_code in (403, 404)
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.op)
    assert r.status_code == 200 and r.json()["status"] == "pending_approval" and r.json()["quantity"] == "1.000"
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.op)
    assert r.status_code == 409  # já voltou para aprovação


# ------------------------------------------------------------- segregação
def test_segregation_blocks_self_approval(env):
    item = new(env, headers=env.manager, department_id=env.central_dep, consumer_name="Maria do caixa")
    act(env, item, "approve", env.manager, expect=403)
    act(env, item, "approve", env.manager2)


def test_segregation_blocks_consumer_approving(env):
    mgr2_id = next(c["id"] for c in env.client.get(f"{CFG}/consumers", headers=env.manager).json()
                   if c["name"] == "manager2")
    item = new(env, headers=env.manager, department_id=env.central_dep, consumer_user_id=mgr2_id)
    assert item["consumer_name"] == "manager2"
    act(env, item, "approve", env.manager2, expect=403)
    act(env, item, "approve", env.admin)


def test_without_segregation_self_approval_is_allowed(env):
    r = env.client.put(f"{CFG}/settings", json={"segregation": False}, headers=env.admin)
    assert r.status_code == 204
    item = new(env, headers=env.manager, department_id=env.central_dep, consumer_name="Ana")
    act(env, item, "approve", env.manager)


# ------------------------------------------------------------- modos e permissões
def test_centralized_department_rejects_operator(env):
    new(env, department_id=env.central_dep, expect=403)


def test_operator_cannot_register_for_someone_else(env):
    new(env, consumer_name="Outra pessoa", expect=403)


def test_manager_must_say_who_consumed(env):
    new(env, headers=env.manager, department_id=env.central_dep, expect=422)


def test_operator_and_auditor_cannot_decide(env):
    item = new(env)
    act(env, item, "approve", env.op2, expect=403)
    act(env, item, "approve", env.aud, expect=403)
    new(env, headers=env.aud, expect=403)


def test_operator_sees_only_own_consumptions(env):
    mine = new(env)
    other = new(env, headers=env.op2)
    seen = {c["id"] for c in env.client.get(BASE, headers=env.op).json()}
    assert mine["id"] in seen and other["id"] not in seen
    assert env.client.get(f"{BASE}/{other['id']}", headers=env.op).status_code == 404
    assert len(env.client.get(BASE, headers=env.aud).json()) == 2


def test_cancel_by_registrant_or_manager_only(env):
    item = new(env)
    act(env, item, "cancel", env.op2, expect=404, reason="não é meu")
    act(env, item, "cancel", env.op, reason="lancei errado")
    item2 = new(env)
    act(env, item2, "approve", env.manager)
    assert act(env, item2, "cancel", env.manager, reason="desistiu")["status"] == "cancelled"


def test_config_changes_are_admin_only(env):
    assert env.client.post(f"{CFG}/departments", json={"name": "Padaria"}, headers=env.manager).status_code == 403
    assert env.client.put(f"{CFG}/settings", json={"segregation": False}, headers=env.manager).status_code == 403
    assert env.client.post(f"{CFG}/defaults", headers=env.admin).status_code == 204
    names = {d["name"] for d in env.client.get(CFG, headers=env.op).json()["departments"]}
    assert {"Refeitório", "Padaria", "Açougue"} <= names
    assert env.client.post(f"{CFG}/defaults", headers=env.admin).status_code == 204  # sem duplicar
    assert len(env.client.get(CFG, headers=env.op).json()["departments"]) == len(names)


def test_inactive_department_and_duplicate_name(env):
    r = env.client.patch(f"{CFG}/departments/{env.self_dep}", json={"is_active": False}, headers=env.admin)
    assert r.status_code == 200
    new(env, expect=404)
    r = env.client.post(f"{CFG}/departments", json={"name": "Limpeza"}, headers=env.admin)
    assert r.status_code == 409


# ------------------------------------------------------------- quantidades e idempotência
def test_unit_without_fraction_rejects_decimal_and_kg_accepts(env):
    new(env, quantity="1.5", expect=422)
    item = new(env, product_id=env.p["2001"], quantity="0.75")
    assert item["unit_code"] == "KG" and item["quantity"] == "0.750"
    new(env, quantity="0", expect=422)


def test_idempotent_create(env):
    body = {"client_request_id": "fixo-0001", "location_id": env.loc, "department_id": env.self_dep,
            "purpose_id": env.purpose, "product_id": env.p["1001"], "quantity": "2"}
    first = env.client.post(BASE, json=body, headers=env.op)
    again = env.client.post(BASE, json=body, headers=env.op)
    assert first.status_code == 201 and again.status_code == 200 and first.json()["id"] == again.json()["id"]
    assert env.client.post(BASE, json={**body, "quantity": "3"}, headers=env.op).status_code == 409


# ------------------------------------------------------------- tenant e loja
def test_other_organization_gets_404(env):
    item = new(env)
    assert env.client.get(f"{BASE}/{item['id']}", headers=env.admin_b).status_code == 404
    assert env.client.post(f"{BASE}/{item['id']}/approve", headers=env.admin_b).status_code == 404
    assert env.client.get(BASE, headers=env.admin_b).json() == []
    assert env.client.get(CFG, headers=env.admin_b).json()["departments"] == []
    r = env.client.patch(f"{CFG}/departments/{env.self_dep}", json={"is_active": False}, headers=env.admin_b)
    assert r.status_code == 404


def test_store_bound_operator_cannot_use_other_store_location(env):
    new(env, location_id=env.loc_a2, expect=404)
