from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.erp_deps import get_erp_adapter
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, ProductReference, Role, StockLocation
from tests.conftest import auth, make_user

BASE = "/api/v1/receivings"


def make_key(prefix43: str) -> str:
    """Gera chave de acesso válida (DV módulo 11) de forma independente do código de produção."""
    total, w = 0, 2
    for d in reversed(prefix43):
        total += int(d) * w
        w = 2 if w == 9 else w + 1
    r = total % 11
    return prefix43 + str(0 if r in (0, 1) else 11 - r)


KEY1 = make_key("3525" + "1" * 39)
KEY2 = make_key("3525" + "2" * 39)


class Env:
    pass


@pytest.fixture()
def env(client, world, db):
    e = Env()
    e.client, e.world, e.db, e.n = client, world, db, 0
    client.app.dependency_overrides[get_erp_adapter] = lambda: MockErpAdapter()
    e.admin, e.manager, e.op = (auth(client, x) for x in ("admin@a.com", "manager@a.com", "op@a.com"))
    make_user(db, world["org_a"], "aud@a.com", Role.AUDITOR)
    e.aud, e.admin_b = auth(client, "aud@a.com"), auth(client, "admin@b.com")
    client.post("/api/v1/catalog/sync", headers=e.admin)
    e.loc = client.post(f"/api/v1/stores/{world['store_a1'].id}/locations",
                        json={"code": "DEP", "name": "Depósito"}, headers=e.admin).json()["id"]
    e.p = {x.external_id: str(x.id) for x in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == world["org_a"].id))}
    return e


def rid(e):
    e.n += 1
    return f"req-{e.n:08d}"


