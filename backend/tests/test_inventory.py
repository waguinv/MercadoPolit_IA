from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.erp_deps import get_erp_adapter
from app.integrations.erp_port import ErpTimeoutError
from app.integrations.mock_erp import MockErpAdapter
from app.models import AuditEvent, InventoryCount, ProductReference, Role, UnitReference
from tests.conftest import auth, make_user

BASE = "/api/v1/inventory-sessions"


class StubErp(MockErpAdapter):
    """Mock que permite controlar a última alteração no ERP e falhar a pedido; registra as chamadas."""

    def __init__(self, last_changed_at=None, error=None):
        super().__init__()
        self.last_changed_at = last_changed_at
        self.error = error
        self.calls = 0

    def get_stock_balance(self, product_external_id, location_code=None):
        self.calls += 1
        if self.error:
            raise self.error
        return replace(super().get_stock_balance(product_external_id, location_code),
                       last_changed_at=self.last_changed_at)


class Scenario:
    """Organização A com catálogo sincronizado, local mapeado no ERP e cabeçalhos por papel."""

    def __init__(self, client, world, db):
        self.client, self.world, self.db = client, world, db
        make_user(db, world["org_a"], "op2@a.com", Role.OPERATOR, world["store_a1"])
        make_user(db, world["org_a"], "auditor@a.com", Role.AUDITOR)
        make_user(db, world["org_a"], "opa2@a.com", Role.OPERATOR, world["store_a2"])
        self.admin = auth(client, "admin@a.com")
        self.manager = auth(client, "manager@a.com")
        self.op = auth(client, "op@a.com")
        self.op2 = auth(client, "op2@a.com")
        self.auditor = auth(client, "auditor@a.com")
        client.app.dependency_overrides[get_erp_adapter] = lambda: MockErpAdapter()
        assert client.post("/api/v1/catalog/sync", headers=self.admin).status_code == 200
        self.loc = client.post(f"/api/v1/stores/{world['store_a1'].id}/locations",
                               json={"code": "DEP", "name": "Depósito", "external_code": "DEP"},
                               headers=self.admin).json()
        self.un = self.product("1001")      # unidade UN (sem fração)
        self.kg = self.product("2001")      # unidade KG (com fração)

    def product(self, external_id):
        return self.db.scalar(select(ProductReference).where(
            ProductReference.external_id == external_id,
            ProductReference.organization_id == self.world["org_a"].id))

    def unit(self, code):
        return self.db.scalar(select(UnitReference).where(
            UnitReference.code == code, UnitReference.organization_id == self.world["org_a"].id))

    def session(self, headers=None, **extra):
        r = self.client.post(BASE, json={"location_id": self.loc["id"], "name": "Inv", **extra},
                             headers=headers or self.manager)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def count(self, sid, headers, qty="10", product=None, unit_id=None, key=None):
        product = product or self.un
        return self.client.post(f"{BASE}/{sid}/counts", headers=headers, json={
            "product_id": str(product.id), "unit_id": str(unit_id or product.unit_id),
            "quantity": qty, "client_key": key or uuid4().hex})

    def item(self, sid, product=None):
        product = product or self.un
        rows = self.client.get(f"{BASE}/{sid}/items", headers=self.manager).json()
        return next(i for i in rows if i["product_id"] == str(product.id))

    def post(self, path, body=None, headers=None):
        return self.client.post(f"{BASE}/{path}", json=body, headers=headers or self.manager)

    def use_erp(self, erp):
        self.client.app.dependency_overrides[get_erp_adapter] = lambda: erp


@pytest.fixture()
def sc(client, world, db):
    return Scenario(client, world, db)


# ---------- sessões ----------
def test_create_session_derives_store_from_location_and_audits(sc, db):
    sid = sc.session(sector=" Mercearia ")
    s = sc.client.get(f"{BASE}/{sid}", headers=sc.manager).json()
    assert s["store_id"] == str(sc.world["store_a1"].id)
    assert s["status"] == "open" and s["sector"] == "Mercearia" and s["items_total"] == 0
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.session_created")).one()


