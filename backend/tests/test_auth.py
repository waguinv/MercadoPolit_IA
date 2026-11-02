from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditEvent
from tests.conftest import PASSWORD, auth, login


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_ok_and_me(client, world):
    h = auth(client, "admin@a.com")
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["email"] == "admin@a.com"
    assert me["organization_id"] == str(world["org_a"].id)
    assert "password_hash" not in me


def test_login_wrong_password_and_unknown_user_same_response(client, world):
    r1 = login(client, "admin@a.com", "errada-errada-123")
    r2 = login(client, "naoexiste@a.com", PASSWORD)
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()


def test_login_failure_is_audited_without_password(client, world, db):
    login(client, "admin@a.com", "errada-errada-123")
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")).one()
    assert "errada" not in str(ev.details)


def test_inactive_user_cannot_login_or_use_old_token(client, world, db):
    h = auth(client, "manager@a.com")
    world["manager_a"].is_active = False
    db.add(world["manager_a"])
    db.commit()
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert login(client, "manager@a.com").status_code == 401


def test_requires_token_and_rejects_garbage_and_expired(client, world):
    assert client.get("/api/v1/stores").status_code == 401
    assert client.get("/api/v1/stores", headers={"Authorization": "Bearer lixo"}).status_code == 401
    now = datetime.now(UTC)
    expired = jwt.encode(
        {"sub": str(world["admin_a"].id), "iat": now - timedelta(hours=2),
         "exp": now - timedelta(hours=1)},
        get_settings().secret_key, algorithm="HS256")
    assert client.get("/api/v1/stores", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_token_signed_with_other_key_rejected(client, world):
    forged = jwt.encode({"sub": str(world["admin_a"].id),
                         "exp": datetime.now(UTC) + timedelta(hours=1)},
                        "outra-chave-qualquer-com-mais-de-32-chars", algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