def new(e, headers=None, expect=201, **over):
    body = {"client_request_id": rid(e), "store_id": str(e.world["store_a1"].id), "location_id": e.loc,
            "supplier_name": "Distribuidora Alfa", **over}
    r = e.client.post(BASE, json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def add(e, rec, ext, received, headers=None, expect=201, **over):
    body = {"client_request_id": rid(e), "product_id": e.p[ext], "received_quantity": str(received), **over}
    r = e.client.post(f"{BASE}/{rec['id']}/items", json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def finalize(e, rec, expect=200, headers=None, **body):
    r = e.client.post(f"{BASE}/{rec['id']}/finalize", json=body, headers=headers or e.manager)
    assert r.status_code == expect, r.text
    return r.json()


# ------------------------------------------------------------ cabeçalho
def test_create_draft_idempotent_and_roles(env):
    rec = new(env, client_request_id="req-fixo-0001")
    assert rec["status"] == "draft" and rec["erp_application_status"] == "not_applied"
    again = new(env, expect=200, client_request_id="req-fixo-0001")
    assert again["id"] == rec["id"]
    new(env, expect=409, client_request_id="req-fixo-0001", supplier_name="Outro fornecedor")
    new(env, headers=env.aud, expect=403)
    assert env.client.post(BASE, json={}, headers=env.op).status_code == 422
    assert env.client.post(BASE, json={}).status_code == 401


def test_access_key_validation_and_duplicates(env):
    new(env, expect=422, access_key="1" * 44)                       # DV errado
    new(env, expect=422, access_key="123")                          # curta
    rec = new(env, access_key=KEY1)
    new(env, expect=409, access_key=KEY1)                           # NF-e já recebida
    new(env, access_key=KEY2)                                       # outra nota
    assert env.client.post(f"{BASE}/{rec['id']}/cancel", json={"reason": "lançada por engano"},
                           headers=env.manager).status_code == 200
    new(env, access_key=KEY1)                                       # liberada após cancelar
    # chave com espaços é normalizada
    new(env, expect=409, access_key=" ".join([KEY2[:11], KEY2[11:]]))


def test_supplier_document_series_number_duplicates(env):
    base = {"supplier_document": "11.222.333/0001-81", "document_number": "123", "document_series": "1"}
    first = new(env, **base)
    assert first["supplier_document"] == "11222333000181"
    new(env, expect=409, **base)
    new(env, **{**base, "document_series": "2"})                    # outra série
    new(env, **{**base, "supplier_document": "99888777000166"})     # outro fornecedor
    new(env, expect=422, supplier_document="123")
    new(env, document_number="777")                                 # sem documento do fornecedor: sem trava
    new(env, document_number="777")


def test_location_and_tenant_validation(env, db):
    new(env, expect=404, location_id="00000000-0000-0000-0000-000000000000")
    other = StockLocation(organization_id=env.world["org_b"].id, store_id=env.world["store_b1"].id,
                          code="Z", name="Z")
    db.add(other)
    db.commit()
    new(env, expect=404, location_id=str(other.id))
    new(env, expect=404, store_id=str(env.world["store_b1"].id), location_id=str(other.id))
    a2 = env.client.post(f"/api/v1/stores/{env.world['store_a2'].id}/locations",
                         json={"code": "D2", "name": "D2"}, headers=env.admin).json()
    new(env, expect=404, location_id=a2["id"])                      # local de outra loja
    env.client.patch(f"/api/v1/locations/{env.loc}", json={"is_active": False}, headers=env.admin)
    new(env, expect=409)                                            # local inativo


# ------------------------------------------------------------ itens
def test_item_defaults_and_computed_shortage_excess(env):
    rec = new(env)
    a = add(env, rec, "1001", 90, expected_quantity=100)
    assert Decimal(a["accepted_quantity"]) == 90 and Decimal(a["shortage_quantity"]) == 10
    assert Decimal(a["excess_quantity"]) == 0
    b = add(env, rec, "1002", 55, expected_quantity=50)
    assert Decimal(b["excess_quantity"]) == 5 and Decimal(b["shortage_quantity"]) == 0
    c = add(env, rec, "2002", 4)
    assert c["shortage_quantity"] is None and c["excess_quantity"] is None  # sem esperado: nada a calcular
    detail = env.client.get(f"{BASE}/{rec['id']}", headers=env.aud).json()
    assert len(detail["items"]) == 3


def test_items_carry_the_product_name(env):
    rec = new(env)
    item = add(env, rec, "1001", 1)
    assert item["product_name"]
    detail = env.client.get(f"{BASE}/{rec['id']}", headers=env.aud).json()
    assert detail["items"][0]["product_name"] == item["product_name"]
    changed = env.client.patch(f"{BASE}/{rec['id']}/items/{item['id']}", headers=env.op,
                               json={"received_quantity": "2"})
    assert changed.json()["product_name"] == item["product_name"]


def test_accepted_plus_rejected_must_equal_received_and_reason_rules(env):
    rec = new(env)
    add(env, rec, "1001", 10, accepted_quantity=8, expect=422)                       # 8+0 != 10
    add(env, rec, "1001", 10, rejected_quantity=3, expect=422)                       # sem motivo
    add(env, rec, "1001", 10, rejection_reason="damaged", expect=422)                # motivo sem rejeição
    add(env, rec, "1001", 10, rejected_quantity=11, rejection_reason="damaged", expect=422)
    ok = add(env, rec, "1001", 10, rejected_quantity=3, rejection_reason="damaged")
    assert Decimal(ok["accepted_quantity"]) == 7 and ok["rejection_reason"] == "damaged"
    add(env, rec, "1002", 10, accepted_quantity=6, rejected_quantity=4, rejection_reason="quality")
    add(env, rec, "2002", 1, rejection_reason="nao_existe", expect=422)


def test_expired_product_cannot_be_accepted(env):
    rec = new(env)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    add(env, rec, "1001", 6, expiry_date=yesterday, lot="L1", expect=422)
    ok = add(env, rec, "1001", 6, expiry_date=yesterday, lot="L1", rejected_quantity=6,
             rejection_reason="expired")
    assert Decimal(ok["accepted_quantity"]) == 0
    add(env, rec, "1002", 6, expiry_date=(date.today() + timedelta(days=30)).isoformat(), lot="L9")


def test_unit_fraction_rules(env):
    rec = new(env)
    add(env, rec, "1001", "2.5", expect=422)       # UN
    add(env, rec, "1001", 3, expected_quantity="3.5", expect=422)
    add(env, rec, "2001", "12.750")                # KG
    add(env, rec, "2002", "1.2345", expect=422)    # >3 casas
    add(env, rec, "2002", "-1", expect=422)


def test_duplicate_product_lot_and_idempotent_item(env):
    rec = new(env)
    first = add(env, rec, "1001", 5, client_request_id="item-fixo-01", lot="A")
    again = add(env, rec, "1001", 5, client_request_id="item-fixo-01", lot="A", expect=200)
    assert again["id"] == first["id"]
    add(env, rec, "1001", 6, client_request_id="item-fixo-01", lot="A", expect=409)   # id reutilizado
    add(env, rec, "1001", 5, lot="A", expect=409)                                    # mesmo produto+lote
    add(env, rec, "1001", 5, lot="B")                                                # outro lote
    add(env, rec, "1002", 5)
    add(env, rec, "1002", 5, expect=409)                                             # sem lote/validade
    assert len(env.client.get(f"{BASE}/{rec['id']}", headers=env.op).json()["items"]) == 3


def test_item_product_must_be_active_and_same_tenant(env, db):
    rec = new(env)
    env.client.post("/api/v1/catalog/sync", headers=env.admin_b)
    pb = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == env.world["org_b"].id))
    r = env.client.post(f"{BASE}/{rec['id']}/items", headers=env.op, json={
        "client_request_id": rid(env), "product_id": str(pb), "received_quantity": 1})
    assert r.status_code == 404
    db.get(ProductReference, __import__("uuid").UUID(env.p["1002"])).is_active = False
    db.commit()
    add(env, rec, "1002", 1, expect=404)


