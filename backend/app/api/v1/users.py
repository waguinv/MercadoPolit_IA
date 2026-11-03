from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, require_roles
from app.models.user import Role, User
from app.schemas.auth import UserCreate, UserOut, UserUpdate
from app.services import tenant_admin as svc

router = APIRouter(prefix="/users", tags=["users"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession, actor: AdminUser) -> User:
    try:
        return svc.create_user(db, actor, body)
    except svc.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: UUID, body: UserUpdate, db: DbSession, actor: AdminUser) -> User:
    try:
        return svc.update_user(db, actor, user_id, body)
    except svc.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("", response_model=list[UserOut])
def list_users(db: DbSession, actor: AdminUser) -> list[User]:
    q = select(User).where(User.organization_id == actor.organization_id).order_by(User.email)
    return list(db.scalars(q))
