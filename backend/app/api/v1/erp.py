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
from app.integrations.erp_port import ErpAdapter, ErpAuthError, ErpError
from app.integrations.registry import CONNECTORS, get_connector
from app.models.erp_connection import ErpConnection
from app.models.organization import Organization
from app.models.user import Role, User
from app.schemas.catalog import (
    ErpCheckOut,
    ErpConnectionIn,
    ErpConnectionOut,
    ErpConnectorOut,
    ErpStockLocationOut,
)
from app.services import audit
from app.services.erp_checks import run_read_checks

router = APIRouter(prefix="/erp", tags=["erp"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
Erp = Annotated[ErpAdapter, Depends(get_erp_adapter)]


def _get(db, org_id) -> ErpConnection | None:
    return db.scalar(select(ErpConnection).where(ErpConnection.organization_id == org_id))


def _out(conn: ErpConnection | None, checks: list[ErpCheckOut] | None = None) -> ErpConnectionOut:
    adapter = get_settings().erp_adapter
    if conn is None:
        return ErpConnectionOut(adapter=adapter, configured=False, checks=checks or [])
    return ErpConnectionOut(adapter=adapter, configured=True, kind=conn.kind, is_active=conn.is_active,
                            last_verified_at=conn.last_verified_at, checks=checks or [])


def _checks(erp: ErpAdapter) -> list[ErpCheckOut]:
    return [ErpCheckOut(**c.__dict__) for c in run_read_checks(erp)]


@router.get("/connectors", response_model=list[ErpConnectorOut])
def list_connectors(actor: AdminUser) -> list[ErpConnectorOut]:
    """Sistemas que esta versão da plataforma sabe ler."""
    return [ErpConnectorOut(kind=c.kind, name=c.name, token_label=c.token_label,
                            token_help=c.token_help) for c in CONNECTORS.values()]


@router.get("/connection", response_model=ErpConnectionOut)
def get_connection(db: DbSession, actor: AdminUser) -> ErpConnectionOut:
    return _out(_get(db, actor.organization_id))


@router.put("/connection", response_model=ErpConnectionOut)
def put_connection(body: ErpConnectionIn, db: DbSession, actor: AdminUser) -> ErpConnectionOut:
    """Testa o token no sistema escolhido e só então o guarda, cifrado. Token recusado não é salvo."""
    if get_settings().erp_adapter == "mock":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A integração real está desligada neste servidor (ERP simulado).")
    connector = get_connector(body.kind)
    if connector is None:
        raise HTTPException(422, "Sistema não disponível para integração.")
    org_id = actor.organization_id
    source = db.scalar(select(Organization.catalog_source).where(Organization.id == org_id))
    if source not in (None, body.kind):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Esta organização já tem catálogo de outra origem. "
                            "Para não misturar dados, conecte em uma organização nova.")
    token = body.token.strip()
    try:
        encrypted = encrypt_secret(token)
    except SecretsError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Armazenamento seguro de credenciais não configurado.") from exc
    erp = connector.build(token)
    try:
        try:
            erp.check_connection()
        except ErpError as exc:
            audit.record(db, action="erp_connection.test_failed", organization_id=org_id,
                         actor_user_id=actor.id,
                         details={"kind": body.kind, "error": type(exc).__name__})
            db.commit()
            if isinstance(exc, ErpAuthError):
                raise HTTPException(422, f"O {connector.name} recusou este token. Nada foi salvo.") from exc
            raise erp_http_error(exc) from exc
        conn = _get(db, org_id)
        if conn is None:
            conn = ErpConnection(organization_id=org_id, kind=body.kind, token_encrypted=encrypted)
            db.add(conn)
            action = "erp_connection.created"
        else:
            conn.kind, conn.token_encrypted, conn.is_active = body.kind, encrypted, True
            action = "erp_connection.replaced"
        conn.last_verified_at = utcnow()
        db.flush()
        audit.record(db, action=action, organization_id=org_id, actor_user_id=actor.id,
                     entity_type="erp_connection", entity_id=conn.id, details={"kind": body.kind})
        db.commit()  # A conexão vale mesmo que uma leitura abaixo falhe: a tela mostra qual.
        return _out(conn, _checks(erp))
    finally:
        erp.close()


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
    """Valida o token e confere as leituras básicas no ERP."""
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
    return _out(conn, _checks(erp))


@router.get("/stock-locations", response_model=list[ErpStockLocationOut])
def list_erp_stock_locations(actor: AdminUser, erp: Erp) -> list[ErpStockLocationOut]:
    """Locais de estoque do ERP, para mapear ao `external_code` dos locais da plataforma."""
    try:
        return [ErpStockLocationOut(code=loc.code, name=loc.name, reference=loc.reference,
                                    active=loc.active) for loc in erp.list_stock_locations()]
    except ErpError as exc:
        raise erp_http_error(exc) from exc
