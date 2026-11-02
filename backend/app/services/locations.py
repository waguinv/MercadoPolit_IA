from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.catalog import StockLocation
from app.models.organization import Store
from app.models.user import User
from app.schemas.catalog import LocationCreate, LocationUpdate
from app.services import audit


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


def get_accessible_store(db: Session, user: User, store_id: UUID) -> Store:
    """Loja da organização do usuário e, se ele for restrito a uma loja, somente essa."""
    if user.store_id is not None and user.store_id != store_id:
        raise NotFoundError("Loja não encontrada.")
    store = db.scalar(select(Store).where(
        Store.id == store_id, Store.organization_id == user.organization_id))
    if store is None:
        raise NotFoundError("Loja não encontrada.")
    return store


def get_accessible_location(db: Session, user: User, location_id: UUID) -> StockLocation:
    loc = db.scalar(select(StockLocation).where(
        StockLocation.id == location_id,
        StockLocation.organization_id == user.organization_id))
    if loc is None or (user.store_id is not None and loc.store_id != user.store_id):
        raise NotFoundError("Local não encontrado.")
    return loc


def list_locations(db: Session, user: User, store_id: UUID) -> list[StockLocation]:
    get_accessible_store(db, user, store_id)
    return list(db.scalars(select(StockLocation).where(
        StockLocation.store_id == store_id,
        StockLocation.organization_id == user.organization_id).order_by(StockLocation.code)))


def create_location(db: Session, actor: User, store_id: UUID, data: LocationCreate) -> StockLocation:
    store = get_accessible_store(db, actor, store_id)
    loc = StockLocation(organization_id=store.organization_id, store_id=store.id,
                        code=data.code.strip(), name=data.name.strip(),
                        external_code=data.external_code)
    db.add(loc)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Já existe local com esse código nesta loja.") from exc
    audit.record(db, action="location.created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="stock_location", entity_id=loc.id,
                 details={"store_id": str(store.id), "code": loc.code})
    db.commit()
    return loc


def update_location(db: Session, actor: User, location_id: UUID, data: LocationUpdate) -> StockLocation:
    loc = get_accessible_location(db, actor, location_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        changes.pop("name")
    if "is_active" in changes and changes["is_active"] is None:
        changes.pop("is_active")
    for k, v in changes.items():
        setattr(loc, k, v)
    audit.record(db, action="location.updated", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="stock_location", entity_id=loc.id,
                 details={"fields": sorted(changes)})
    db.commit()
    return loc
