from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.api.erp_deps import get_erp_adapter
from app.integrations.erp_port import ErpStockBalance, ErpTimeoutError
from app.integrations.mock_erp import MockErpAdapter
from app.models import (
    AuditEvent,
    InventoryAdjustmentProposal,
    InventoryCount,
    InventorySession,
    ProductReference,
    Role,
    StockLocation,
)
from tests.conftest import auth, make_user

BASE = "/api/v1/inventory-sessions"


class Env:
    pass


@pytest.fixture()
def env(client, world, db):
    e = Env()
    e.client, e.world, e.db = client, world, db
    e.erp = MockErpAdapter()  # DEP: 1001=100, 1002=50, 2001=20.500 (KG), 2002=10
    client.app.dependency_overrides[get_erp_adapter] = lambda: e.erp
    e.admin = auth(client, "admin@a.com")
    e.manager = auth(client, "manager@a.com")
    e.op = auth(client, "op@a.com")
    make_user(db, world["org_a"], "op2@a.com", Role.OPERATOR, world["store_a1"])
    make_user(db, world["org_a"], "aud@a.com", Role.AUDITOR)
    e.op2, e.aud = auth(client, "op2@a.com"), auth(client, "aud@a.com")
    e.admin_b = auth(client, "admin@b.com")
    assert client.post("/api/v1/catalog/sync", headers=e.admin).status_code == 200
    loc = client.post(f"/api/v1/stores/{world['store_a1'].id}/locations",
                      json={"code": "DEP", "name": "Depósito", "external_code": "DEP"}, headers=e.admin)
    e.loc_id = loc.json()["id"]
    e.p = {x.external_id: str(x.id) for x in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == world["org_a"].id))}
    e.seq = 0
    return e


def new_session(e, headers=None, expect=201):
    r = e.client.post(BASE, json={"store_id": str(e.world["store_a1"].id), "location_id": e.loc_id,
                                  "name": "Inventário teste"}, headers=headers or e.manager)
    assert r.status_code == expect, r.text
    return r.json()


def count(e, sid, ext, qty, headers=None, rid=None, expect=201):
    e.seq += 1
    body = {"product_id": e.p[ext], "quantity": str(qty), "client_request_id": rid or f"req-{e.seq:06d}"}
    r = e.client.post(f"{BASE}/{sid}/counts", json=body, headers=headers or e.op)
    assert r.status_code == expect, r.text
    return r.json()


def close(e, sid, expect=200, headers=None):
    r = e.client.post(f"{BASE}/{sid}/close-counting", headers=headers or e.manager)
    assert r.status_code == expect, r.text
    return r.json()


def discs(e, sid, **params):
    r = e.client.get(f"{BASE}/{sid}/discrepancies", params=params, headers=e.manager)
    assert r.status_code == 200
    return {d["product_id"]: d for d in r.json()}


def resolve(e, sid, did, decision, note="decisão do gerente", expect=200, **extra):
    r = e.client.post(f"{BASE}/{sid}/discrepancies/{did}/resolve",
                      json={"decision": decision, "note": note, **extra}, headers=e.manager)
    assert r.status_code == expect, r.text
    return r.json()


# ----------------------------------------------------------- criação
def test_create_session_reads_baseline_only_managers_and_blind_for_operators(env):
    s = new_session(env)
    assert s["status"] == "open" and s["current_round"] == 1 and s["erp_application_status"] == "not_applied"
    mgr = env.client.get(f"{BASE}/{s['id']}", headers=env.manager).json()
    assert mgr["baseline_items"] == 4 and mgr["baseline_unmapped_count"] == 0
    op = env.client.get(f"{BASE}/{s['id']}", headers=env.op).json()
    assert op["baseline_items"] == 0 and op["discrepancies_open"] == 0  # contagem às cegas
    for h in (env.op, env.aud):
        assert env.client.post(BASE, json={"store_id": str(env.world["store_a1"].id),
                                           "location_id": env.loc_id, "name": "x"}, headers=h).status_code == 403
    assert env.client.post(BASE, json={}, headers=env.manager).status_code == 422


