"""Hash de senha (Argon2) e tokens JWT de acesso."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# Hash descartável para equalizar o tempo de resposta quando o usuário não existe.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_expire_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> UUID | None:
    try:
        data = jwt.decode(
            token, get_settings().secret_key, algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        return UUID(data["sub"])
    except (jwt.PyJWTError, ValueError):
        return None
