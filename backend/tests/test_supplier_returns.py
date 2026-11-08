"""Devoluções e trocas: aprovação, saída e reposição como eventos distintos, tenant e idempotência."""
import pytest
from sqlalchemy import select

from app.api.erp_deps import get_erp_adapter
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ProductReference, Role
from tests.conftest import auth, make_user

BASE = "/api/v1/supplier-returns"
SUPPLIER = "Distribuidora Boa"


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


def key(e, prefix="req"):
    e.n += 1
    return f"{prefix}-{e.n:08d}"


def new(e, headers=None, expect=201, **over):
    body = {"client_request_id": key(e), "location_id": e.loc, "product_id": e.p["1001"], "quantity": "2",
            "reason": "damaged", "modality": "exchange", "supplier_name": SUPPLIER, **over}
    r = e.client.post(BASE, json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def act(e, item, action, headers, expect=200, **body):
    r = e.client.post(f"{BASE}/{item['id']}/{action}", json=body or None, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def receiving(e, *, finalize=True, supplier=SUPPLIER, quantity="5", rejected="0", product="1001", store="store_a1"):
    location = e.loc if store == "store_a1" else e.loc_a2
    r = e.client.post("/api/v1/receivings", headers=e.manager, json={
        "client_request_id": key(e, "rec"), "store_id": str(e.world[store].id), "location_id": location,
        "supplier_name": supplier, "document_number": str(e.n)})
    assert r.status_code == 201, r.text
    rec = r.json()
    r = e.client.post(f"/api/v1/receivings/{rec['id']}/items", headers=e.manager, json={
        "client_request_id": key(e, "ite"), "product_id": e.p[product], "received_quantity": quantity,
        "rejected_quantity": rejected, "rejection_reason": "damaged" if rejected != "0" else None})
    assert r.status_code == 201, r.text
    if finalize:
        r = e.client.post(f"/api/v1/receivings/{rec['id']}/finalize", headers=e.manager, json={"partial": False})
        assert r.status_code == 200, r.text
    return rec


def picked_up(e, **over):
    item = new(e, **over)
    act(e, item, "approve", e.manager)
    return e.client.post(f"{BASE}/{item['id']}/pickup", json={}, headers=e.manager).json()


def replace(e, item, rec, quantity="2", expect=200, headers=None):
    r = e.client.post(f"{BASE}/{item['id']}/replacements", headers=headers or e.manager,
                      json={"receiving_id": rec["id"], "quantity": quantity})
    assert r.status_code == expect, r.text
    return r.json()


def test_every_return_starts_pending_and_nothing_goes_to_erp(env):
    item = new(env, lot="L1", expiry_date="2026-12-01", expected_pickup_date="2026-12-05")
    assert item["status"] == "pending_approval" and item["erp_status"] == "not_sent"
    assert item["supplier_name"] == SUPPLIER and item["replaced_quantity"] == "0"
    assert new(env, headers=env.manager)["status"] == "pending_approval"
    approved = act(env, item, "approve", env.manager)
    assert approved["status"] == "approved" and approved["erp_status"] == "not_sent"


def test_pickup_is_a_separate_event_and_requires_approval(env):
    item = new(env)
    act(env, item, "pickup", env.manager, expect=409, note="cedo")
    act(env, item, "approve", env.manager)
    act(env, item, "pickup", env.op, expect=403, note="sem permissão")
    out = act(env, item, "pickup", env.manager, note="Motorista João")
    assert out["status"] == "picked_up" and out["picked_up_by_name"] == "manager"
    assert out["pickup_note"] == "Motorista João" and out["replaced_quantity"] == "0"
    act(env, item, "pickup", env.manager, expect=409, note="de novo")
    actions = {a for (a,) in env.db.execute(select(AuditEvent.action))}
    assert {"supplier_return.created", "supplier_return.approved", "supplier_return.picked_up"} <= actions


def test_no_replacement_finishes_on_pickup(env):
    out = picked_up(env, modality="no_replacement")
    assert out["status"] == "completed"


def test_replacement_only_after_pickup_and_only_on_exchange(env):
    rec = receiving(env)
    item = new(env)
    act(env, item, "approve", env.manager)
    replace(env, item, rec, expect=409)
    credit = picked_up(env, modality="credit")
    replace(env, credit, rec, expect=409)


def test_replacement_requires_finalized_receiving(env):
    item = picked_up(env)
    replace(env, item, receiving(env, finalize=False), expect=409)
    out = replace(env, item, receiving(env))
    assert out["status"] == "completed" and out["replaced_quantity"] == "2.000"
    assert out["replacements"][0]["registered_by_name"] == "manager"


def test_replacement_rejects_wrong_supplier_product_store_and_rejected_goods(env):
    item = picked_up(env)
    replace(env, item, receiving(env, supplier="Outro"), expect=422)
    replace(env, item, receiving(env, product="2001"), expect=422)
    refused = replace(env, item, receiving(env, quantity="2", rejected="2"), expect=422)
    assert "não foi aceito" in refused["detail"]
    replace(env, item, receiving(env, store="store_a2"), expect=404)
    assert env.client.get(f"{BASE}/{item['id']}", headers=env.manager).json()["replacements"] == []


def test_replacement_cannot_exceed_returned_or_receiving_or_repeat(env):
    item = picked_up(env, quantity="3")
    first = receiving(env, quantity="2")
    replace(env, item, first, quantity="4", expect=422)
    replace(env, item, first, quantity="3", expect=422)  # só 2 aceitos
    assert replace(env, item, first, quantity="1")["status"] == "picked_up"
    replace(env, item, first, quantity="1", expect=409)  # mesma nota de novo
    assert replace(env, item, receiving(env), quantity="2")["status"] == "completed"
    # Saldo da nota é compartilhado entre devoluções diferentes.
    other = picked_up(env, quantity="2")
    replace(env, other, first, quantity="2", expect=422)
    replace(env, other, first, quantity="1")


def test_replacement_cannot_exceed_returned_even_when_receiving_has_more(env):
    item = picked_up(env, quantity="3")
    replace(env, item, receiving(env, quantity="10"), quantity="4", expect=422)


def test_replacement_cannot_be_the_origin_receiving(env):
    rec = receiving(env)
    item = picked_up(env, receiving_id=rec["id"], supplier_name=None)
    replace(env, item, rec, expect=422)


def test_origin_fills_supplier_and_limits_quantity(env):
    rec = receiving(env, quantity="3", supplier="Da Nota")
    item = new(env, receiving_id=rec["id"], supplier_name="Digitado", quantity="2")
    assert item["supplier_name"] == "Da Nota" and item["origin_document"] == rec["document_number"]
    new(env, receiving_id=rec["id"], quantity="2", expect=422)
    act(env, item, "cancel", env.op, reason="lancei errado")
    assert new(env, receiving_id=rec["id"], quantity="3")["quantity"] == "3.000"


def test_origin_must_be_finalized_same_store_with_product(env):
    new(env, receiving_id=receiving(env, finalize=False)["id"], expect=409)
    new(env, receiving_id=receiving(env, product="2001")["id"], expect=422)
    new(env, headers=env.manager, location_id=env.loc_a2, receiving_id=receiving(env)["id"], expect=404)


def test_complete_requires_pickup_and_reason(env):
    item = new(env, modality="credit")
    act(env, item, "complete", env.manager, expect=409, reason="crédito na nota 10")
    act(env, item, "approve", env.manager)
    act(env, item, "pickup", env.manager, note="ok")
    act(env, item, "complete", env.op, expect=403, reason="crédito na nota 10")
    assert env.client.post(f"{BASE}/{item['id']}/complete", json={}, headers=env.manager).status_code == 422
    out = act(env, item, "complete", env.manager, reason="crédito na nota 10")
    assert out["status"] == "completed" and out["completion_note"] == "crédito na nota 10"


def test_cannot_cancel_after_goods_left(env):
    item = picked_up(env)
    act(env, item, "cancel", env.manager, expect=409, reason="mudei de ideia")
    approved = new(env)
    act(env, approved, "approve", env.manager)
    assert act(env, approved, "cancel", env.manager, reason="fornecedor recusou")["status"] == "cancelled"


def test_reject_is_final(env):
    item = new(env)
    act(env, item, "reject", env.manager, reason="Sem evidência")
    act(env, item, "approve", env.manager, expect=409)
    act(env, item, "cancel", env.op, expect=409, reason="tarde demais")


def test_correction_only_by_registrant_and_returns_to_approval(env):
    item = new(env)
    fix = {"product_id": env.p["1001"], "quantity": "1", "reason": "quality", "modality": "credit",
           "supplier_name": "Novo"}
    assert env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.op).status_code == 409
    act(env, item, "request-correction", env.manager, reason="Modalidade errada")
    assert env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.manager).status_code == 403
    r = env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.op)
    assert r.status_code == 200 and r.json()["status"] == "pending_approval"
    assert r.json()["modality"] == "credit" and r.json()["quantity"] == "1.000"


