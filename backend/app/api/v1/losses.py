from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.catalog import ProductReference, StockLocation, UnitReference
from app.models.loss import StockLoss
from app.models.organization import Store
from app.models.user import Role, User
from app.schemas.consumption import ReasonIn
from app.schemas.loss import LossCreate, LossData, LossOut
from app.services import losses as svc

router = APIRouter(prefix="/stock-losses", tags=["stock-losses"])
Manager = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER))]
Writer = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.OPERATOR))]


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except svc.ForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc


def _names(db, model, ids, column) -> dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(db.execute(select(model.id, column).where(model.id.in_(ids))).all())


def _out(db, items: list[StockLoss]) -> list[LossOut]:
    products = _names(db, ProductReference, [i.product_id for i in items], ProductReference.name)
    units = _names(db, UnitReference, [i.unit_id for i in items], UnitReference.code)
    stores = _names(db, Store, [i.store_id for i in items], Store.name)
    locs = _names(db, StockLocation, [i.location_id for i in items], StockLocation.name)
    users = _names(db, User, [u for i in items for u in (i.registered_by, i.decided_by)], User.full_name)
    return [LossOut(
        id=i.id, store_id=i.store_id, location_id=i.location_id, product_id=i.product_id, unit_id=i.unit_id,
        quantity=i.quantity, reason=i.reason, lot=i.lot, expiry_date=i.expiry_date, note=i.note,
        registered_by=i.registered_by, status=i.status, decided_by=i.decided_by, decided_at=i.decided_at,
        decision_note=i.decision_note, erp_status=i.erp_status, created_at=i.created_at,
        updated_at=i.updated_at, product_name=products.get(i.product_id, ""),
        unit_code=units.get(i.unit_id, ""), store_name=stores.get(i.store_id, ""),
        location_name=locs.get(i.location_id, ""), registered_by_name=users.get(i.registered_by, ""),
        decided_by_name=users.get(i.decided_by),
    ) for i in items]


def _one(db, item) -> LossOut:
    return _out(db, [item])[0]


@router.get("", response_model=list[LossOut])
def list_all(db: DbSession, user: CurrentUser,
             status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    return _out(db, svc.list_losses(db, user, status_))


@router.post("", response_model=LossOut)
def create(body: LossCreate, db: DbSession, actor: Writer, response: Response):
    item, created = _wrap(svc.create, db, actor, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _one(db, item)


@router.get("/{loss_id}", response_model=LossOut)
def get_one(loss_id: UUID, db: DbSession, user: CurrentUser):
    return _one(db, _wrap(svc.get_loss, db, user, loss_id))


@router.patch("/{loss_id}", response_model=LossOut)
def correct(loss_id: UUID, body: LossData, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.correct, db, actor, loss_id, body))


@router.post("/{loss_id}/approve", response_model=LossOut)
def approve(loss_id: UUID, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.approve, db, actor, loss_id))


@router.post("/{loss_id}/reject", response_model=LossOut)
def reject(loss_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.reject, db, actor, loss_id, body.reason))


@router.post("/{loss_id}/request-correction", response_model=LossOut)
def request_correction(loss_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.request_correction, db, actor, loss_id, body.reason))


@router.post("/{loss_id}/cancel", response_model=LossOut)
def cancel(loss_id: UUID, body: ReasonIn, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.cancel, db, actor, loss_id, body.reason))