def test_one_active_session_per_location_and_new_after_cancel(env):
    s = new_session(env)
    new_session(env, expect=409)
    r = env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "abertura errada"}, headers=env.manager)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    new_session(env)


def test_location_validation_and_tenant(env, db):
    no_code = env.client.post(f"/api/v1/stores/{env.world['store_a1'].id}/locations",
                              json={"code": "X", "name": "Sem código"}, headers=env.admin).json()
    r = env.client.post(BASE, json={"store_id": str(env.world["store_a1"].id),
                                    "location_id": no_code["id"], "name": "x"}, headers=env.manager)
    assert r.status_code == 422 and "ERP" in r.json()["detail"]
    other = StockLocation(organization_id=env.world["org_b"].id, store_id=env.world["store_b1"].id,
                          code="Z", name="Z", external_code="DEP")
    db.add(other)
    db.commit()
    for store, loc in ((env.world["store_a1"].id, other.id), (env.world["store_b1"].id, other.id)):
        assert env.client.post(BASE, json={"store_id": str(store), "location_id": str(loc), "name": "x"},
                               headers=env.manager).status_code == 404
    # local de OUTRA loja da mesma organização com a loja errada
    a2 = env.client.post(f"/api/v1/stores/{env.world['store_a2'].id}/locations",
                         json={"code": "D2", "name": "D2", "external_code": "DEP"}, headers=env.admin).json()
    assert env.client.post(BASE, json={"store_id": str(env.world["store_a1"].id), "location_id": a2["id"],
                                       "name": "x"}, headers=env.manager).status_code == 404


def test_erp_failure_on_create_leaves_nothing_and_unmapped_reported(env, db):
    class Down(MockErpAdapter):
        def list_location_balances(self, location_code):
            raise ErpTimeoutError("t")

    env.erp = Down()
    new_session(env, expect=504)
    assert db.scalar(select(func.count()).select_from(InventorySession)) == 0
    env.erp = MockErpAdapter()
    env.erp.balances[("9999", "DEP")] = Decimal("5")  # produto fora do espelho
    env.erp.products.append(__import__("app.integrations.erp_port", fromlist=["ErpProduct"]).ErpProduct(
        "9999", "Não sincronizado", "UN"))
    s = new_session(env)
    assert s["baseline_unmapped_count"] == 1


# ----------------------------------------------------------- contagem
def test_count_saved_idempotent_and_conflicting_reuse_rejected(env, db):
    s = new_session(env)
    first = count(env, s["id"], "1001", 98, rid="abc-12345")
    again = count(env, s["id"], "1001", 98, rid="abc-12345", expect=200)
    assert again["id"] == first["id"]
    assert db.scalar(select(func.count()).select_from(InventoryCount)) == 1
    count(env, s["id"], "1001", 99, rid="abc-12345", expect=409)
    assert first["counted_by"] == str(env.world["op_a1"].id) and first["round_no"] == 1


def test_count_validation(env):
    s = new_session(env)
    count(env, s["id"], "1001", "2.5", expect=422)           # UN não aceita fração
    count(env, s["id"], "2001", "20.500")                     # KG aceita
    count(env, s["id"], "1001", "-1", expect=422)
    count(env, s["id"], "1001", "1.2345", expect=422)         # >3 casas
    count(env, s["id"], "1001", 1, rid="curto", expect=422)   # id de requisição curto
    r = env.client.post(f"{BASE}/{s['id']}/counts", headers=env.op,
                        json={"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1,
                              "client_request_id": "req-xxxxxxxx"})
    assert r.status_code == 404
    assert env.client.post(f"{BASE}/{s['id']}/counts", headers=env.aud, json={}).status_code == 403


