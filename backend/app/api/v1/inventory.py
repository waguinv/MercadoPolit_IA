from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_roles
from app.api.erp_deps import get_erp_adapter
from app.api.v1.catalog import erp_http_error
from app.integrations.erp_port import ErpAdapter, ErpError
from app.models.user import Role, User
from app.schemas.inventory import (
    CancelIn,
    CloseCountingOut,
    CountCreate,
    CountOut,
    DiscrepancyOut,
    ProposalOut,
    RecountIn,
    ResolveIn,
    SessionCreate,
    SessionOut,
    SessionProgress,
)
from app.services import inventory as svc
from app.services import locations as loc_svc

router = APIRouter(prefix="/inventory-sessions", tags=["inventory"])
Manager = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER))]
Counter = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.OPERATOR))]
Reader = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.AUDITOR))]
Erp = Annotated[ErpAdapter, Depends(get_erp_adapter)]


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (svc.NotFoundError, loc_svc.NotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ErpError as exc:
        raise erp_http_error(exc) from exc


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreate, db: DbSession, actor: Manager, erp: Erp):
    return _wrap(svc.create_session, db, actor, erp, body)


@router.get("", response_model=list[SessionOut])
def list_sessions(db: DbSession, user: CurrentUser, store_id: UUID | None = None,
                  status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    return svc.list_sessions(db, user, store_id, status_)


@router.get("/{session_id}", response_model=SessionProgress)
def get_session(session_id: UUID, db: DbSession, user: CurrentUser):
    inv = _wrap(svc.get_session_for, db, user, session_id)
    data = SessionOut.model_validate(inv).model_dump()
    prog = svc.progress(db, inv)
    if user.role == Role.OPERATOR:  # contagem às cegas: sem dados que revelem o saldo do ERP
        prog["baseline_items"] = 0
        prog["discrepancies_open"] = 0
    return SessionProgress(**data, **prog)


@router.post("/{session_id}/counts", response_model=CountOut)
def add_count(session_id: UUID, body: CountCreate, db: DbSession, actor: Counter, response: Response):
    count, created = _wrap(svc.add_count, db, actor, session_id, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return count


@router.get("/{session_id}/counts", response_model=list[CountOut])
def list_counts(session_id: UUID, db: DbSession, user: CurrentUser, product_id: UUID | None = None,
                round_no: Annotated[int | None, Query(ge=1)] = None):
    inv = _wrap(svc.get_session_for, db, user, session_id)
    return svc.list_counts(db, user, inv, product_id, round_no)


@router.post("/{session_id}/close-counting", response_model=CloseCountingOut)
def close_counting(session_id: UUID, db: DbSession, actor: Manager, erp: Erp):
    return _wrap(svc.close_counting, db, actor, erp, session_id)


@router.get("/{session_id}/discrepancies", response_model=list[DiscrepancyOut])
def list_discrepancies(session_id: UUID, db: DbSession, user: Reader,
                       status_: Annotated[str | None, Query(alias="status", max_length=20)] = None):
    inv = _wrap(svc.get_session_for, db, user, session_id)
    return svc.list_discrepancies(db, inv, status_)


@router.post("/{session_id}/discrepancies/{discrepancy_id}/resolve", response_model=DiscrepancyOut)
def resolve(session_id: UUID, discrepancy_id: UUID, body: ResolveIn, db: DbSession, actor: Manager):
    return _wrap(svc.resolve, db, actor, session_id, discrepancy_id, body)


@router.post("/{session_id}/recounts", response_model=SessionOut)
def request_recount(session_id: UUID, body: RecountIn, db: DbSession, actor: Manager, erp: Erp):
    return _wrap(svc.request_recount, db, actor, erp, session_id, body.discrepancy_ids, body.reason)


@router.post("/{session_id}/finalize", response_model=SessionOut)
def finalize(session_id: UUID, db: DbSession, actor: Manager):
    return _wrap(svc.finalize, db, actor, session_id)


@router.post("/{session_id}/cancel", response_model=SessionOut)
def cancel(session_id: UUID, body: CancelIn, db: DbSession, actor: Manager):
    return _wrap(svc.cancel, db, actor, session_id, body.reason)


@router.get("/{session_id}/proposals", response_model=list[ProposalOut])
def list_proposals(session_id: UUID, db: DbSession, user: Reader):
    inv = _wrap(svc.get_session_for, db, user, session_id)
    return svc.list_proposals(db, inv)
