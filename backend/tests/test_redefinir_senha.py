"""Comando de servidor que redefine uma senha (scripts/redefinir_senha.py)."""
import importlib.util
from pathlib import Path

from sqlalchemy import select

from app.models import AuditEvent, RefreshToken
from tests.conftest import PASSWORD, login

_path = Path(__file__).resolve().parents[2] / "scripts" / "redefinir_senha.py"
_spec = importlib.util.spec_from_file_location("redefinir_senha", _path)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

NEW = "senha-nova-de-teste-456"
ACTION = "auth.password_reset_on_server"


def test_reset_changes_password_ends_logins_and_is_audited(client, world, db):
    assert login(client, "admin@a.com").status_code == 200

    assert script.reset_password(db, " ADMIN@a.com ", NEW) is True

    db.expire_all()
    tokens = db.scalars(select(RefreshToken)).all()
    assert tokens and all(t.revoked_at is not None for t in tokens)
    assert login(client, "admin@a.com", PASSWORD).status_code == 401
    assert login(client, "admin@a.com", NEW).status_code == 200
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == ACTION))
    assert event is not None and NEW not in str(event.details)


def test_reset_only_touches_that_person(client, world, db):
    assert script.reset_password(db, "admin@a.com", NEW) is True
    assert login(client, "admin@b.com", PASSWORD).status_code == 200


def test_unknown_email_changes_nothing(client, world, db):
    assert script.reset_password(db, "ninguem@a.com", NEW) is False
    assert login(client, "admin@a.com", PASSWORD).status_code == 200
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == ACTION)) is None