@pytest.mark.parametrize("first,second,expected", [
    (None, None, 409), (None, "A", 409), ("A", None, 409), ("A", "a", 409), ("A", "B", 201),
])
def test_overlapping_active_sessions_conflict(sc, first, second, expected):
    sc.session(sector=first)
    r = sc.client.post(BASE, json={"location_id": sc.loc["id"], "name": "2", "sector": second},
                       headers=sc.manager)
    assert r.status_code == expected


def test_cancelled_session_frees_location(sc):
    sid = sc.session()
    assert sc.post(f"{sid}/cancel", {"reason": "aberta por engano"}).status_code == 200
    sc.session()


def test_inactive_location_rejected(sc):
    sc.client.patch(f"/api/v1/locations/{sc.loc['id']}", json={"is_active": False}, headers=sc.admin)
    r = sc.client.post(BASE, json={"location_id": sc.loc["id"], "name": "x"}, headers=sc.manager)
    assert r.status_code == 422


def test_only_managers_create_sessions(sc):
    body = {"location_id": sc.loc["id"], "name": "x"}
    assert sc.client.post(BASE, json=body, headers=sc.op).status_code == 403
    assert sc.client.post(BASE, json=body, headers=sc.auditor).status_code == 403
    assert sc.client.post(BASE, json=body).status_code == 401


# ---------- isolamento ----------
def test_other_tenant_gets_404_everywhere(sc):
    hb = auth(sc.client, "admin@b.com")
    sid = sc.session()
    sc.count(sid, sc.op)
    item_id = sc.item(sid)["id"]
    assert sc.client.get(f"{BASE}/{sid}", headers=hb).status_code == 404
    assert sc.client.get(f"{BASE}/{sid}/items", headers=hb).status_code == 404
    assert sc.client.get(f"{BASE}/{sid}/items/{item_id}", headers=hb).status_code == 404
    assert sc.client.get(BASE, headers=hb).json() == []
    assert sc.count(sid, hb).status_code == 404
    assert sc.post(f"{sid}/close", headers=hb).status_code == 404
    assert sc.post(f"{sid}/items/{item_id}/erp-check", headers=hb).status_code == 404
    assert sc.client.post(BASE, json={"location_id": sc.loc["id"], "name": "x"}, headers=hb).status_code == 404


def test_cannot_count_other_tenant_product_or_unit(sc, db):
    hb = auth(sc.client, "admin@b.com")
    sc.client.post("/api/v1/catalog/sync", headers=hb)
    loc_b = sc.client.post(f"/api/v1/stores/{sc.world['store_b1'].id}/locations",
                           json={"code": "D", "name": "D"}, headers=hb).json()
    sid_b = sc.client.post(BASE, json={"location_id": loc_b["id"], "name": "b"}, headers=hb).json()["id"]
    assert sc.count(sid_b, hb, product=sc.un).status_code == 422  # produto da org A
    own = db.scalar(select(ProductReference).where(
        ProductReference.organization_id == sc.world["org_b"].id, ProductReference.external_id == "1001"))
    assert sc.count(sid_b, hb, product=own, unit_id=sc.un.unit_id).status_code == 422  # unidade da org A


def test_store_restricted_user_cannot_reach_other_store(sc):
    sid = sc.session()
    hx = auth(sc.client, "opa2@a.com")
    assert sc.client.get(f"{BASE}/{sid}", headers=hx).status_code == 404
    assert sc.count(sid, hx).status_code == 404
    assert sc.client.get(BASE, headers=hx).json() == []
    assert len(sc.client.get(BASE, headers=sc.op).json()) == 1


# ---------- contagem ----------
def test_single_count_is_accepted_and_audited(sc, db):
    sid = sc.session()
    r = sc.count(sid, sc.op, qty="12")
    assert r.status_code == 201
    it = sc.item(sid)
    assert it["status"] == "counted" and Decimal(it["accepted_quantity"]) == 12
    assert it["accepted_count_id"] == r.json()["id"]
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.count_recorded")).one()
    assert Decimal(ev.details["quantity"]) == 12


