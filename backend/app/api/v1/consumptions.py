from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.models.catalog import ProductReference, StockLocation, UnitReference
from app.models.consumption import ConsumptionDepartment, ConsumptionPurpose, InternalConsumption
from app.models.organization import Store
from app.models.user import Role, User
from app.schemas.consumption import (
    ConsumerOut,
    ConsumptionConfigOut,
    ConsumptionCorrection,
    ConsumptionCreate,
    ConsumptionOut,
    DepartmentIn,
    DepartmentOut,
    DepartmentUpdate,
    PurposeIn,
    PurposeOut,
    PurposeUpdate,
    ReasonIn,
    SettingsIn,
)
from app.services import consumption as svc

router = APIRouter(tags=["internal-consumptions"])
Admin = Annotated[User, Depends(require_roles(Role.ADMIN))]
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


def _out(db, items: list[InternalConsumption]) -> list[ConsumptionOut]:
    products = _names(db, ProductReference, [i.product_id for i in items], ProductReference.name)
    units = _names(db, UnitReference, [i.unit_id for i in items], UnitReference.code)
    deps = _names(db, ConsumptionDepartment, [i.department_id for i in items], ConsumptionDepartment.name)
    purs = _names(db, ConsumptionPurpose, [i.purpose_id for i in items], ConsumptionPurpose.name)
    stores = _names(db, Store, [i.store_id for i in items], Store.name)
    locs = _names(db, StockLocation, [i.location_id for i in items], StockLocation.name)
    users = _names(db, User, [u for i in items for u in (i.registered_by, i.decided_by)], User.full_name)
    return [ConsumptionOut(
        id=i.id, store_id=i.store_id, location_id=i.location_id, department_id=i.department_id,
        purpose_id=i.purpose_id, product_id=i.product_id, unit_id=i.unit_id, quantity=i.quantity,
        consumer_user_id=i.consumer_user_id, consumer_name=i.consumer_name, registered_by=i.registered_by,
        status=i.status, note=i.note, decided_by=i.decided_by, decided_at=i.decided_at,
        decision_note=i.decision_note, erp_status=i.erp_status, created_at=i.created_at,
        updated_at=i.updated_at,
        product_name=products.get(i.product_id, ""), unit_code=units.get(i.unit_id, ""),
        department_name=deps.get(i.department_id, ""), purpose_name=purs.get(i.purpose_id, ""),
        store_name=stores.get(i.store_id, ""), location_name=locs.get(i.location_id, ""),
        registered_by_name=users.get(i.registered_by, ""), decided_by_name=users.get(i.decided_by),
    ) for i in items]


def _one(db, item) -> ConsumptionOut:
    return _out(db, [item])[0]


# ----------------------------------------------------------------- configuração
@router.get("/consumption-config", response_model=ConsumptionConfigOut)
def get_config(db: DbSession, user: CurrentUser):
    segregation, deps, purs = svc.config(db, user)
    return ConsumptionConfigOut(segregation=segregation, departments=deps, purposes=purs)


@router.put("/consumption-config/settings", status_code=status.HTTP_204_NO_CONTENT)
def put_settings(body: SettingsIn, db: DbSession, actor: Admin):
    svc.set_segregation(db, actor, body.segregation)


@router.post("/consumption-config/defaults", status_code=status.HTTP_204_NO_CONTENT)
def add_defaults(db: DbSession, actor: Admin):
    svc.add_defaults(db, actor)


@router.post("/consumption-config/departments", response_model=DepartmentOut, status_code=201)
def create_department(body: DepartmentIn, db: DbSession, actor: Admin):
    return _wrap(svc.create_department, db, actor, body)


@router.patch("/consumption-config/departments/{dep_id}", response_model=DepartmentOut)
def update_department(dep_id: UUID, body: DepartmentUpdate, db: DbSession, actor: Admin):
    return _wrap(svc.update_department, db, actor, dep_id, body)


@router.post("/consumption-config/purposes", response_model=PurposeOut, status_code=201)
def create_purpose(body: PurposeIn, db: DbSession, actor: Admin):
    return _wrap(svc.create_purpose, db, actor, body)


@router.patch("/consumption-config/purposes/{pur_id}", response_model=PurposeOut)
def update_purpose(pur_id: UUID, body: PurposeUpdate, db: DbSession, actor: Admin):
    return _wrap(svc.update_purpose, db, actor, pur_id, body)


@router.get("/consumption-config/consumers", response_model=list[ConsumerOut])
def list_consumers(db: DbSession, actor: Manager):
    return [ConsumerOut(id=u.id, name=u.full_name) for u in svc.consumers(db, actor)]


# ----------------------------------------------------------------- consumos
@router.get("/internal-consumptions", response_model=list[ConsumptionOut])
def list_all(db: DbSession, user: CurrentUser,
             status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    return _out(db, svc.list_consumptions(db, user, status_))


@router.post("/internal-consumptions", response_model=ConsumptionOut)
def create(body: ConsumptionCreate, db: DbSession, actor: Writer, response: Response):
    item, created = _wrap(svc.create, db, actor, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _one(db, item)


@router.get("/internal-consumptions/{consumption_id}", response_model=ConsumptionOut)
def get_one(consumption_id: UUID, db: DbSession, user: CurrentUser):
    return _one(db, _wrap(svc.get_consumption, db, user, consumption_id))


@router.patch("/internal-consumptions/{consumption_id}", response_model=ConsumptionOut)
def correct(consumption_id: UUID, body: ConsumptionCorrection, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.correct, db, actor, consumption_id, body))


@router.post("/internal-consumptions/{consumption_id}/approve", response_model=ConsumptionOut)
def approve(consumption_id: UUID, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.approve, db, actor, consumption_id))


@router.post("/internal-consumptions/{consumption_id}/reject", response_model=ConsumptionOut)
def reject(consumption_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.reject, db, actor, consumption_id, body.reason))


@router.post("/internal-consumptions/{consumption_id}/request-correction", response_model=ConsumptionOut)
def request_correction(consumption_id: UUID, body: ReasonIn, db: DbSession, actor: Manager):
    return _one(db, _wrap(svc.request_correction, db, actor, consumption_id, body.reason))


@router.post("/internal-consumptions/{consumption_id}/cancel", response_model=ConsumptionOut)
def cancel(consumption_id: UUID, body: ReasonIn, db: DbSession, actor: Writer):
    return _one(db, _wrap(svc.cancel, db, actor, consumption_id, body.reason))