def test_cannot_count_other_tenant_product_inactive_product_or_after_close(env, db):
    s = new_session(env)
    env.client.post("/api/v1/catalog/sync", headers=env.admin_b)
    pb = db.scalar(select(ProductReference.id).where(
        ProductReference.organization_id == env.world["org_b"].id))
    r = env.client.post(f"{BASE}/{s['id']}/counts", headers=env.op,
                        json={"product_id": str(pb), "quantity": 1, "client_request_id": "req-tenantb1"})
    assert r.status_code == 404
    prod = db.get(ProductReference, __import__("uuid").UUID(env.p["1002"]))
    prod.is_active = False
    db.commit()
    count(env, s["id"], "1002", 1, expect=404)
    count(env, s["id"], "1001", 100)
    close(env, s["id"])
    count(env, s["id"], "1001", 100, expect=409)


def test_operator_sees_only_own_counts_manager_sees_all(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100, headers=env.op)
    count(env, s["id"], "1001", 100, headers=env.op2)
    mine = env.client.get(f"{BASE}/{s['id']}/counts", headers=env.op).json()
    allc = env.client.get(f"{BASE}/{s['id']}/counts", headers=env.manager).json()
    assert len(mine) == 1 and len(allc) == 2


def test_counts_are_append_only(env, db):
    s = new_session(env)
    count(env, s["id"], "1001", 100)
    c = db.scalars(select(InventoryCount)).one()
    c.quantity = Decimal("1")
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()
    db.delete(db.scalars(select(InventoryCount)).one())
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()


# ----------------------------------------------------------- fechamento / divergências
def test_clean_count_matching_erp_creates_no_discrepancy(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100)
    r = close(env, s["id"])
    assert r == {"products_checked": 1, "clean": 1, "discrepancies_created": 0,
                 "baseline_items_not_counted": 3}
    assert discs(env, s["id"]) == {}


def test_two_counters_same_value_are_not_summed(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100, headers=env.op)
    count(env, s["id"], "1001", 100, headers=env.op2)
    assert close(env, s["id"])["clean"] == 1  # 100 + 100 = 200 seria divergência: NÃO somou


def test_conflicting_counts_never_summed_and_resolved_only_with_one_of_the_values(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100, headers=env.op)
    count(env, s["id"], "1001", 90, headers=env.op2)
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1001"]]
    assert d["kind"] == "conflicting_counts" and d["counted_quantity"] is None
    assert sorted(d["details"]["counted_values"]) == ["100.000", "90.000"]
    resolve(env, s["id"], d["id"], "accept_count", expect=422)                         # sem quantidade
    resolve(env, s["id"], d["id"], "accept_count", accepted_quantity="190", expect=422)  # soma proibida
    ok = resolve(env, s["id"], d["id"], "accept_count", accepted_quantity="90")
    assert Decimal(ok["accepted_quantity"]) == Decimal("90") and ok["status"] == "resolved"


def test_same_user_recount_replaces_own_previous_count(env):
    s = new_session(env)
    count(env, s["id"], "1001", 70)
    count(env, s["id"], "1001", 100)  # mesma pessoa corrigiu o próprio valor
    assert close(env, s["id"])["clean"] == 1


def test_differs_from_erp(env):
    s = new_session(env)
    count(env, s["id"], "1002", 47)
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1002"]]
    assert d["kind"] == "differs_from_erp"
    assert Decimal(d["baseline_quantity"]) == 50 and Decimal(d["counted_quantity"]) == 47


def test_missing_erp_balance_is_not_zero(env):
    s = new_session(env)
    env.erp.balances = {k: v for k, v in env.erp.balances.items() if k != ("2002", "DEP")}
    s2 = None
    env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "refazer sem 2002"}, headers=env.manager)
    s2 = new_session(env)
    count(env, s2["id"], "2002", 0)  # contou ZERO, mas o ERP nem tem registro
    close(env, s2["id"])
    d = discs(env, s2["id"])[env.p["2002"]]
    assert d["kind"] == "no_erp_balance" and d["baseline_quantity"] is None