def test_same_client_key_does_not_duplicate(sc, db):
    sid = sc.session()
    first = sc.count(sid, sc.op, key="chave-repetida-1")
    again = sc.count(sid, sc.op, key="chave-repetida-1")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(db.scalars(select(InventoryCount)).all()) == 1
    assert sc.item(sid)["status"] == "counted"


def test_same_client_key_with_other_payload_conflicts(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="10", key="chave-repetida-2")
    assert sc.count(sid, sc.op, qty="11", key="chave-repetida-2").status_code == 409


def test_client_key_is_per_user(sc):
    sid = sc.session()
    a = sc.count(sid, sc.op, key="mesma-chave-1")
    b = sc.count(sid, sc.op2, key="mesma-chave-1")
    assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]


def test_retry_after_close_returns_original(sc):
    sid = sc.session()
    first = sc.count(sid, sc.op, key="chave-pos-encerramento")
    sc.post(f"{sid}/submit-review")
    sc.post(f"{sid}/close")
    again = sc.count(sid, sc.op, key="chave-pos-encerramento")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert sc.count(sid, sc.op, key="chave-nova-depois").status_code == 409


def test_agreeing_counts_from_two_people_are_not_summed(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="10")
    sc.count(sid, sc.op2, qty="10.00")
    it = sc.item(sid)
    assert it["status"] == "counted" and Decimal(it["accepted_quantity"]) == 10


def test_conflicting_counts_go_to_review_and_are_not_summed(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="10")
    sc.count(sid, sc.op2, qty="7")
    it = sc.item(sid)
    assert it["status"] == "needs_review" and it["review_reasons"] == ["count_conflict"]
    assert it["accepted_quantity"] is None


def test_same_person_counting_twice_is_duplicate(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="10")
    sc.count(sid, sc.op, qty="10")
    assert sc.item(sid)["review_reasons"] == ["duplicate_count"]


def test_unit_mismatch_is_saved_flagged_and_cannot_be_accepted(sc):
    sid = sc.session()
    r = sc.count(sid, sc.op, qty="10", unit_id=sc.unit("CX").id)
    assert r.status_code == 201 and r.json()["unit_issue"] == "unit_mismatch"
    it = sc.item(sid)
    assert it["status"] == "needs_review" and "unit_mismatch" in it["review_reasons"]
    assert sc.post(f"{sid}/items/{it['id']}/resolve",
                   {"count_id": r.json()["id"], "justification": "aceitar"}).status_code == 409


def test_fraction_only_allowed_when_unit_permits(sc):
    sid = sc.session()
    assert sc.count(sid, sc.op, qty="1.5").json()["unit_issue"] == "fraction_not_allowed"
    assert sc.count(sid, sc.op, qty="1.5", product=sc.kg).json()["unit_issue"] is None


@pytest.mark.parametrize("qty", ["-1", "NaN", "Infinity", "1.23456", "abc"])
def test_invalid_quantities_rejected(sc, qty):
    assert sc.count(sc.session(), sc.op, qty=qty).status_code == 422


def test_zero_is_a_valid_count(sc):
    sid = sc.session()
    assert sc.count(sid, sc.op, qty="0").status_code == 201
    assert Decimal(sc.item(sid)["accepted_quantity"]) == 0


def test_inactive_product_rejected(sc, db):
    sc.un.is_active = False
    db.commit()
    assert sc.count(sc.session(), sc.op).status_code == 422


def test_auditor_cannot_count(sc):
    assert sc.count(sc.session(), sc.auditor).status_code == 403


def test_counting_does_not_call_erp(sc):
    erp = StubErp(error=ErpTimeoutError("fora do ar"))
    sc.use_erp(erp)
    assert sc.count(sc.session(), sc.op).status_code == 201
    assert erp.calls == 0


