from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.catalog import ProductReference, StockLocation, UnitReference
from app.models.organization import Store
from app.models.receiving import Receiving
from app.models.supplier_return import SupplierReturn
from app.models.user import Role, User
from app.schemas.consumption import ReasonIn
from app.schemas.supplier_return import (
    PickupIn,
    ReplacementIn,
    ReplacementOut,
    ReturnCreate,
    ReturnData,
    ReturnOut,
)
from app.services import supplier_returns as svc

router = APIRouter(prefix="/supplier-returns", tags=["supplier-returns"])
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


def _out(db, items: list[SupplierReturn]) -> list[ReturnOut]:
    replacements = svc.replacements_of(db, [i.id for i in items])
    products = _names(db, ProductReference, [i.product_id for i in items], ProductReference.name)
    units = _names(db, UnitReference, [i.unit_id for i in items], UnitReference.code)
    stores = _names(db, Store, [i.store_id for i in items], Store.name)
    locs = _names(db, StockLocation, [i.location_id for i in items], StockLocation.name)
    docs = _names(db, Receiving, [r.receiving_id for r in replacements], Receiving.document_number)
    people = [u for i in items for u in (i.registered_by, i.decided_by, i.picked_up_by, i.completed_by)]
    users = _names(db, User, people + [r.registered_by for r in replacements], User.full_name)

    def of(item_id):
        return [ReplacementOut(id=r.id, receiving_id=r.receiving_id, document_number=docs.get(r.receiving_id),
                               quantity=r.quantity, registered_by_name=users.get(r.registered_by, ""),
                               created_at=r.created_at)
                for r in replacements if r.return_id == item_id]

    result = []
    for i in items:
        own = of(i.id)
        result.append(ReturnOut(
            id=i.id, store_id=i.store_id, location_id=i.location_id, product_id=i.product_id,
            unit_id=i.unit_id, quantity=i.quantity, reason=i.reason, modality=i.modality,
            supplier_name=i.supplier_name, supplier_document=i.supplier_document, receiving_id=i.receiving_id,
            origin_document=i.origin_document, lot=i.lot, expiry_date=i.expiry_date, note=i.note,
            expected_pickup_date=i.expected_pickup_date,
            expected_replacement_date=i.expected_replacement_date, registered_by=i.registered_by,
            status=i.status, decided_by=i.decided_by, decided_at=i.decided_at, decision_note=i.decision_note,
            picked_up_at=i.picked_up_at, pickup_note=i.pickup_note, completed_at=i.completed_at,
            completion_note=i.completion_note, erp_status=i.erp_status, created_at=i.created_at,
            updated_at=i.updated_at, product_name=products.get(i.product_id, ""),
            unit_code=units.get(i.unit_id, ""), store_name=stores.get(i.store_id, ""),
            location_name=locs.get(i.location_id, ""), registered_by_name=users.get(i.registered_by, ""),
            decided_by_name=users.get(i.decided_by), picked_up_by_name=users.get(i.picked_up_by),
            completed_by_name=users.get(i.completed_by), replaced_quantity=sum(r.quantity for r in own),
            replacements=own,
        ))
    return result


def _one(db, item) -> ReturnOut:
    return _out(db, [item])[0]


@router.get("", response_model=list[ReturnOut])
def list_all(db: DbSession, user: CurrentUser,
             status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    return _out(db, svc.list_returns(db, user, status_))


@router.post("", response_model=ReturnOut)
def create(body: ReturnCreate, db: DbSession, actor: Writer, response: Response):
    item, created = _wrap(svc.create, db, actor, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _one(db, item)


@router.get("/{return_id}", response_model=ReturnOut)
def get_one(return_id: UUID, db: DbSession, user: CurrentUser):
    return _one(db, _wrap(svc.get_return, db, user, return_id))


@router.patch("/{return_id}", response_model=ReturnOut)
def correct(return_id: UUID, body: ReturnData, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.correct, db, actor, return_id, body))


@router.post("/{return_id}/approve", response_model=ReturnOut)
def approve(return_id: UUID, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.approve, db, actor, return_id))


@router.post("/{return_id}/reject", response_model=ReturnOut)
def reject(return_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.reject, db, actor, return_id, body.reason))


@router.post("/{return_id}/request-correction", response_model=ReturnOut)
def request_correction(return_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.request_correction, db, actor, return_id, body.reason))


@router.post("/{return_id}/cancel", response_model=ReturnOut)
def cancel(return_id: UUID, body: ReasonIn, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.cancel, db, actor, return_id, body.reason))


@router.post("/{return_id}/pickup", response_model=ReturnOut)
def pickup(return_id: UUID, body: PickupIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.pickup, db, actor, return_id, body.note))


@router.post("/{return_id}/replacements", response_model=ReturnOut)
def add_replacement(return_id: UUID, body: ReplacementIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.add_replacement, db, actor, return_id, body))


@router.post("/{return_id}/complete", response_model=ReturnOut)
def complete(return_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.complete, db, actor, return_id, body.reason))
