from sqlalchemy import select

from app.models import AuditEvent
from tests.conftest import auth


def test_admin_creates_and_lists_locations(client, world, db):
    h = auth(client, "admin@a.com")
    sid = world["store_a1"].id
    r = client.post(f"/api/v1/stores/{sid}/locations",
                    json={"code": "DEP", "name": "Depósito", "external_code": "DEP"}, headers=h)
    assert r.status_code == 201 and r.json()["store_id"] == str(sid)
    assert [x["code"] for x in client.get(f"/api/v1/stores/{sid}/locations", headers=h).json()] == ["DEP"]
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "location.created")).one()


def test_duplicate_code_in_same_store_conflicts_but_ok_in_other_store(client, world):
    h = auth(client, "admin@a.com")
    body = {"code": "DEP", "name": "Depósito"}
    assert client.post(f"/api/v1/stores/{world['store_a1'].id}/locations", json=body, headers=h).status_code == 201
    assert client.post(f"/api/v1/stores/{world['store_a1'].id}/locations", json=body, headers=h).status_code == 409
    assert client.post(f"/api/v1/stores/{world['store_a2'].id}/locations", json=body, headers=h).status_code == 201


def test_non_admin_cannot_create_or_update(client, world):
    ha, hm = auth(client, "admin@a.com"), auth(client, "manager@a.com")
    sid = world["store_a1"].id
    loc = client.post(f"/api/v1/stores/{sid}/locations", json={"code": "A", "name": "A"}, headers=ha).json()
    assert client.post(f"/api/v1/stores/{sid}/locations", json={"code": "B", "name": "B"},
                       headers=hm).status_code == 403
    assert client.patch(f"/api/v1/locations/{loc['id']}", json={"name": "Z"}, headers=hm).status_code == 403


def test_cannot_touch_other_tenant_store_or_location(client, world):
    ha, hb = auth(client, "admin@a.com"), auth(client, "admin@b.com")
    sid_b = world["store_b1"].id
    assert client.post(f"/api/v1/stores/{sid_b}/locations", json={"code": "X", "name": "X"},
                       headers=ha).status_code == 404
    assert client.get(f"/api/v1/stores/{sid_b}/locations", headers=ha).status_code == 404
    loc_b = client.post(f"/api/v1/stores/{sid_b}/locations", json={"code": "X", "name": "X"}, headers=hb).json()
    assert client.patch(f"/api/v1/locations/{loc_b['id']}", json={"name": "hack"}, headers=ha).status_code == 404


def test_store_restricted_user_sees_only_own_store_locations(client, world):
    ha, ho = auth(client, "admin@a.com"), auth(client, "op@a.com")
    for s in ("store_a1", "store_a2"):
        client.post(f"/api/v1/stores/{world[s].id}/locations", json={"code": "DEP", "name": "D"}, headers=ha)
    assert client.get(f"/api/v1/stores/{world['store_a1'].id}/locations", headers=ho).status_code == 200
    assert client.get(f"/api/v1/stores/{world['store_a2'].id}/locations", headers=ho).status_code == 404


def test_update_and_deactivate_location(client, world):
    h = auth(client, "admin@a.com")
    sid = world["store_a1"].id
    loc = client.post(f"/api/v1/stores/{sid}/locations", json={"code": "A", "name": "A"}, headers=h).json()
    r = client.patch(f"/api/v1/locations/{loc['id']}",
                     json={"name": "Novo", "is_active": False, "external_code": "E1"}, headers=h).json()
    assert r["name"] == "Novo" and r["is_active"] is False and r["external_code"] == "E1"
    assert client.patch(f"/api/v1/locations/{loc['id']}", json={"name": ""}, headers=h).status_code == 422
