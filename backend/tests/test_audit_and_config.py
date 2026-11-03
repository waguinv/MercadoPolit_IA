import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import AuditEvent
from app.services import audit


def test_audit_event_is_append_only(db, world):
    audit.record(db, action="t.created", organization_id=world["org_a"].id)
    db.commit()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "t.created")).one()
    ev.action = "adulterado"
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()
    db.delete(ev)
    with pytest.raises(RuntimeError):
        db.commit()


def test_audit_strips_sensitive_keys(db, world):
    audit.record(db, action="t.x", organization_id=world["org_a"].id,
                 details={"password": "x", "token": "y", "ok": 1})
    db.commit()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "t.x")).one()
    assert ev.details == {"ok": 1}


def test_placeholder_secret_rejected_outside_dev():
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="change-me-with-a-long-random-string", _env_file=None)


def test_weak_secret_rejected_outside_dev():
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="curta", _env_file=None)


def test_strong_secret_accepted_in_production():
    Settings(app_env="production", secret_key="x" * 40, _env_file=None)


def test_invalid_erp_adapter_rejected():
    with pytest.raises(ValueError):
        Settings(erp_adapter="outro", _env_file=None)


def test_sysordem_requires_valid_encryption_key():
    with pytest.raises(ValueError):
        Settings(erp_adapter="sysordem", secrets_encryption_key=None, _env_file=None)
    with pytest.raises(ValueError):
        Settings(erp_adapter="sysordem", secrets_encryption_key="nao-e-fernet", _env_file=None)


def test_sysordem_base_url_must_be_https_outside_development():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    with pytest.raises(ValueError):
        Settings(erp_adapter="sysordem", secrets_encryption_key=key, app_env="test",
                 sysordem_base_url="http://interno.local", _env_file=None)
    Settings(erp_adapter="sysordem", secrets_encryption_key=key, app_env="test", _env_file=None)


def test_page_size_bounds():
    with pytest.raises(ValueError):
        Settings(sysordem_page_size=0, _env_file=None)
    with pytest.raises(ValueError):
        Settings(sysordem_page_size=501, _env_file=None)