# ---------- contagem cega e retomada ----------
def test_operator_cannot_see_review_data(sc):
    sid = sc.session()
    sc.count(sid, sc.op)
    item_id = sc.item(sid)["id"]
    assert sc.client.get(f"{BASE}/{sid}/items", headers=sc.op).status_code == 403
    assert sc.client.get(f"{BASE}/{sid}/items/{item_id}", headers=sc.op).status_code == 403
    assert sc.post(f"{sid}/items/{item_id}/erp-check", headers=sc.op).status_code == 403


def test_auditor_reads_review_but_cannot_decide(sc):
    sid = sc.session()
    count = sc.count(sid, sc.op).json()
    item_id = sc.item(sid)["id"]
    assert sc.client.get(f"{BASE}/{sid}/items", headers=sc.auditor).status_code == 200
    assert sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"}, sc.auditor).status_code == 403
    assert sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": count["id"], "justification": "abc"},
                   sc.auditor).status_code == 403
    assert sc.post(f"{sid}/submit-review", headers=sc.auditor).status_code == 403


def test_my_counts_only_shows_own(sc):
    sid = sc.session()
    mine = sc.count(sid, sc.op, qty="10").json()
    sc.count(sid, sc.op2, qty="99")
    rows = sc.client.get(f"{BASE}/{sid}/my-counts", headers=sc.op).json()
    assert [r["id"] for r in rows] == [mine["id"]]


# ---------- atribuição ----------
def test_assignment_restricts_operators_only(sc):
    sid = sc.session()
    assert sc.post(f"{sid}/assignments", {"user_id": str(sc.world["op_a1"].id)}).status_code == 201
    assert sc.count(sid, sc.op).status_code == 201
    assert sc.count(sid, sc.op2).status_code == 403
    assert sc.count(sid, sc.manager, product=sc.kg).status_code == 201
    r = sc.client.delete(f"{BASE}/{sid}/assignments/{sc.world['op_a1'].id}", headers=sc.manager)
    assert r.status_code == 204
    assert sc.count(sid, sc.op2, product=sc.kg, qty="1").status_code == 201


def test_invalid_assignees_rejected(sc):
    sid = sc.session()
    other_store = auth(sc.client, "opa2@a.com")
    me = sc.client.get("/api/v1/auth/me", headers=other_store).json()["id"]
    for uid in (sc.world["admin_b"].id, uuid4(), me):
        assert sc.post(f"{sid}/assignments", {"user_id": str(uid)}).status_code == 422
    assert sc.post(f"{sid}/assignments", {"user_id": str(sc.world["op_a1"].id)}).status_code == 201
    assert sc.post(f"{sid}/assignments", {"user_id": str(sc.world["op_a1"].id)}).status_code == 409


def test_assignable_users_respects_tenant_store_and_role(sc):
    sid = sc.session()
    rows = sc.client.get(f"{BASE}/{sid}/assignable-users", headers=sc.manager).json()
    emails = {r["full_name"] for r in rows}
    assert emails == {"admin", "manager", "op", "op2"}  # sem auditor, outra loja ou outra organização
    assert sc.client.get(f"{BASE}/{sid}/assignable-users", headers=sc.op).status_code == 403
    assert sc.client.get(f"{BASE}/{sid}/assignable-users",
                         headers=auth(sc.client, "admin@b.com")).status_code == 404


def test_review_detail_shows_who_counted(sc):
    sid = sc.session()
    sc.count(sid, sc.op)
    detail = sc.client.get(f"{BASE}/{sid}/items/{sc.item(sid)['id']}", headers=sc.manager).json()
    assert detail["counts"][0]["counted_by_name"] == "op"


# ---------- reconferência ----------
def _conflict(sc, sid):
    a = sc.count(sid, sc.op, qty="10").json()
    b = sc.count(sid, sc.op2, qty="7").json()
    return sc.item(sid)["id"], a, b


