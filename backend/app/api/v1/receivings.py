from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.user import Role, User
from app.schemas.receiving import (
    CancelIn,
    FinalizeIn,
    ItemCreate,
    ItemOut,
    ItemUpdate,
    ReceivingCreate,
    ReceivingDetail,
    ReceivingOut,
)
from app.services import locations as loc_svc
from app.services import receiving as svc

router = APIRouter(prefix="/receivings", tags=["receivings"])
Manager = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER))]
Writer = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.OPERATOR))]


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (svc.NotFoundError, loc_svc.NotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc


def _item_out(item, product_name: str) -> ItemOut:
    return ItemOut.model_validate({**ItemOut.model_validate(item).model_dump(), **svc.with_diffs(item),
                                   "product_name": product_name})


def _one_item(db, item) -> ItemOut:
    return _item_out(item, svc.product_names(db, [item])[item.product_id])


def _detail(db, rec) -> ReceivingDetail:
    base = ReceivingOut.model_validate(rec).model_dump()
    items = svc.items_of(db, rec)
    names = svc.product_names(db, items)
    return ReceivingDetail(**base, items=[_item_out(i, names[i.product_id]) for i in items])


@router.post("", response_model=ReceivingOut)
def create(body: ReceivingCreate, db: DbSession, actor: Writer, response: Response):
    rec, created = _wrap(svc.create, db, actor, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return rec


@router.get("", response_model=list[ReceivingOut])
def list_all(db: DbSession, user: CurrentUser, store_id: UUID | None = None,
             status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    return svc.list_receivings(db, user, store_id, status_)


@router.get("/{receiving_id}", response_model=ReceivingDetail)
def get_one(receiving_id: UUID, db: DbSession, user: CurrentUser):
    return _detail(db, _wrap(svc.get_receiving, db, user, receiving_id))


@router.post("/{receiving_id}/items", response_model=ItemOut)
def add_item(receiving_id: UUID, body: ItemCreate, db: DbSession, actor: Writer, response: Response):
    item, created = _wrap(svc.add_item, db, actor, receiving_id, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _one_item(db, item)


@router.patch("/{receiving_id}/items/{item_id}", response_model=ItemOut)
def update_item(receiving_id: UUID, item_id: UUID, body: ItemUpdate, db: DbSession, actor: Writer):
    return _one_item(db, _wrap(svc.update_item, db, actor, receiving_id, item_id, body))


@router.delete("/{receiving_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(receiving_id: UUID, item_id: UUID, db: DbSession, actor: Writer):
    _wrap(svc.delete_item, db, actor, receiving_id, item_id)


@router.post("/{receiving_id}/finalize", response_model=ReceivingOut)
def finalize(receiving_id: UUID, body: FinalizeIn, db: DbSession, actor: Manager):
    return _wrap(svc.finalize, db, actor, receiving_id, body)


@router.post("/{receiving_id}/cancel", response_model=ReceivingOut)
def cancel(receiving_id: UUID, body: CancelIn, db: DbSession, actor: Manager):
    return _wrap(svc.cancel, db, actor, receiving_id, body.reason)
