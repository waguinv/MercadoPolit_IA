"""Sobe o backend para desenvolvimento LOCAL, só com SQLite.

Uso (na raiz do repositório):
  backend/.venv/Scripts/python.exe scripts/dev_local.py              ERP simulado e dados de exemplo
  backend/.venv/Scripts/python.exe scripts/dev_local.py --erp-real   só leituras no ERP real, com o token
                                                                     cadastrado na tela de Configuração

Os dados e a senha abaixo são fictícios e só existem na máquina. Com --erp-real, o banco é outro
(backend/dev_real.db) e a chave que cifra o token fica em backend/.dev_real.key; os dois são ignorados
pelo Git. O script recusa qualquer banco que não seja SQLite, para nunca semear um ambiente real.
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
REAL = "--erp-real" in sys.argv[1:]

os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "development")
if REAL:
    from cryptography.fernet import Fernet

    key_file = BACKEND / ".dev_real.key"
    if not key_file.exists():
        key_file.write_text(Fernet.generate_key().decode(), encoding="ascii")
    os.environ["SECRETS_ENCRYPTION_KEY"] = key_file.read_text(encoding="ascii").strip()
    os.environ["DATABASE_URL"] = "sqlite:///./dev_real.db"
    os.environ["ERP_ADAPTER"] = "real"
else:
    os.environ.setdefault("DATABASE_URL", "sqlite:///./dev.db")
    os.environ["ERP_ADAPTER"] = "mock"
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
        password_hash = hash_password(DEV_PASSWORD)
        if REAL:  # Sem catálogo, loja ou local fictícios: tudo vem da conexão feita na tela.
            org = Organization(name="Teste local com ERP real")
            db.add(org)
            db.flush()
            email, name, role = USERS[0]
            db.add(User(organization_id=org.id, email=email, full_name=name, password_hash=password_hash,
                        role=Role[role]))
            db.commit()
            print(f"Administrador local criado: {email}")
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
        users = [User(organization_id=org.id, email=email, full_name=name, password_hash=password_hash,
                      role=Role[role]) for email, name, role in USERS]
        db.add_all(users)
        db.commit()
        sync_catalog(db, users[0], MockErpAdapter(), "mock")
    print(f"Dados de exemplo criados. Usuários: {', '.join(u[0] for u in USERS)}")


subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
seed()
# No mesmo processo: no Windows, os.execv criaria um processo solto que não para com o script.
uvicorn.run("app.main:app", port=8010)