def test_recount_opens_new_round_and_clears_conflict(sc):
    sid = sc.session()
    item_id, _, _ = _conflict(sc, sid)
    assert sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "divergência"}).status_code == 201
    it = sc.item(sid)
    assert it["status"] == "recount_requested" and it["current_round"] == 2 and it["review_reasons"] == []
    # O mesmo operador pode recontar sem virar duplicidade: é outra rodada.
    r = sc.count(sid, sc.op, qty="8")
    assert r.json()["round"] == 2
    it = sc.item(sid)
    assert it["status"] == "counted" and Decimal(it["accepted_quantity"]) == 8
    detail = sc.client.get(f"{BASE}/{sid}/items/{item_id}", headers=sc.manager).json()
    assert len(detail["counts"]) == 3 and len(detail["recounts"]) == 1  # histórico preservado


def test_duplicate_recount_request_conflicts(sc):
    sid = sc.session()
    item_id, _, _ = _conflict(sc, sid)
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"})
    assert sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"}).status_code == 409


def test_assigned_recount_only_for_that_operator(sc):
    sid = sc.session()
    item_id, _, _ = _conflict(sc, sid)
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver",
                                "assigned_user_id": str(sc.world["op_a1"].id)})
    assert sc.count(sid, sc.op2, qty="9").status_code == 403
    pending_op = sc.client.get(f"{BASE}/{sid}/pending-recounts", headers=sc.op).json()
    pending_op2 = sc.client.get(f"{BASE}/{sid}/pending-recounts", headers=sc.op2).json()
    assert [p["item_id"] for p in pending_op] == [item_id] and pending_op2 == []
    assert "quantity" not in pending_op[0]
    assert sc.count(sid, sc.op, qty="9").status_code == 201


def test_recount_in_other_session_item_is_404(sc):
    sid = sc.session(sector="A")
    item_id = (sc.count(sid, sc.op), sc.item(sid))[1]["id"]
    other = sc.session(sector="B")
    assert sc.post(f"{other}/recounts", {"item_id": item_id, "reason": "ver"}).status_code == 404


# ---------- decisão ----------
def test_resolve_picks_one_real_count_with_justification(sc, db):
    sid = sc.session()
    item_id, a, _ = _conflict(sc, sid)
    assert sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": a["id"], "justification": ""}).status_code == 422
    r = sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": a["id"], "justification": "conferido no local"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved" and Decimal(body["accepted_quantity"]) == 10
    assert body["review_reasons"] == [] and body["resolution_note"] == "conferido no local"
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.item_resolved")).one()
    assert ev.details["previous_reasons"] == ["count_conflict"]


def test_resolve_rejects_foreign_count_and_old_round(sc):
    sid = sc.session()
    item_id, a, _ = _conflict(sc, sid)
    other = sc.count(sid, sc.op, qty="1", product=sc.kg).json()
    assert sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": other["id"], "justification": "abc"}).status_code == 422
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"})
    sc.count(sid, sc.op, qty="5")
    sc.count(sid, sc.op2, qty="6")
    assert sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": a["id"], "justification": "abc"}).status_code == 422


def test_resolve_only_when_needed(sc):
    sid = sc.session()
    c = sc.count(sid, sc.op).json()
    assert sc.post(f"{sid}/items/{sc.item(sid)['id']}/resolve",
                   {"count_id": c["id"], "justification": "abc"}).status_code == 409


def test_count_after_resolution_reopens_item(sc):
    sid = sc.session()
    item_id, a, _ = _conflict(sc, sid)
    sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": a["id"], "justification": "abc"})
    sc.count(sid, sc.manager, qty="10")
    it = sc.item(sid)
    assert it["status"] == "needs_review" and "count_after_resolution" in it["review_reasons"]
    assert it["accepted_quantity"] is None and it["resolved_by_id"] is None


# ---------- transições ----------
def test_full_flow_and_closed_session_is_frozen(sc, db):
    sid = sc.session()
    item_id, a, _ = _conflict(sc, sid)
    assert sc.post(f"{sid}/close").status_code == 409  # ainda aberta
    assert sc.post(f"{sid}/submit-review").status_code == 200
    assert sc.post(f"{sid}/submit-review").status_code == 409
    assert sc.post(f"{sid}/close").status_code == 409  # item pendente
    sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": a["id"], "justification": "abc"})
    r = sc.post(f"{sid}/close")
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["closed_at"]
    assert sc.count(sid, sc.op, qty="1", product=sc.kg).status_code == 409
    assert sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"}).status_code == 409
    assert sc.post(f"{sid}/items/{item_id}/erp-check").status_code == 409
    assert sc.post(f"{sid}/cancel", {"reason": "tarde demais"}).status_code == 409
    sc.session()  # local liberado para nova sessão


