from datetime import timedelta

from sqlalchemy import select

from app.api.v1.auth import REFRESH_COOKIE
from app.db.base import utcnow
from app.models import AuditEvent, RefreshToken
from tests.conftest import PASSWORD, auth, login

AUTH = "/api/v1/auth"
NEW_PASSWORD = "outra-senha-forte-456"


def cookie(client):
    return client.cookies.get(REFRESH_COOKIE)


def refresh(client):
    return client.post(f"{AUTH}/refresh")


def use_cookie(client, value):
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, value, path=f"{AUTH}")


# ---------- login e cookie ----------
def test_login_sets_protected_cookie_and_keeps_token_out_of_body(client, world):
    r = login(client, "admin@a.com")
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header and "path=/api/v1/auth" in header
    assert cookie(client) not in r.text


def test_cookie_is_secure_outside_development(client, world, monkeypatch):
    from app.core import config

    monkeypatch.setenv("APP_ENV", "production")
    config.get_settings.cache_clear()
    try:
        assert "secure" in login(client, "admin@a.com").headers["set-cookie"].lower()
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


def test_only_hash_is_stored(client, world, db):
    login(client, "admin@a.com")
    row = db.scalars(select(RefreshToken)).one()
    assert row.token_hash != cookie(client) and cookie(client) not in row.token_hash


# ---------- renovação ----------
def test_refresh_gives_working_access_token_and_rotates(client, world):
    login(client, "admin@a.com")
    old = cookie(client)
    r = refresh(client)
    assert r.status_code == 200 and cookie(client) != old
    me = client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "admin@a.com"


def test_refresh_without_or_with_unknown_cookie_is_401(client, world):
    assert refresh(client).status_code == 401
    use_cookie(client, "token-inventado")
    assert refresh(client).status_code == 401


def test_repeat_within_grace_keeps_login_for_flaky_network(client, world):
    # A resposta se perdeu na rede: o aparelho repete com o token antigo e o login continua.
    login(client, "admin@a.com")
    old = cookie(client)
    assert refresh(client).status_code == 200
    use_cookie(client, old)
    assert refresh(client).status_code == 200
    assert refresh(client).status_code == 200


def test_reuse_after_grace_revokes_whole_login_and_audits(client, world, db):
    login(client, "admin@a.com")
    stolen = cookie(client)
    assert refresh(client).status_code == 200
    current = cookie(client)
    row = db.scalars(select(RefreshToken).where(RefreshToken.rotated_at.is_not(None))).one()
    row.rotated_at = utcnow() - timedelta(minutes=5)
    db.commit()
    use_cookie(client, stolen)
    assert refresh(client).status_code == 401
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.refresh_reuse_detected")).one()
    # O token legítimo mais novo também cai: não dá para saber quem é o ladrão.
    use_cookie(client, current)
    assert refresh(client).status_code == 401


def test_expired_refresh_is_rejected(client, world, db):
    login(client, "admin@a.com")
    row = db.scalars(select(RefreshToken)).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert refresh(client).status_code == 401


def test_inactive_user_cannot_refresh(client, world, db):
    login(client, "manager@a.com")
    world["manager_a"].is_active = False
    db.add(world["manager_a"])
    db.commit()
    assert refresh(client).status_code == 401


# ---------- sair ----------
def test_logout_revokes_this_login_only(client, world, db):
    login(client, "admin@a.com")
    other_device = cookie(client)
    login(client, "admin@a.com")
    assert client.post(f"{AUTH}/logout").status_code == 204
    assert not cookie(client)
    assert refresh(client).status_code == 401
    use_cookie(client, other_device)
    assert refresh(client).status_code == 200
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.logout")).one()


def test_logout_without_cookie_is_harmless(client, world):
    assert client.post(f"{AUTH}/logout").status_code == 204


# ---------- trocar a própria senha ----------
def test_change_password_ends_other_devices_and_keeps_this_one(client, world, db):
    login(client, "op@a.com")
    other_device = cookie(client)
    h = auth(client, "op@a.com")
    r = client.post(f"{AUTH}/change-password", headers=h,
                    json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 200
    assert refresh(client).status_code == 200
    use_cookie(client, other_device)
    assert refresh(client).status_code == 401
    assert login(client, "op@a.com").status_code == 401
    assert login(client, "op@a.com", NEW_PASSWORD).status_code == 200
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.password_changed")).all()
    assert len(events) == 1 and NEW_PASSWORD not in str(events[0].details)


def test_change_password_requires_current_and_strong_new(client, world):
    h = auth(client, "op@a.com")
    body = {"current_password": "errada", "new_password": NEW_PASSWORD}
    assert client.post(f"{AUTH}/change-password", headers=h, json=body).status_code == 409
    body = {"current_password": PASSWORD, "new_password": "curta"}
    assert client.post(f"{AUTH}/change-password", headers=h, json=body).status_code == 422
    assert client.post(f"{AUTH}/change-password", json=body).status_code == 401
    assert login(client, "op@a.com").status_code == 200


# ---------- administrar usuários ----------
def test_admin_deactivates_user_and_ends_devices(client, world, db):
    login(client, "op@a.com")
    device = cookie(client)
    h = auth(client, "admin@a.com")
    uid = world["op_a1"].id
    r = client.patch(f"/api/v1/users/{uid}", json={"is_active": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    use_cookie(client, device)
    assert refresh(client).status_code == 401
    assert login(client, "op@a.com").status_code == 401
    assert client.patch(f"/api/v1/users/{uid}", json={"is_active": True}, headers=h).status_code == 200
    assert login(client, "op@a.com").status_code == 200


def test_admin_resets_password_without_leaking_it(client, world, db):
    h = auth(client, "admin@a.com")
    r = client.patch(f"/api/v1/users/{world['op_a1'].id}", json={"password": NEW_PASSWORD}, headers=h)
    assert r.status_code == 200 and "password" not in r.text
    assert login(client, "op@a.com", NEW_PASSWORD).status_code == 200
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.updated")).all()
    assert NEW_PASSWORD not in str([e.details for e in events])


def test_admin_cannot_deactivate_self(client, world):
    h = auth(client, "admin@a.com")
    r = client.patch(f"/api/v1/users/{world['admin_a'].id}", json={"is_active": False}, headers=h)
    assert r.status_code == 409


def test_user_update_respects_tenant_and_role(client, world):
    ha, hm = auth(client, "admin@a.com"), auth(client, "manager@a.com")
    assert client.patch(f"/api/v1/users/{world['admin_b'].id}", json={"is_active": False}, headers=ha).status_code == 404
    assert client.patch(f"/api/v1/users/{world['op_a1'].id}", json={"is_active": False}, headers=hm).status_code == 403
    assert client.patch(f"/api/v1/users/{world['op_a1'].id}", json={"password": "curta"}, headers=ha).status_code == 422


def test_erp_connection_reports_simulated_mode_without_token(client, world):
    r = client.get("/api/v1/erp/connection", headers=auth(client, "admin@a.com")).json()
    assert r["adapter"] == "mock" and r["configured"] is False and "token" not in r
