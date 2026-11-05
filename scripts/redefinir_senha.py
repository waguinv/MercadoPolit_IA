"""Redefine a senha de uma pessoa, para quem tem acesso ao servidor.

Uso no servidor: sudo infra/deploy/compose.sh exec backend python /scripts/redefinir_senha.py
A senha é lida por prompt (getpass) ou da variável NOVA_SENHA. Nunca a passe em argumentos nem em chats.

A lógica fica aqui, sem código novo no backend: basta "git pull" para o servidor ter este comando,
porque a pasta scripts é montada no contêiner.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.models import User  # noqa: E402
from app.services import audit, refresh_tokens  # noqa: E402


def reset_password(db: Session, email: str, new: str) -> bool:
    """Troca a senha e encerra todos os logins da pessoa. Devolve False se o e-mail não existe."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        return False
    user.password_hash = hash_password(new)
    refresh_tokens.revoke_user(db, user.id)
    audit.record(db, action="auth.password_reset_on_server", organization_id=user.organization_id,
                 entity_type="user", entity_id=user.id)
    db.commit()
    return True


def main() -> None:
    email = input("E-mail da pessoa: ").strip().lower()
    password = os.environ.get("NOVA_SENHA")
    if password is None:
        password = getpass.getpass("Nova senha, com 12 caracteres ou mais (não aparece ao digitar): ")
        if password != getpass.getpass("Repita a nova senha: "):
            sys.exit("As senhas não são iguais. Nada foi alterado.")
    if not email or len(password) < 12:
        sys.exit("Dados inválidos (a senha precisa de 12 ou mais caracteres). Nada foi alterado.")
    with Session(get_engine()) as db:
        if not reset_password(db, email, password):
            sys.exit("E-mail não encontrado. Nada foi alterado.")
    print("Senha redefinida. Os logins abertos dessa pessoa foram encerrados.")


if __name__ == "__main__":
    main()
