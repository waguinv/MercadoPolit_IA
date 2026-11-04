"""Resolve o adaptador de ERP DA ORGANIZAÇÃO do usuário autenticado."""
from collections.abc import Iterator

from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.crypto import SecretsError, decrypt_secret
from app.integrations.erp_port import ErpAdapter, ErpNotAvailableError
from app.integrations.mock_erp import MockErpAdapter
from app.integrations.registry import get_connector
from app.models.erp_connection import ErpConnection


def get_erp_adapter(user: CurrentUser, db: DbSession) -> Iterator[ErpAdapter]:
    if get_settings().erp_adapter == "mock":
        yield MockErpAdapter()
        return
    conn = db.scalar(select(ErpConnection).where(
        ErpConnection.organization_id == user.organization_id,
        ErpConnection.is_active.is_(True)))
    if conn is None:
        raise ErpNotAvailableError("ERP não configurado para esta organização.")
    connector = get_connector(conn.kind)
    if connector is None:
        raise ErpNotAvailableError("Sistema da conexão não é mais oferecido.")
    try:
        token = decrypt_secret(conn.token_encrypted)
    except SecretsError as exc:
        raise ErpNotAvailableError("Credencial do ERP ilegível; reconfigure a conexão.") from exc
    adapter = connector.build(token)
    try:
        yield adapter
    finally:
        adapter.close()


def erp_source(db, organization_id) -> str | None:
    """De onde vêm os dados desta organização agora: ``mock``, um conector ou nenhum."""
    if get_settings().erp_adapter == "mock":
        return "mock"
    return db.scalar(select(ErpConnection.kind).where(
        ErpConnection.organization_id == organization_id,
        ErpConnection.is_active.is_(True)))