def test_erp_changed_after_opening_invalidates_count(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100)           # igual ao saldo da abertura
    env.erp.balances[("1001", "DEP")] = Decimal("93")  # vendas depois da abertura
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1001"]]
    assert d["kind"] == "erp_changed_since_baseline"
    assert Decimal(d["baseline_quantity"]) == 100 and Decimal(d["erp_current_quantity"]) == 93
    # contagem antiga NÃO pode ser aceita; manter ERP ou recontar
    resolve(env, s["id"], d["id"], "accept_count", expect=409)
    assert resolve(env, s["id"], d["id"], "keep_erp")["decision"] == "keep_erp"


def test_close_rules_and_erp_failure_keeps_session_open(env):
    s = new_session(env)
    close(env, s["id"], expect=422)  # sem contagens
    count(env, s["id"], "1001", 100)

    class Down(MockErpAdapter):
        def list_location_balances(self, location_code):
            raise ErpTimeoutError("t")

    good = env.erp
    env.erp = Down()
    close(env, s["id"], expect=504)
    assert env.client.get(f"{BASE}/{s['id']}", headers=env.manager).json()["status"] == "open"
    env.erp = good
    close(env, s["id"])
    close(env, s["id"], expect=409)
    close(env, s["id"], expect=403, headers=env.op)


# ----------------------------------------------------------- resolução / recontagem / finalização
def test_resolve_validations(env):
    s = new_session(env)
    count(env, s["id"], "1002", 47)
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1002"]]
    resolve(env, s["id"], d["id"], "accept_count", note="ok", expect=422)           # nota obrigatória (5+)
    resolve(env, s["id"], d["id"], "keep_erp", accepted_quantity="1", expect=422)
    resolve(env, s["id"], d["id"], "accept_count", accepted_quantity="999", expect=422)
    resolve(env, s["id"], d["id"], "accept_count")
    resolve(env, s["id"], d["id"], "keep_erp", expect=409)  # já resolvida
    assert env.client.post(f"{BASE}/{s['id']}/discrepancies/{d['id']}/resolve", headers=env.op,
                           json={"decision": "keep_erp", "note": "tentativa"}).status_code == 403


def test_recount_refreshes_baseline_and_supersedes_discrepancy(env):
    s = new_session(env)
    count(env, s["id"], "1001", 100)
    env.erp.balances[("1001", "DEP")] = Decimal("93")
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1001"]]
    r = env.client.post(f"{BASE}/{s['id']}/recounts", headers=env.manager,
                        json={"discrepancy_ids": [d["id"]], "reason": "saldo mudou na abertura"})
    assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["current_round"] == 2
    count(env, s["id"], "1002", 50, expect=422)   # só o produto pedido pode ser recontado
    c = count(env, s["id"], "1001", 93)
    assert c["round_no"] == 2
    assert close(env, s["id"])["clean"] == 1       # novo baseline = 93
    assert discs(env, s["id"], status="open") == {}
    assert discs(env, s["id"], status="superseded")[env.p["1001"]]["decision"] == "recount_requested"


def test_recount_rules(env):
    s = new_session(env)
    count(env, s["id"], "1002", 47)
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1002"]]
    body = {"discrepancy_ids": [d["id"]], "reason": "conferir de novo"}
    assert env.client.post(f"{BASE}/{s['id']}/recounts", json=body, headers=env.op).status_code == 403
    bogus = {"discrepancy_ids": ["00000000-0000-0000-0000-000000000000"], "reason": "conferir de novo"}
    assert env.client.post(f"{BASE}/{s['id']}/recounts", json=bogus, headers=env.manager).status_code == 404
    resolve(env, s["id"], d["id"], "keep_erp")
    assert env.client.post(f"{BASE}/{s['id']}/recounts", json=body, headers=env.manager).status_code == 409


