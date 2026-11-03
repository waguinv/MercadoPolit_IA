from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_roles
from app.api.erp_deps import get_erp_adapter
from app.integrations.erp_port import ErpAdapter, ErpNotFoundError
from app.models.inventory import (
    InventoryAssignment,
    InventoryRecount,
    InventorySession,
    ItemStatus,
    SessionStatus,
)
from app.models.user import Role, User
from app.schemas.inventory import (
    AssignmentIn,
    AssignmentOut,
    CancelIn,
    CountIn,
    CountOut,
    ErpCheckOut,
    ErpCheckResultOut,
    ItemDetailOut,
    ItemOut,
    PendingRecountOut,
    RecountIn,
    RecountOut,
    ResolveIn,
    SessionCreate,
    SessionDetailOut,
    SessionOut,
)
from app.services import inventory as svc

router = APIRouter(prefix="/inventory-sessions", tags=["inventory"])
Manager = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER))]
Counter = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.OPERATOR))]
# Operador não vê a revisão: a contagem é cega (sem saldo do ERP nem contagens alheias).
Reviewer = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.AUDITOR))]
Erp = Annotated[ErpAdapter, Depends(get_erp_adapter)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, svc.ForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, svc.InvalidError):
        return HTTPException(422, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


_DOMAIN_ERRORS = (svc.NotFoundError, svc.ForbiddenError, svc.InvalidError, svc.ConflictError)


def _item_out(item, product_name: str) -> dict:
    return {
        "id": item.id, "product_id": item.product_id, "product_name": product_name,
        "unit_id": item.unit_id, "status": item.status, "current_round": item.current_round,
        "review_reasons": item.review_reasons or [], "accepted_count_id": item.accepted_count_id,
        "accepted_quantity": item.accepted_quantity, "resolved_by_id": item.resolved_by_id,
        "resolved_at": item.resolved_at, "resolution_note": item.resolution_note,
    }


def _count_out(count, product_id: UUID) -> CountOut:
    return CountOut(
        id=count.id, product_id=product_id, round=count.round, quantity=count.quantity,
        unit_id=count.unit_id, unit_issue=count.unit_issue, counted_at=count.counted_at,
        client_key=count.client_key, note=count.note)


# ---------------- Sessões ----------------
@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreate, db: DbSession, actor: Manager) -> InventorySession:
    try:
        return svc.create_session(db, actor, body)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.get("", response_model=list[SessionOut])
def list_sessions(
    db: DbSession,
    user: CurrentUser,
    store_id: UUID | None = None,
    session_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InventorySession]:
    return svc.list_sessions(db, user, store_id, session_status, limit, offset)


@router.get("/{session_id}", response_model=SessionDetailOut)
def get_session(session_id: UUID, db: DbSession, user: CurrentUser) -> SessionDetailOut:
    try:
        session = svc.get_session(db, user, session_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    totals = svc.items_by_status(db, session)
    return SessionDetailOut(**SessionOut.model_validate(session).model_dump(),
                            items_total=sum(totals.values()), items_by_status=totals)


@router.post("/{session_id}/submit-review", response_model=SessionOut)
def submit_review(session_id: UUID, db: DbSession, actor: Manager) -> InventorySession:
    try:
        return svc.submit_for_review(db, actor, session_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.post("/{session_id}/close", response_model=SessionOut)
def close_session(session_id: UUID, db: DbSession, actor: Manager) -> InventorySession:
    """Aprova a contagem. NÃO envia nada ao ERP."""
    try:
        return svc.close_session(db, actor, session_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.post("/{session_id}/cancel", response_model=SessionOut)
def cancel_session(session_id: UUID, body: CancelIn, db: DbSession, actor: Manager) -> InventorySession:
    try:
        return svc.cancel_session(db, actor, session_id, body.reason)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


# ---------------- Atribuições ----------------
@router.get("/{session_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(session_id: UUID, db: DbSession, user: Reviewer) -> list[InventoryAssignment]:
    try:
        return svc.list_assignments(db, user, session_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.post("/{session_id}/assignments", response_model=AssignmentOut, status_code=status.HTTP_201_CREATED)
def add_assignment(session_id: UUID, body: AssignmentIn, db: DbSession,
                   actor: Manager) -> InventoryAssignment:
    try:
        return svc.add_assignment(db, actor, session_id, body.user_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.delete("/{session_id}/assignments/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_assignment(session_id: UUID, user_id: UUID, db: DbSession, actor: Manager) -> None:
    try:
        svc.remove_assignment(db, actor, session_id, user_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


# ---------------- Contagens ----------------
@router.post("/{session_id}/counts", response_model=CountOut, status_code=status.HTTP_201_CREATED)
def record_count(session_id: UUID, body: CountIn, response: Response, db: DbSession,
                 user: Counter) -> CountOut:
    """Salva na hora. Reenvio com a mesma `client_key` devolve 200 com o registro original."""
    try:
        count, product_id, created = svc.record_count(db, user, session_id, body)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return _count_out(count, product_id)


@router.get("/{session_id}/my-counts", response_model=list[CountOut])
def my_counts(session_id: UUID, db: DbSession, user: Counter) -> list[CountOut]:
    try:
        return [_count_out(c, pid) for c, pid in svc.list_my_counts(db, user, session_id)]
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.get("/{session_id}/pending-recounts", response_model=list[PendingRecountOut])
def pending_recounts(session_id: UUID, db: DbSession, user: Counter) -> list[dict]:
    try:
        return svc.list_pending_recounts(db, user, session_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


# ---------------- Revisão ----------------
@router.get("/{session_id}/items", response_model=list[ItemOut])
def list_items(
    session_id: UUID,
    db: DbSession,
    user: Reviewer,
    item_status: Annotated[ItemStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    try:
        rows = svc.list_items(db, user, session_id, item_status, limit, offset)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    return [_item_out(item, name) for item, name in rows]


@router.get("/{session_id}/items/{item_id}", response_model=ItemDetailOut)
def get_item(session_id: UUID, item_id: UUID, db: DbSession, user: Reviewer) -> dict:
    try:
        d = svc.get_item_detail(db, user, session_id, item_id)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    return {**_item_out(d["item"], d["product_name"]), "counts": d["counts"],
            "recounts": d["recounts"], "erp_checks": d["erp_checks"]}


@router.post("/{session_id}/recounts", response_model=RecountOut, status_code=status.HTTP_201_CREATED)
def request_recount(session_id: UUID, body: RecountIn, db: DbSession, actor: Manager) -> InventoryRecount:
    try:
        return svc.request_recount(db, actor, session_id, body)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.post("/{session_id}/items/{item_id}/resolve", response_model=ItemOut)
def resolve_item(session_id: UUID, item_id: UUID, body: ResolveIn, db: DbSession, actor: Manager) -> dict:
    try:
        item = svc.resolve_item(db, actor, session_id, item_id, body)
        return _item_out(item, svc.get_item_detail(db, actor, session_id, item_id)["product_name"])
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc


@router.post("/{session_id}/items/{item_id}/erp-check", response_model=ErpCheckResultOut)
def erp_check(session_id: UUID, item_id: UUID, db: DbSession, actor: Manager, erp: Erp) -> ErpCheckResultOut:
    """Relê o saldo no ERP (somente leitura) e compara com a contagem."""
    try:
        r = svc.check_item_against_erp(db, actor, session_id, item_id, erp)
    except ErpNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado no ERP.") from exc
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    return ErpCheckResultOut(check=ErpCheckOut.model_validate(r.check), difference=r.difference,
                             item_status=r.item.status, review_reasons=r.item.review_reasons or [])
