"""Cria uma organização e seu primeiro administrador.

Uso (a partir de backend/):  python ../scripts/create_admin.py
A senha é lida por prompt (getpass) ou da variável BOOTSTRAP_ADMIN_PASSWORD.
Nunca passe senhas em argumentos de linha de comando nem em chats.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.models import Organization, Role, User  # noqa: E402
from app.services import audit  # noqa: E402


def main() -> None:
    org_name = input("Nome da organização: ").strip()
    email = input("E-mail do administrador: ").strip().lower()
    name = input("Nome completo: ").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Senha (>= 12): ")
    if not (org_name and email and name) or len(password) < 12:
        sys.exit("Dados inválidos (senha precisa de 12+ caracteres).")
    with Session(get_engine()) as db:
        org = Organization(name=org_name)
        db.add(org)
        db.flush()
        admin = User(organization_id=org.id, email=email, full_name=name,
                     password_hash=hash_password(password), role=Role.ADMIN)
        db.add(admin)
        db.flush()
        audit.record(db, action="bootstrap.admin_created", organization_id=org.id,
                     actor_user_id=admin.id, entity_type="user", entity_id=admin.id)
        db.commit()
    print("Organização e administrador criados.")


if __name__ == "__main__":
    main()
