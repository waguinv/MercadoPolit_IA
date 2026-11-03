"""Criptografia simétrica (Fernet) para segredos guardados no banco (ex.: token do ERP)."""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretsError(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().secrets_encryption_key
    if not key:
        raise SecretsError("SECRETS_ENCRYPTION_KEY não configurada.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretsError("SECRETS_ENCRYPTION_KEY inválida.") from exc


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretsError("Não foi possível decifrar o segredo (chave alterada?).") from exc