def test_correction_does_not_count_itself_against_origin(env):
    rec = receiving(env, quantity="3")
    item = new(env, receiving_id=rec["id"], quantity="3")
    act(env, item, "request-correction", env.manager, reason="Motivo errado")
    fix = {"product_id": env.p["1001"], "quantity": "3", "reason": "quality", "modality": "exchange",
           "receiving_id": rec["id"]}
    assert env.client.patch(f"{BASE}/{item['id']}", json=fix, headers=env.op).status_code == 200


def test_segregation_blocks_registrant_and_can_be_turned_off(env):
    item = new(env, headers=env.manager)
    act(env, item, "approve", env.manager, expect=403)
    assert env.client.put("/api/v1/consumption-config/settings", json={"segregation": False},
                          headers=env.admin).status_code == 204
    act(env, item, "approve", env.manager)


def test_permissions_and_operator_scope(env):
    mine, other = new(env), new(env, headers=env.op2)
    act(env, mine, "approve", env.op2, expect=403)
    act(env, mine, "approve", env.aud, expect=403)
    new(env, headers=env.aud, expect=403)
    seen = {c["id"] for c in env.client.get(BASE, headers=env.op).json()}
    assert mine["id"] in seen and other["id"] not in seen
    assert env.client.get(f"{BASE}/{other['id']}", headers=env.op).status_code == 404
    assert len(env.client.get(BASE, headers=env.aud).json()) == 2


def test_field_rules(env):
    new(env, reason="other", expect=422)
    assert new(env, reason="other", note="Sem etiqueta")["reason"] == "other"
    new(env, modality="venda", expect=422)
    new(env, supplier_name=None, expect=422)
    new(env, supplier_name="   ", expect=422)
    new(env, modality="credit", expected_replacement_date="2026-12-01", expect=422)
    new(env, quantity="1.5", expect=422)
    new(env, quantity="0", expect=422)
    assert new(env, product_id=env.p["2001"], quantity="0.75")["unit_code"] == "KG"


def test_idempotent_create(env):
    body = {"client_request_id": "fixo-0001", "location_id": env.loc, "product_id": env.p["1001"],
            "quantity": "2", "reason": "quality", "modality": "exchange", "supplier_name": SUPPLIER}
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