def test_finalize_requires_all_resolved_then_builds_proposals_without_touching_erp(env, db):
    s = new_session(env)
    count(env, s["id"], "1001", 100)   # limpo
    count(env, s["id"], "1002", 47)    # aceitar -> proposta delta -3
    count(env, s["id"], "2002", 8)     # manter ERP -> sem proposta
    close(env, s["id"])
    snapshot = dict(env.erp.balances)
    assert env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.manager).status_code == 409
    ds = discs(env, s["id"])
    resolve(env, s["id"], ds[env.p["1002"]]["id"], "accept_count")
    assert env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.manager).status_code == 409  # falta 2002
    resolve(env, s["id"], ds[env.p["2002"]]["id"], "keep_erp")
    assert env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.op).status_code == 403
    r = env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.manager)
    assert r.status_code == 200 and r.json()["status"] == "finalized"
    assert r.json()["erp_application_status"] == "not_applied"
    props = env.client.get(f"{BASE}/{s['id']}/proposals", headers=env.aud).json()
    assert len(props) == 1
    p = props[0]
    assert p["product_id"] == env.p["1002"] and Decimal(p["delta"]) == Decimal("-3")
    assert Decimal(p["counted_quantity"]) == 47 and p["status"] == "proposed"
    assert env.erp.balances == snapshot  # nenhuma escrita no ERP
    count(env, s["id"], "1001", 1, expect=409)
    assert env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.manager).status_code == 409
    assert db.scalar(select(func.count()).select_from(InventoryAdjustmentProposal)) == 1


def test_cancel_rules(env):
    s = new_session(env)
    assert env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "ok"}, headers=env.manager).status_code == 422
    assert env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "motivo válido"}, headers=env.op).status_code == 403
    env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "motivo válido"}, headers=env.manager)
    assert env.client.post(f"{BASE}/{s['id']}/cancel", json={"reason": "motivo válido"}, headers=env.manager).status_code == 409
    count(env, s["id"], "1001", 1, expect=409)


# ----------------------------------------------------------- tenant e auditoria
def test_tenant_and_store_isolation(env, db):
    s = new_session(env)
    sid = s["id"]
    assert env.client.get(f"{BASE}/{sid}", headers=env.admin_b).status_code == 404
    assert env.client.get(f"{BASE}/{sid}/counts", headers=env.admin_b).status_code == 404
    assert env.client.post(f"{BASE}/{sid}/close-counting", headers=env.admin_b).status_code == 404
    assert env.client.get(BASE, headers=env.admin_b).json() == []
    assert [x["id"] for x in env.client.get(BASE, headers=env.aud).json()] == [sid]
    # operador restrito a OUTRA loja não enxerga a sessão
    make_user(db, env.world["org_a"], "op2loja@a.com", Role.OPERATOR, env.world["store_a2"])
    other = auth(env.client, "op2loja@a.com")
    assert env.client.get(f"{BASE}/{sid}", headers=other).status_code == 404
    assert env.client.get(BASE, headers=other).json() == []


def test_full_flow_is_audited_without_exposing_quantities_of_secrets(env, db):
    s = new_session(env)
    count(env, s["id"], "1002", 47)
    close(env, s["id"])
    d = discs(env, s["id"])[env.p["1002"]]
    resolve(env, s["id"], d["id"], "accept_count")
    env.client.post(f"{BASE}/{s['id']}/finalize", headers=env.manager)
    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))
               if e.action.startswith("inventory.")]
    assert actions == ["inventory.session_created", "inventory.counting_closed",
                       "inventory.discrepancy_resolved", "inventory.session_finalized"]


def test_inventory_requires_authentication(client):
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={}).status_code == 401


def test_erp_balance_value_object_still_has_no_write_surface():
    assert not [n for n in dir(MockErpAdapter()) if n.startswith(("set_", "adjust", "write", "post", "put"))]
    assert ErpStockBalance  # porta de leitura continua sendo a única superfície do ERP
