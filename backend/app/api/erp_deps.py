"""Resolve o adaptador de ERP DA ORGANIZAÇÃO do usuário autenticado."""
from collections.abc import Iterator

from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.crypto import SecretsError, decrypt_secret
from app.integrations.erp_port import ErpAdapter, ErpNotAvailableError
from app.integrations.mock_erp import MockErpAdapter
from app.integrations.sysordem.adapter import SysordemErpAdapter
from app.models.erp_connection import ErpConnection


def get_erp_adapter(user: CurrentUser, db: DbSession) -> Iterator[ErpAdapter]:
    s = get_settings()
    if s.erp_adapter == "mock":
        yield MockErpAdapter()
        return
    conn = db.scalar(select(ErpConnection).where(
        ErpConnection.organization_id == user.organization_id,
        ErpConnection.is_active.is_(True)))
    if conn is None:
        raise ErpNotAvailableError("ERP não configurado para esta organização.")
    try:
        token = decrypt_secret(conn.token_encrypted)
    except SecretsError as exc:
        raise ErpNotAvailableError("Credencial do ERP ilegível; reconfigure a conexão.") from exc
    adapter = SysordemErpAdapter(
        s.sysordem_base_url, token,
        timeout=s.sysordem_timeout_seconds, page_size=s.sysordem_page_size)
    try:
        yield adapter
    finally:
        adapter.close()
