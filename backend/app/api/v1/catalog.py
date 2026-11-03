from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.api.erp_deps import get_erp_adapter
from app.integrations.erp_port import (
    ErpAdapter,
    ErpAuthError,
    ErpError,
    ErpInvalidRequestError,
    ErpNotAvailableError,
    ErpNotFoundError,
    ErpRateLimitedError,
    ErpTimeoutError,
)
from app.models.catalog import ProductReference, UnitReference
from app.models.user import Role, User
from app.schemas.catalog import ProductOut, StockBalanceOut, SyncResultOut, UnitOut
from app.services import locations as loc_svc
from app.services.catalog_sync import SyncConflictError, sync_catalog

router = APIRouter(tags=["catalog"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
Erp = Annotated[ErpAdapter, Depends(get_erp_adapter)]


def erp_http_error(exc: ErpError) -> HTTPException:
    if isinstance(exc, ErpNotAvailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Integração com o ERP indisponível.")
    if isinstance(exc, ErpTimeoutError):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "O ERP não respondeu a tempo.")
    if isinstance(exc, ErpRateLimitedError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                             "Limite de requisições do ERP excedido. Tente novamente em instantes.")
    if isinstance(exc, ErpAuthError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY,
                             "O ERP rejeitou as credenciais da organização. Reconfigure a conexão.")
    if isinstance(exc, ErpInvalidRequestError):
        return HTTPException(422, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Falha ao consultar o ERP.")


@router.post("/catalog/sync", response_model=SyncResultOut)
def sync(db: DbSession, actor: AdminUser, erp: Erp) -> SyncResultOut:
    try:
        return SyncResultOut(**sync_catalog(db, actor, erp).__dict__)
    except SyncConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ErpError as exc:
        raise erp_http_error(exc) from exc


@router.get("/units", response_model=list[UnitOut])
def list_units(db: DbSession, user: CurrentUser) -> list[UnitReference]:
    q = select(UnitReference).where(UnitReference.organization_id == user.organization_id)
    return list(db.scalars(q.order_by(UnitReference.code)))


@router.get("/products", response_model=list[ProductOut])
def list_products(
    db: DbSession,
    user: CurrentUser,
    q: Annotated[str | None, Query(max_length=100)] = None,
    barcode: Annotated[str | None, Query(max_length=50)] = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductReference]:
    stmt = select(ProductReference).where(ProductReference.organization_id == user.organization_id)
    if not include_inactive:
        stmt = stmt.where(ProductReference.is_active.is_(True))
    if barcode:
        stmt = stmt.where(ProductReference.barcode == barcode)
    if q:
        stmt = stmt.where(or_(
            ProductReference.name.icontains(q, autoescape=True),
            ProductReference.sku.icontains(q, autoescape=True),
            ProductReference.external_id == q,
        ))
    stmt = stmt.order_by(ProductReference.name, ProductReference.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def _get_product(db, user, product_id: UUID) -> ProductReference:
    p = db.scalar(select(ProductReference).where(
        ProductReference.id == product_id,
        ProductReference.organization_id == user.organization_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado.")
    return p


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: UUID, db: DbSession, user: CurrentUser) -> ProductReference:
    return _get_product(db, user, product_id)


@router.get("/products/{product_id}/stock", response_model=StockBalanceOut)
def get_stock(
    product_id: UUID, db: DbSession, user: CurrentUser, erp: Erp,
    location_id: UUID | None = None,
) -> StockBalanceOut:
    """Saldo lido do ERP (fonte oficial), sem cache e sem escrita."""
    product = _get_product(db, user, product_id)
    location_code = None
    if location_id is not None:
        try:
            loc = loc_svc.get_accessible_location(db, user, location_id)
        except loc_svc.NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if not loc.external_code:
            raise HTTPException(422,
                                "Local sem código do ERP mapeado.")
        location_code = loc.external_code
    try:
        bal = erp.get_stock_balance(product.external_id, location_code)
    except ErpNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado no ERP.") from exc
    except ErpError as exc:
        raise erp_http_error(exc) from exc
    unit = db.get(UnitReference, product.unit_id)
    return StockBalanceOut(
        product_id=product.id, location_id=location_id, quantity=bal.quantity,
        unit_code=unit.code if unit else "", reserved_quantity=bal.reserved_quantity,
        as_of=bal.as_of, last_changed_at=bal.last_changed_at)
