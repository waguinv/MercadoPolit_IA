from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.catalog import StockLocation
from app.models.user import Role, User
from app.schemas.catalog import LocationCreate, LocationOut, LocationUpdate
from app.services import locations as svc

router = APIRouter(tags=["locations"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("/stores/{store_id}/locations", response_model=list[LocationOut])
def list_locations(store_id: UUID, db: DbSession, user: CurrentUser) -> list[StockLocation]:
    try:
        return svc.list_locations(db, user, store_id)
    except svc.NotFoundError as exc:
        raise _http(exc) from exc


@router.post("/stores/{store_id}/locations", response_model=LocationOut,
             status_code=status.HTTP_201_CREATED)
def create_location(store_id: UUID, body: LocationCreate, db: DbSession,
                    actor: AdminUser) -> StockLocation:
    try:
        return svc.create_location(db, actor, store_id, body)
    except (svc.NotFoundError, svc.ConflictError) as exc:
        raise _http(exc) from exc


@router.patch("/locations/{location_id}", response_model=LocationOut)
def update_location(location_id: UUID, body: LocationUpdate, db: DbSession,
                    actor: AdminUser) -> StockLocation:
    try:
        return svc.update_location(db, actor, location_id, body)
    except svc.NotFoundError as exc:
        raise _http(exc) from exc
