"""Conexão do ERP por organização (token cifrado, nunca devolvido) e consultas de apoio."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, require_roles
from app.api.erp_deps import get_erp_adapter
from app.api.v1.catalog import erp_http_error
from app.core.config import get_settings
from app.core.crypto import SecretsError, encrypt_secret
from app.db.base import utcnow
from app.integrations.erp_port import ErpAdapter, ErpError
from app.models.erp_connection import ErpConnection
from app.models.user import Role, User
from app.schemas.catalog import ErpConnectionIn, ErpConnectionOut, ErpStockLocationOut
from app.services import audit

router = APIRouter(prefix="/erp", tags=["erp"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
Erp = Annotated[ErpAdapter, Depends(get_erp_adapter)]


def _get(db, org_id) -> ErpConnection | None:
    return db.scalar(select(ErpConnection).where(ErpConnection.organization_id == org_id))


def _out(conn: ErpConnection | None) -> ErpConnectionOut:
    adapter = get_settings().erp_adapter
    if conn is None:
        return ErpConnectionOut(adapter=adapter, configured=False)
    return ErpConnectionOut(adapter=adapter, configured=True, is_active=conn.is_active,
                            last_verified_at=conn.last_verified_at)


@router.get("/connection", response_model=ErpConnectionOut)
def get_connection(db: DbSession, actor: AdminUser) -> ErpConnectionOut:
    return _out(_get(db, actor.organization_id))


@router.put("/connection", response_model=ErpConnectionOut)
def put_connection(body: ErpConnectionIn, db: DbSession, actor: AdminUser) -> ErpConnectionOut:
    """Guarda (cifrado) o token do ERP da organização. Trocar o token zera a verificação."""
    try:
        encrypted = encrypt_secret(body.token.strip())
    except SecretsError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Armazenamento seguro de credenciais não configurado.") from exc
    conn = _get(db, actor.organization_id)
    if conn is None:
        conn = ErpConnection(organization_id=actor.organization_id, token_encrypted=encrypted)
        db.add(conn)
        action = "erp_connection.created"
    else:
        conn.token_encrypted, conn.is_active, conn.last_verified_at = encrypted, True, None
        action = "erp_connection.replaced"
    db.flush()
    audit.record(db, action=action, organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="erp_connection", entity_id=conn.id)
    db.commit()
    return _out(conn)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(db: DbSession, actor: AdminUser) -> None:
    conn = _get(db, actor.organization_id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conexão não configurada.")
    audit.record(db, action="erp_connection.deleted", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="erp_connection", entity_id=conn.id)
    db.delete(conn)
    db.commit()


@router.post("/connection/test", response_model=ErpConnectionOut)
def test_connection(db: DbSession, actor: AdminUser, erp: Erp) -> ErpConnectionOut:
    """Faz uma leitura barata no ERP para validar o token."""
    conn = _get(db, actor.organization_id)
    try:
        erp.check_connection()
    except ErpError as exc:
        audit.record(db, action="erp_connection.test_failed", organization_id=actor.organization_id,
                     actor_user_id=actor.id, details={"error": type(exc).__name__})
        db.commit()
        raise erp_http_error(exc) from exc
    if conn is not None:
        conn.last_verified_at = utcnow()
    audit.record(db, action="erp_connection.tested", organization_id=actor.organization_id,
                 actor_user_id=actor.id)
    db.commit()
    return _out(conn)


@router.get("/stock-locations", response_model=list[ErpStockLocationOut])
def list_erp_stock_locations(actor: AdminUser, erp: Erp) -> list[ErpStockLocationOut]:
    """Locais de estoque do ERP, para mapear ao `external_code` dos locais da plataforma."""
    try:
        return [ErpStockLocationOut(code=loc.code, name=loc.name, reference=loc.reference,
                                    active=loc.active) for loc in erp.list_stock_locations()]
    except ErpError as exc:
        raise erp_http_error(exc) from exc
