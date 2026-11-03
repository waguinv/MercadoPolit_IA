import os

os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-more-than-32-characters"
from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRETS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, make_url  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Organization, Role, Store, User  # noqa: E402

PASSWORD = "senha-de-teste-123"
_PASSWORD_HASH = hash_password(PASSWORD)  # calculado uma vez (Argon2 é lento de propósito)


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _make_test_engine():
    if not TEST_DATABASE_URL:
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # As tabelas são apagadas a cada teste: nunca aceitar um banco que não seja claramente de teste.
    url = make_url(TEST_DATABASE_URL)
    if "test" not in (url.database or ""):
        raise RuntimeError("TEST_DATABASE_URL deve apontar para um banco com 'test' no nome.")
    # Limites: uma trava presa deve falhar o teste, nunca deixar o CI parado.
    return create_engine(url, pool_size=20, connect_args={"options": "-c lock_timeout=20s -c statement_timeout=60s"})


@pytest.fixture()
def db_factory():
    engine = _make_test_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db(db_factory) -> Session:
    with db_factory() as s:
        yield s


@pytest.fixture()
def client(db_factory):
    app = create_app()

    def _get_db():
        with db_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def make_user(db: Session, org: Organization, email: str, role: Role, store: Store | None = None):
    u = User(organization_id=org.id, store_id=store.id if store else None, email=email,
             full_name=email.split("@")[0], password_hash=_PASSWORD_HASH, role=role)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db):
    """Duas organizações (A e B) com lojas e usuários, para testar isolamento."""
    org_a, org_b = Organization(name="Rede A"), Organization(name="Rede B")
    db.add_all([org_a, org_b])
    db.flush()
    store_a1 = Store(organization_id=org_a.id, code="A1", name="Loja A1")
    store_a2 = Store(organization_id=org_a.id, code="A2", name="Loja A2")
    store_b1 = Store(organization_id=org_b.id, code="B1", name="Loja B1")
    db.add_all([store_a1, store_a2, store_b1])
    db.commit()
    return {
        "org_a": org_a, "org_b": org_b,
        "store_a1": store_a1, "store_a2": store_a2, "store_b1": store_b1,
        "admin_a": make_user(db, org_a, "admin@a.com", Role.ADMIN),
        "manager_a": make_user(db, org_a, "manager@a.com", Role.MANAGER),
        "op_a1": make_user(db, org_a, "op@a.com", Role.OPERATOR, store_a1),
        "admin_b": make_user(db, org_b, "admin@b.com", Role.ADMIN),
    }


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def auth(client: TestClient, email: str) -> dict[str, str]:
    r = login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()["access_token"]}"}


@pytest.fixture()
def sysordem_mode(monkeypatch):
    """Liga ERP_ADAPTER=sysordem (cada organização usa o próprio token)."""
    from app.core import config

    monkeypatch.setenv("ERP_ADAPTER", "sysordem")
    config.get_settings.cache_clear()
    yield
    monkeypatch.undo()
    config.get_settings.cache_clear()
