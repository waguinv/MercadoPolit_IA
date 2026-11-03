from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import utcnow
from app.models import AuditEvent, LoginThrottle
from tests.conftest import PASSWORD, auth, login

WRONG = "senha-errada-123"
AUTH = "/api/v1/auth"


def from_ip(client, ip):
    return TestClient(client.app, client=(ip, 50000))


def fail(client, email="op@a.com", times=1):
    return [login(client, email, WRONG).status_code for _ in range(times)]


def test_fifth_error_still_checks_and_sixth_is_blocked_even_with_right_password(client, world):
    assert fail(client, times=5) == [401] * 5
    r = login(client, "op@a.com")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "Aguarde" in r.json()["detail"] and "15 minuto" in r.json()["detail"]


def test_blocked_attempt_does_not_check_password_or_write_audit(client, world, db, monkeypatch):
    fail(client, times=5)
    before = len(db.scalars(select(AuditEvent)).all())
    calls = []
    monkeypatch.setattr("app.services.auth.verify_password", lambda *a: calls.append(a) or True)
    assert login(client, "op@a.com").status_code == 429
    assert calls == []
    assert len(db.scalars(select(AuditEvent)).all()) == before


def test_right_password_clears_the_errors_of_the_account(client, world):
    fail(client, times=4)
    assert login(client, "op@a.com").status_code == 200
    assert fail(client, times=5) == [401] * 5


def test_block_does_not_reach_same_account_from_other_ip(client, world):
    fail(client, times=5)
    assert login(client, "op@a.com").status_code == 429
    assert login(from_ip(client, "10.0.0.2"), "op@a.com").status_code == 200


def test_block_does_not_reach_other_account_on_same_ip(client, world):
    fail(client, times=5)
    assert login(client, "manager@a.com").status_code == 200


def test_distributed_attack_on_one_account_is_blocked(client, world):
    for i in range(20):
        assert fail(from_ip(client, f"10.0.1.{i}")) == [401]
    assert login(from_ip(client, "10.0.2.1"), "op@a.com").status_code == 429


def test_many_accounts_from_one_ip_is_blocked(client, world):
    for i in range(50):
        assert fail(client, f"pessoa{i}@a.com") == [401]
    assert login(client, "op@a.com").status_code == 429
    assert login(from_ip(client, "10.0.0.2"), "op@a.com").status_code == 200


def test_store_sharing_one_ip_is_not_blocked_by_successful_logins(client, world):
    # Uma loja atrás do mesmo IP: acertos não consomem o limite por IP.
    for _ in range(55):
        assert login(client, "op@a.com").status_code == 200


def test_unknown_account_behaves_like_existing_one(client, world):
    # Não revela se o e-mail existe.
    assert fail(client, "ninguem@a.com", times=5) == fail(client, "op@a.com", times=5) == [401] * 5
    a, b = login(client, "ninguem@a.com", WRONG), login(client, "op@a.com", WRONG)
    assert a.status_code == b.status_code == 429
    assert a.json()["detail"].split(" Aguarde")[0] == b.json()["detail"].split(" Aguarde")[0]


def test_email_case_does_not_bypass(client, world):
    fail(client, times=3)
    fail(client, "OP@a.com", times=2)
    assert login(client, "Op@A.com").status_code == 429


def test_window_expiry_unblocks(client, world, db):
    fail(client, times=5)
    assert login(client, "op@a.com").status_code == 429
    for row in db.scalars(select(LoginThrottle)):
        row.window_started_at = utcnow() - timedelta(minutes=16)
    db.commit()
    assert login(client, "op@a.com").status_code == 200


def test_table_stores_neither_email_nor_ip(client, world, db):
    fail(client)
    rows = db.scalars(select(LoginThrottle)).all()
    assert len(rows) == 3
    assert all("op@a.com" not in r.key_hash and "testclient" not in r.key_hash for r in rows)


def test_reaching_limit_is_audited_once_without_password(client, world, db):
    fail(client, times=5)
    login(client, "op@a.com", WRONG)
    login(client, "op@a.com", WRONG)
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_limit_reached")).all()
    assert len(events) == 1
    assert events[0].details["limit"] == "conta_ip" and events[0].details["email"] == "op@a.com"
    assert WRONG not in str(events[0].details)


def test_refresh_is_not_affected_by_login_block(client, world):
    # Quem está contando não cai porque alguém errou a senha da conta.
    assert login(client, "op@a.com").status_code == 200
    fail(client, times=5)
    assert login(client, "op@a.com").status_code == 429
    assert client.post(f"{AUTH}/refresh").status_code == 200


def test_old_rows_are_purged_on_success(client, world, db):
    db.add(LoginThrottle(key_hash="x" * 64, attempts=1, window_started_at=utcnow() - timedelta(days=2)))
    db.commit()
    assert login(client, "op@a.com").status_code == 200
    db.expire_all()
    assert db.get(LoginThrottle, "x" * 64) is None


def test_change_password_guessing_is_limited(client, world):
    h = auth(client, "op@a.com")
    body = {"current_password": WRONG, "new_password": "outra-senha-forte-456"}
    assert [client.post(f"{AUTH}/change-password", headers=h, json=body).status_code for _ in range(5)] == [409] * 5
    body["current_password"] = PASSWORD
    r = client.post(f"{AUTH}/change-password", headers=h, json=body)
    assert r.status_code == 429 and "Retry-After" in r.headers