def test_in_review_accepts_only_requested_recounts(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="10")
    item_id = sc.item(sid)["id"]
    sc.post(f"{sid}/submit-review")
    assert sc.count(sid, sc.op, qty="11").status_code == 409  # item já contado
    assert sc.count(sid, sc.op, qty="1", product=sc.kg).status_code == 409  # produto novo
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"})
    assert sc.post(f"{sid}/close").status_code == 409
    assert sc.count(sid, sc.op, qty="11").status_code == 201
    assert sc.post(f"{sid}/close").status_code == 200


def test_empty_session_cannot_be_closed(sc):
    sid = sc.session()
    sc.post(f"{sid}/submit-review")
    assert sc.post(f"{sid}/close").status_code == 409
    assert sc.post(f"{sid}/cancel", {"reason": "sem contagem"}).json()["cancel_reason"] == "sem contagem"


def test_counts_are_append_only(sc, db):
    sid = sc.session()
    sc.count(sid, sc.op)
    count = db.scalars(select(InventoryCount)).one()
    count.quantity = Decimal("999")
    with pytest.raises(RuntimeError):
        db.flush()
    db.rollback()
    db.delete(db.scalars(select(InventoryCount)).one())
    with pytest.raises(RuntimeError):
        db.flush()
    db.rollback()


# ---------- leitura do ERP ----------
def _old():
    return (datetime.now(UTC) - timedelta(days=2)).replace(tzinfo=None)


def test_erp_check_requires_mapped_location(sc):
    sc.client.patch(f"/api/v1/locations/{sc.loc['id']}", json={"external_code": None}, headers=sc.admin)
    sid = sc.session()
    sc.count(sid, sc.op)
    erp = StubErp()
    sc.use_erp(erp)
    assert sc.post(f"{sid}/items/{sc.item(sid)['id']}/erp-check").status_code == 422
    assert erp.calls == 0


def test_erp_check_matching_balance_without_movement_keeps_item(sc):
    sc.use_erp(StubErp(last_changed_at=_old()))
    sid = sc.session()
    sc.count(sid, sc.op, qty="100")  # mock: 1001 em DEP = 100
    r = sc.post(f"{sid}/items/{sc.item(sid)['id']}/erp-check").json()
    assert Decimal(r["difference"]) == 0 and r["item_status"] == "counted" and r["review_reasons"] == []
    assert Decimal(r["check"]["erp_quantity"]) == 100


def test_erp_difference_goes_to_review(sc):
    sc.use_erp(StubErp(last_changed_at=_old()))
    sid = sc.session()
    sc.count(sid, sc.op, qty="90")
    r = sc.post(f"{sid}/items/{sc.item(sid)['id']}/erp-check").json()
    assert Decimal(r["difference"]) == -10
    assert r["item_status"] == "needs_review" and r["review_reasons"] == ["erp_difference"]


@pytest.mark.parametrize("minutes_ago_vs_count", [-60, 60, 60 * 4])
def test_erp_change_possibly_after_count_goes_to_review(sc, minutes_ago_vs_count):
    # Horário sem fuso: até 5h antes da contagem ainda pode ser posterior (ERP em horário do Brasil).
    changed = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago_vs_count)
    sc.use_erp(StubErp(last_changed_at=changed))
    sid = sc.session()
    sc.count(sid, sc.op, qty="100")
    r = sc.post(f"{sid}/items/{sc.item(sid)['id']}/erp-check").json()
    assert r["review_reasons"] == ["erp_movement_after_count"]


