from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.organization import Store
from app.models.user import Role, User
from app.schemas.auth import StoreCreate, StoreOut
from app.services import tenant_admin as svc

router = APIRouter(prefix="/stores", tags=["stores"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
def create_store(body: StoreCreate, db: DbSession, actor: AdminUser) -> Store:
    try:
        return svc.create_store(db, actor, body)
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("", response_model=list[StoreOut])
def list_stores(db: DbSession, user: CurrentUser) -> list[Store]:
    q = select(Store).where(Store.organization_id == user.organization_id)
    if user.store_id is not None:  # usuário restrito a uma loja
        q = q.where(Store.id == user.store_id)
    return list(db.scalars(q.order_by(Store.code)))


@router.get("/{store_id}", response_model=StoreOut)
def get_store(store_id: UUID, db: DbSession, user: CurrentUser) -> Store:
    if user.store_id is not None and user.store_id != store_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loja não encontrada.")
    try:
        return svc.get_store(db, user.organization_id, store_id)
    except svc.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
