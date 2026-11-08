"""Perdas e avarias: aprovação obrigatória, segregação, tenant e idempotência."""
import pytest
from sqlalchemy import select

from app.api.erp_deps import get_erp_adapter
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ProductReference, Role
from tests.conftest import auth, make_user

BASE = "/api/v1/stock-losses"


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
    e.aud, e.op2, e.admin_b = (auth(client, x) for x in ("aud@a.com", "op2@a.com", "admin@b.com"))
    client.post("/api/v1/catalog/sync", headers=e.admin)
    e.loc = client.post(f"/api/v1/stores/{world['store_a1'].id}/locations",
                        json={"code": "LOJ", "name": "Loja"}, headers=e.admin).json()["id"]
    e.loc_a2 = client.post(f"/api/v1/stores/{world['store_a2'].id}/locations",
                           json={"code": "LOJ", "name": "Loja 2"}, headers=e.admin).json()["id"]
    e.p = {x.external_id: str(x.id) for x in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == world["org_a"].id))}
    return e


def new(e, headers=None, expect=201, **over):
    e.n += 1
    body = {"client_request_id": f"req-{e.n:08d}", "location_id": e.loc, "product_id": e.p["1001"],
            "quantity": "2", "reason": "damaged_package", **over}
    r = e.client.post(BASE, json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def act(e, item, action, headers, expect=200, **body):
    r = e.client.post(f"{BASE}/{item['id']}/{action}", json=body or None, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def fix(e, **over):
    return {"product_id": e.p["1001"], "quantity": "1", "reason": "breakage", **over}


def test_every_loss_starts_pending_and_approval_does_not_touch_erp(env):
    item = new(env, lot="L1", expiry_date="2026-12-01")
    assert item["status"] == "pending_approval" and item["erp_status"] == "not_sent"
    assert item["lot"] == "L1" and item["expiry_date"] == "2026-12-01" and item["registered_by_name"] == "op"
    # Mesmo registrada por gerente, aguarda aprovação.
    assert new(env, headers=env.manager)["status"] == "pending_approval"
    approved = act(env, item, "approve", env.manager)
    assert approved["status"] == "approved" and approved["decided_by_name"] == "manager"
    assert approved["erp_status"] == "not_sent"
    actions = {a for (a,) in env.db.execute(select(AuditEvent.action))}
    assert {"loss.created", "loss.approved"} <= actions


def test_reject_is_final(env):
    item = new(env)
    assert act(env, item, "reject", env.manager, reason="Sem evidência")["decision_note"] == "Sem evidência"
    act(env, item, "approve", env.manager, expect=409)
    act(env, item, "cancel", env.op, expect=409, reason="tarde demais")


def test_correction_only_by_registrant_and_returns_to_approval(env):
    item = new(env)
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix(env), headers=env.op)
    assert r.status_code == 409  # sem correção pedida
    act(env, item, "request-correction", env.manager, reason="Motivo errado")
    assert env.client.patch(f"{BASE}/{item['id']}", json=fix(env), headers=env.manager).status_code == 403
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix(env), headers=env.op)
    assert r.status_code == 200 and r.json()["status"] == "pending_approval"
    assert r.json()["reason"] == "breakage" and r.json()["quantity"] == "1.000"


def test_segregation_blocks_registrant_and_can_be_turned_off(env):
    item = new(env, headers=env.manager)
    act(env, item, "approve", env.manager, expect=403)
    assert env.client.put("/api/v1/consumption-config/settings", json={"segregation": False},
                          headers=env.admin).status_code == 204
    act(env, item, "approve", env.manager)


def test_operator_and_auditor_permissions(env):
    item = new(env)
    act(env, item, "approve", env.op2, expect=403)
    act(env, item, "approve", env.aud, expect=403)
    new(env, headers=env.aud, expect=403)


def test_operator_sees_only_own_losses(env):
    mine, other = new(env), new(env, headers=env.op2)
    seen = {c["id"] for c in env.client.get(BASE, headers=env.op).json()}
    assert mine["id"] in seen and other["id"] not in seen
    assert env.client.get(f"{BASE}/{other['id']}", headers=env.op).status_code == 404
    assert len(env.client.get(BASE, headers=env.aud).json()) == 2
    assert len(env.client.get(f"{BASE}?status=approved", headers=env.aud).json()) == 0


def test_cancel_by_registrant_or_manager(env):
    item = new(env)
    act(env, item, "cancel", env.op2, expect=404, reason="não é minha")
    assert act(env, item, "cancel", env.op, reason="lancei errado")["status"] == "cancelled"
    other = new(env)
    act(env, other, "approve", env.manager)
    assert act(env, other, "cancel", env.manager, reason="desfeito")["status"] == "cancelled"


def test_other_reason_requires_note_and_invalid_reason_refused(env):
    new(env, reason="other", expect=422)
    assert new(env, reason="other", note="Caiu da prateleira")["reason"] == "other"
    new(env, reason="inventario", expect=422)


def test_unit_fraction_and_positive_quantity(env):
    new(env, quantity="1.5", expect=422)
    assert new(env, product_id=env.p["2001"], quantity="0.75")["unit_code"] == "KG"
    new(env, quantity="0", expect=422)


def test_idempotent_create(env):
    body = {"client_request_id": "fixo-0001", "location_id": env.loc, "product_id": env.p["1001"],
            "quantity": "2", "reason": "spoilage"}
    first = env.client.post(BASE, json=body, headers=env.op)
    again = env.client.post(BASE, json=body, headers=env.op)
    assert first.status_code == 201 and again.status_code == 200 and first.json()["id"] == again.json()["id"]
    assert env.client.post(BASE, json={**body, "quantity": "3"}, headers=env.op).status_code == 409


def test_other_organization_gets_404(env):
    item = new(env)
    assert env.client.get(f"{BASE}/{item['id']}", headers=env.admin_b).status_code == 404
    assert env.client.post(f"{BASE}/{item['id']}/approve", headers=env.admin_b).status_code == 404
    assert env.client.get(BASE, headers=env.admin_b).json() == []


def test_store_bound_operator_cannot_use_other_store_location(env):
    new(env, location_id=env.loc_a2, expect=404)