def test_update_and_delete_item(env):
    rec = new(env)
    it = add(env, rec, "1001", 10, expected_quantity=10)
    url = f"{BASE}/{rec['id']}/items/{it['id']}"
    r = env.client.patch(url, json={"received_quantity": 8}, headers=env.op)   # aceito acompanha
    assert r.status_code == 200 and Decimal(r.json()["accepted_quantity"]) == 8
    assert Decimal(r.json()["shortage_quantity"]) == 2
    r = env.client.patch(url, json={"rejected_quantity": 3, "rejection_reason": "damaged"}, headers=env.op)
    assert r.status_code == 200 and Decimal(r.json()["accepted_quantity"]) == 5
    assert env.client.patch(url, json={"rejected_quantity": 3, "rejection_reason": None},
                            headers=env.op).status_code == 422
    assert env.client.patch(url, json={"rejected_quantity": 9}, headers=env.op).status_code == 422
    assert env.client.patch(url, json={"rejected_quantity": 0}, headers=env.op).status_code == 200
    assert env.client.patch(url, json={"received_quantity": 2.5}, headers=env.op).status_code == 422
    assert env.client.patch(url, json={"received_quantity": 1}, headers=env.aud).status_code == 403
    assert env.client.delete(url, headers=env.aud).status_code == 403
    assert env.client.delete(url, headers=env.op).status_code == 204
    assert env.client.delete(url, headers=env.op).status_code == 404


# ------------------------------------------------------------ finalização
def test_finalize_rules_and_immutability(env):
    rec = new(env)
    assert "não tem itens" in env.client.post(f"{BASE}/{rec['id']}/finalize", json={},
                                              headers=env.manager).json()["detail"]
    it = add(env, rec, "1001", 0, expected_quantity=10)              # nada chegou
    assert "Nenhuma quantidade" in env.client.post(f"{BASE}/{rec['id']}/finalize", json={},
                                                   headers=env.manager).json()["detail"]
    add(env, rec, "1002", 5)
    finalize(env, rec, expect=403, headers=env.op)
    done = finalize(env, rec, partial=True)
    assert done["status"] == "finalized" and done["is_partial"] is True
    assert done["erp_application_status"] == "not_applied" and done["finalized_by"]
    assert env.client.patch(f"{BASE}/{rec['id']}/items/{it['id']}", json={"received_quantity": 1},
                            headers=env.manager).status_code == 409
    add(env, rec, "2002", 1, expect=409)
    assert env.client.delete(f"{BASE}/{rec['id']}/items/{it['id']}", headers=env.manager).status_code == 409
    finalize(env, rec, expect=409)
    r = env.client.post(f"{BASE}/{rec['id']}/cancel", json={"reason": "tentativa de desfazer"},
                        headers=env.manager)
    assert r.status_code == 409 and "compensatório" in r.json()["detail"]


def test_full_receipt_is_not_partial(env):
    rec = new(env)
    add(env, rec, "1001", 10, expected_quantity=10)
    assert finalize(env, rec)["is_partial"] is False


def test_cancel_rules(env):
    rec = new(env)
    add(env, rec, "1001", 1)
    url = f"{BASE}/{rec['id']}/cancel"
    assert env.client.post(url, json={"reason": "ok"}, headers=env.manager).status_code == 422
    assert env.client.post(url, json={"reason": "motivo válido"}, headers=env.op).status_code == 403
    r = env.client.post(url, json={"reason": "motivo válido"}, headers=env.manager)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["cancel_reason"]
    assert env.client.post(url, json={"reason": "motivo válido"}, headers=env.manager).status_code == 409
    add(env, rec, "1002", 1, expect=409)
    finalize(env, rec, expect=409)


# ------------------------------------------------------------ tenant / auditoria
def test_tenant_and_store_isolation(env, db):
    rec = new(env)
    add(env, rec, "1001", 1)
    rid_ = rec["id"]
    for method, url in (("get", f"{BASE}/{rid_}"), ("post", f"{BASE}/{rid_}/finalize"),
                        ("post", f"{BASE}/{rid_}/cancel")):
        r = getattr(env.client, method)(url, headers=env.admin_b, **({"json": {"reason": "xxxxx"}}
                                                                    if method == "post" else {}))
        assert r.status_code == 404, url
    assert env.client.get(BASE, headers=env.admin_b).json() == []
    assert [x["id"] for x in env.client.get(BASE, headers=env.aud).json()] == [rid_]
    make_user(db, env.world["org_a"], "op2loja@a.com", Role.OPERATOR, env.world["store_a2"])
    other = auth(env.client, "op2loja@a.com")
    assert env.client.get(f"{BASE}/{rid_}", headers=other).status_code == 404
    assert env.client.get(BASE, headers=other).json() == []
    assert env.client.get(BASE, params={"status": "draft"}, headers=env.manager).json()[0]["id"] == rid_


def test_audit_trail_and_no_erp_write(env, db):
    rec = new(env)
    it = add(env, rec, "1001", 5)
    env.client.patch(f"{BASE}/{rec['id']}/items/{it['id']}", json={"received_quantity": 4}, headers=env.op)
    finalize(env, rec)
    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))
               if e.action.startswith("receiving.")]
    assert actions == ["receiving.created", "receiving.item_added", "receiving.item_updated",
                       "receiving.finalized"]
    fin = db.scalars(select(AuditEvent).where(AuditEvent.action == "receiving.finalized")).one()
    assert fin.details["erp_application"] == "not_applied"
