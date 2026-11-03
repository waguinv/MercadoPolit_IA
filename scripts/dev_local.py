"""Sobe o backend para desenvolvimento LOCAL: SQLite, ERP simulado e dados de exemplo.

Uso (na raiz do repositório):  backend/.venv/Scripts/python.exe scripts/dev_local.py
Os dados e a senha abaixo são fictícios e só existem no arquivo backend/dev.db. O script recusa
qualquer banco que não seja SQLite, para nunca semear um ambiente real.
"""
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

BACKEND = Path(__file__).resolve().parent.parent / "backend"
DEV_PASSWORD = "senha-local-dev-123"
USERS = [
    ("admin@exemplo.com.br", "Ana Admin", "ADMIN"),
    ("gerente@exemplo.com.br", "Gil Gerente", "MANAGER"),
    ("operador@exemplo.com.br", "Olga Operadora", "OPERATOR"),
    ("operador2@exemplo.com.br", "Otto Operador", "OPERATOR"),
    ("auditor@exemplo.com.br", "Alex Auditor", "AUDITOR"),
]

os.environ.setdefault("DATABASE_URL", "sqlite:///./dev.db")
os.environ.setdefault("APP_ENV", "development")
os.environ["ERP_ADAPTER"] = "mock"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
if not os.environ["DATABASE_URL"].startswith("sqlite"):
    sys.exit("Este script só roda com SQLite local.")


def seed() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.core.security import hash_password
    from app.db.session import get_engine
    from app.integrations.mock_erp import MockErpAdapter
    from app.models import Organization, Role, StockLocation, Store, User
    from app.services.catalog_sync import sync_catalog

    with Session(get_engine(), expire_on_commit=False) as db:
        if db.scalar(select(Organization).limit(1)) is not None:
            return
        org = Organization(name="Mercado Exemplo")
        db.add(org)
        db.flush()
        store = Store(organization_id=org.id, code="L1", name="Loja Centro")
        db.add(store)
        db.flush()
        db.add_all([
            StockLocation(organization_id=org.id, store_id=store.id, code="DEP", name="Depósito",
                          external_code="DEP"),
            StockLocation(organization_id=org.id, store_id=store.id, code="LOJA", name="Salão da loja",
                          external_code="LOJA"),
        ])
        password_hash = hash_password(DEV_PASSWORD)
        users = [User(organization_id=org.id, email=email, full_name=name, password_hash=password_hash,
                      role=Role[role]) for email, name, role in USERS]
        db.add_all(users)
        db.commit()
        sync_catalog(db, users[0], MockErpAdapter())
    print(f"Dados de exemplo criados. Usuários: {', '.join(u[0] for u in USERS)}")


subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
seed()
# No mesmo processo: no Windows, os.execv criaria um processo solto que não para com o script.
uvicorn.run("app.main:app", port=8010)