def test_erp_without_timestamp_is_unverifiable_not_ok(sc):
    sc.use_erp(StubErp(last_changed_at=None))
    sid = sc.session()
    sc.count(sid, sc.op, qty="100")
    r = sc.post(f"{sid}/items/{sc.item(sid)['id']}/erp-check").json()
    assert r["review_reasons"] == ["erp_movement_unverifiable"]


def test_erp_balance_changed_between_reads_is_movement(sc):
    erp = StubErp(last_changed_at=_old())
    sc.use_erp(erp)
    sid = sc.session()
    sc.count(sid, sc.op, qty="100")
    item_id = sc.item(sid)["id"]
    sc.post(f"{sid}/items/{item_id}/erp-check")
    erp.balances[("1001", "DEP")] = Decimal("95")
    r = sc.post(f"{sid}/items/{item_id}/erp-check").json()
    assert "erp_movement_after_count" in r["review_reasons"]


def test_resolved_item_reopens_only_if_erp_changed(sc):
    erp = StubErp(last_changed_at=_old())
    sc.use_erp(erp)
    sid = sc.session()
    c = sc.count(sid, sc.op, qty="90").json()
    item_id = sc.item(sid)["id"]
    sc.post(f"{sid}/items/{item_id}/erp-check")
    sc.post(f"{sid}/items/{item_id}/resolve", {"count_id": c["id"], "justification": "perda confirmada"})
    r = sc.post(f"{sid}/items/{item_id}/erp-check").json()
    assert r["item_status"] == "resolved" and Decimal(r["difference"]) == -10
    erp.balances[("1001", "DEP")] = Decimal("80")
    r = sc.post(f"{sid}/items/{item_id}/erp-check").json()
    assert r["item_status"] == "needs_review" and r["review_reasons"] == ["erp_movement_after_count"]


def test_erp_failure_changes_nothing(sc):
    sid = sc.session()
    sc.count(sid, sc.op, qty="100")
    before = sc.item(sid)
    sc.use_erp(StubErp(error=ErpTimeoutError("timeout")))
    assert sc.post(f"{sid}/items/{before['id']}/erp-check").status_code == 504
    assert sc.item(sid) == before
    detail = sc.client.get(f"{BASE}/{sid}/items/{before['id']}", headers=sc.manager).json()
    assert detail["erp_checks"] == []


def test_erp_check_during_recount_only_records(sc):
    sc.use_erp(StubErp(last_changed_at=_old()))
    sid = sc.session()
    item_id, _, _ = _conflict(sc, sid)
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "ver"})
    r = sc.post(f"{sid}/items/{item_id}/erp-check").json()
    assert r["item_status"] == "recount_requested" and r["review_reasons"] == []


def test_recount_clears_erp_flags(sc):
    sc.use_erp(StubErp(last_changed_at=_old()))
    sid = sc.session()
    sc.count(sid, sc.op, qty="90")
    item_id = sc.item(sid)["id"]
    sc.post(f"{sid}/items/{item_id}/erp-check")
    sc.post(f"{sid}/recounts", {"item_id": item_id, "reason": "recontar"})
    sc.count(sid, sc.op, qty="100")
    it = sc.item(sid)
    assert it["status"] == "counted" and it["review_reasons"] == []


def test_uncounted_products_do_not_become_items(sc):
    sid = sc.session()
    sc.count(sid, sc.op)
    rows = sc.client.get(f"{BASE}/{sid}/items", headers=sc.manager).json()
    assert len(rows) == 1  # só o contado; os demais produtos não viram saldo zero


def test_list_filters(sc):
    sid = sc.session(sector="A")
    sc.count(sid, sc.op)
    sc.session(sector="B")
    assert len(sc.client.get(BASE, params={"status": "open"}, headers=sc.manager).json()) == 2
    assert sc.client.get(BASE, params={"status": "closed"}, headers=sc.manager).json() == []
    assert sc.client.get(f"{BASE}/{sid}/items", params={"status": "needs_review"}, headers=sc.manager).json() == []
    assert sc.client.get(f"{BASE}/{sid}", headers=sc.op).json()["items_by_status"] == {"counted": 1}
