"""Inventário colaborativo. Nenhuma escrita no ERP: o saldo só é LIDO, durante a revisão.

Regras centrais:
- Contagens são imutáveis e nunca somadas; só a rodada atual do item é avaliada.
- O operador grava a contagem mesmo que o ERP esteja fora do ar (o ERP não participa do registro).
- Divergência, duplicidade, unidade incompatível ou movimento no ERP levam o item para revisão humana.
- Sessão encerrada significa contagem aprovada, não saldo aplicado no ERP.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.integrations.erp_port import ErpAdapter
from app.models.catalog import ProductReference, StockLocation, UnitReference
from app.models.inventory import (
    InventoryAssignment,
    InventoryCount,
    InventoryErpCheck,
    InventoryItem,
    InventoryRecount,
    InventorySession,
    ItemStatus,
    SessionStatus,
)
from app.models.user import Role, User
from app.schemas.inventory import CountIn, RecountIn, ResolveIn, SessionCreate
from app.services import audit
from app.services.locations import ConflictError, NotFoundError, get_accessible_location

COUNT_CONFLICT = "count_conflict"
DUPLICATE_COUNT = "duplicate_count"
COUNT_AFTER_RESOLUTION = "count_after_resolution"
ERP_MOVEMENT = "erp_movement_after_count"
ERP_UNVERIFIABLE = "erp_movement_unverifiable"
ERP_DIFFERENCE = "erp_difference"
UNIT_MISMATCH = "unit_mismatch"
FRACTION_NOT_ALLOWED = "fraction_not_allowed"

# Motivos que não dependem só das contagens da rodada: sobrevivem a uma nova avaliação.
_STICKY_REASONS = {COUNT_AFTER_RESOLUTION, ERP_MOVEMENT, ERP_UNVERIFIABLE, ERP_DIFFERENCE}
_ACTIVE = (SessionStatus.OPEN, SessionStatus.IN_REVIEW)
_QTY = Decimal("0.0001")
# O ERP não informa o fuso de dataHora. O Brasil vai até UTC-5: um horário sem fuso pode estar até
# 5h "atrasado". Na dúvida, tratamos como possivelmente posterior à contagem (leva à revisão).
_ERP_CLOCK_MARGIN = timedelta(hours=5)


class ForbiddenError(Exception):
    pass


class InvalidError(Exception):
    pass


@dataclass(frozen=True)
class ErpCheckResult:
    check: InventoryErpCheck
    item: InventoryItem
    difference: Decimal | None


def _as_utc(value: datetime) -> datetime:
    # SQLite devolve datetimes sem fuso; gravamos sempre em UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _maybe_after(erp_changed_at: datetime, counted_at: datetime) -> bool:
    counted = _as_utc(counted_at)
    if erp_changed_at.tzinfo:
        return erp_changed_at >= counted
    return erp_changed_at.replace(tzinfo=UTC) + _ERP_CLOCK_MARGIN >= counted


# ---------------- Acesso ----------------
def get_session(db: Session, user: User, session_id: UUID, lock: str | None = None) -> InventorySession:
    """Sessão da organização do usuário e, se ele for restrito a uma loja, somente dessa loja.

    Registrar/decidir usa trava compartilhada; mudar o estado da sessão, exclusiva. Assim uma
    contagem nunca entra em uma sessão que está sendo encerrada.
    """
    stmt = select(InventorySession).where(
        InventorySession.id == session_id, InventorySession.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(InventorySession.store_id == user.store_id)
    if lock is not None:
        stmt = stmt.with_for_update(read=lock == "share").execution_options(populate_existing=True)
    session = db.scalar(stmt)
    if session is None:
        raise NotFoundError("Sessão de inventário não encontrada.")
    return session


def get_item(db: Session, session: InventorySession, item_id: UUID, lock: bool = False) -> InventoryItem:
    stmt = select(InventoryItem).where(
        InventoryItem.id == item_id, InventoryItem.session_id == session.id,
        InventoryItem.organization_id == session.organization_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Item de inventário não encontrado.")
    return item


def _require_active(session: InventorySession) -> None:
    if session.status not in _ACTIVE:
        raise ConflictError("A sessão de inventário já foi encerrada ou cancelada.")


def _validate_assignee(db: Session, session: InventorySession, user_id: UUID) -> User:
    # Mesma resposta para outro tenant ou inexistente: não revela existência.
    target = db.scalar(select(User).where(
        User.id == user_id, User.organization_id == session.organization_id))
    if (target is None or not target.is_active or target.role == Role.AUDITOR
            or (target.store_id is not None and target.store_id != session.store_id)):
        raise InvalidError("Usuário inválido para esta sessão.")
    return target


def ensure_can_count(db: Session, user: User, session: InventorySession) -> None:
    if user.role != Role.OPERATOR:
        return
    assigned = list(db.scalars(select(InventoryAssignment.user_id).where(
        InventoryAssignment.session_id == session.id)))
    if assigned and user.id not in assigned:
        raise ForbiddenError("Você não está atribuído a esta sessão.")


# ---------------- Sessões ----------------
def create_session(db: Session, actor: User, data: SessionCreate) -> InventorySession:
    location = get_accessible_location(db, actor, data.location_id)
    # Serializa a criação por local: duas sessões sobrepostas contariam o mesmo estoque.
    db.scalar(select(StockLocation.id).where(StockLocation.id == location.id).with_for_update())
    if not location.is_active:
        raise InvalidError("Local de estoque inativo.")
    sector = (data.sector or "").strip() or None
    overlap = select(InventorySession.id).where(
        InventorySession.location_id == location.id, InventorySession.status.in_(_ACTIVE))
    if sector is not None:
        overlap = overlap.where(or_(
            InventorySession.sector.is_(None),
            func.lower(InventorySession.sector) == sector.lower()))
    if db.scalar(overlap.limit(1)) is not None:
        raise ConflictError("Já existe sessão ativa que abrange este local/setor.")
    session = InventorySession(
        organization_id=actor.organization_id, store_id=location.store_id, location_id=location.id,
        sector=sector, name=data.name.strip(), created_by_id=actor.id, status=SessionStatus.OPEN)
    db.add(session)
    db.flush()
    audit.record(db, action="inventory.session_created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id,
                 details={"location_id": str(location.id), "sector": sector})
    db.commit()
    return session


def list_sessions(db: Session, user: User, store_id: UUID | None, status: SessionStatus | None,
                  limit: int, offset: int) -> list[InventorySession]:
    stmt = select(InventorySession).where(InventorySession.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(InventorySession.store_id == user.store_id)
    if store_id is not None:
        stmt = stmt.where(InventorySession.store_id == store_id)
    if status is not None:
        stmt = stmt.where(InventorySession.status == status)
    stmt = stmt.order_by(InventorySession.created_at.desc(), InventorySession.id)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


def items_by_status(db: Session, session: InventorySession) -> dict[str, int]:
    rows = db.execute(select(InventoryItem.status, func.count()).where(
        InventoryItem.session_id == session.id).group_by(InventoryItem.status))
    return {status.value: total for status, total in rows}


def submit_for_review(db: Session, actor: User, session_id: UUID) -> InventorySession:
    session = get_session(db, actor, session_id, lock="update")
    if session.status != SessionStatus.OPEN:
        raise ConflictError("Só uma sessão aberta pode ir para revisão.")
    session.status = SessionStatus.IN_REVIEW
    audit.record(db, action="inventory.session_in_review", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id)
    db.commit()
    return session


def close_session(db: Session, actor: User, session_id: UUID) -> InventorySession:
    session = get_session(db, actor, session_id, lock="update")
    if session.status != SessionStatus.IN_REVIEW:
        raise ConflictError("Só uma sessão em revisão pode ser encerrada.")
    totals = items_by_status(db, session)
    if not totals:
        raise ConflictError("Sessão sem contagens: cancele em vez de encerrar.")
    pending = totals.get(ItemStatus.NEEDS_REVIEW.value, 0) + totals.get(ItemStatus.RECOUNT_REQUESTED.value, 0)
    if pending:
        raise ConflictError(f"Há {pending} item(ns) pendente(s) de revisão ou reconferência.")
    session.status = SessionStatus.CLOSED
    session.closed_by_id = actor.id
    session.closed_at = utcnow()
    audit.record(db, action="inventory.session_closed", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id,
                 details={"items": totals})
    db.commit()
    return session


def cancel_session(db: Session, actor: User, session_id: UUID, reason: str) -> InventorySession:
    session = get_session(db, actor, session_id, lock="update")
    _require_active(session)
    session.status = SessionStatus.CANCELLED
    session.closed_by_id = actor.id
    session.closed_at = utcnow()
    session.cancel_reason = reason.strip()
    audit.record(db, action="inventory.session_cancelled", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id,
                 details={"reason": session.cancel_reason})
    db.commit()
    return session


# ---------------- Atribuições ----------------
def list_assignments(db: Session, user: User, session_id: UUID) -> list[InventoryAssignment]:
    session = get_session(db, user, session_id)
    return list(db.scalars(select(InventoryAssignment).where(
        InventoryAssignment.session_id == session.id).order_by(InventoryAssignment.created_at)))


def add_assignment(db: Session, actor: User, session_id: UUID, user_id: UUID) -> InventoryAssignment:
    session = get_session(db, actor, session_id, lock="share")
    _require_active(session)
    _validate_assignee(db, session, user_id)
    assignment = InventoryAssignment(
        organization_id=session.organization_id, session_id=session.id, user_id=user_id)
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Usuário já atribuído a esta sessão.") from exc
    audit.record(db, action="inventory.assignment_added", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id,
                 details={"user_id": str(user_id)})
    db.commit()
    return assignment


def remove_assignment(db: Session, actor: User, session_id: UUID, user_id: UUID) -> None:
    session = get_session(db, actor, session_id, lock="share")
    _require_active(session)
    assignment = db.scalar(select(InventoryAssignment).where(
        InventoryAssignment.session_id == session.id, InventoryAssignment.user_id == user_id))
    if assignment is None:
        raise NotFoundError("Atribuição não encontrada.")
    db.delete(assignment)
    audit.record(db, action="inventory.assignment_removed", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=session.id,
                 details={"user_id": str(user_id)})
    db.commit()


# ---------------- Contagens ----------------
def _round_counts(db: Session, item: InventoryItem) -> list[InventoryCount]:
    return list(db.scalars(select(InventoryCount).where(
        InventoryCount.item_id == item.id, InventoryCount.round == item.current_round
    ).order_by(InventoryCount.counted_at, InventoryCount.id)))


def _clear_decision(item: InventoryItem) -> None:
    item.accepted_count_id = None
    item.accepted_quantity = None
    item.resolved_by_id = None
    item.resolved_at = None
    item.resolution_note = None


def _evaluate(db: Session, item: InventoryItem) -> None:
    """Recalcula o estado a partir das contagens da rodada. Nunca soma nem faz média."""
    was_resolved = item.status == ItemStatus.RESOLVED
    counts = _round_counts(db, item)
    reasons = {r for r in (item.review_reasons or []) if r in _STICKY_REASONS}
    reasons.update(c.unit_issue for c in counts if c.unit_issue)
    if len({c.quantity for c in counts}) > 1:
        reasons.add(COUNT_CONFLICT)
    counters = [c.counted_by_id for c in counts]
    if len(set(counters)) < len(counters):
        reasons.add(DUPLICATE_COUNT)
    if was_resolved:
        reasons.add(COUNT_AFTER_RESOLUTION)
    _clear_decision(item)
    item.review_reasons = sorted(reasons)
    if reasons:
        item.status = ItemStatus.NEEDS_REVIEW
    else:
        item.status = ItemStatus.COUNTED
        item.accepted_count_id = counts[-1].id
        item.accepted_quantity = counts[-1].quantity


def _unit_issue(data: CountIn, item: InventoryItem, product: ProductReference,
                unit: UnitReference) -> str | None:
    if unit.id != item.unit_id or product.unit_id != item.unit_id:
        return UNIT_MISMATCH
    if not unit.allows_fraction and data.quantity != data.quantity.to_integral_value():
        return FRACTION_NOT_ALLOWED
    return None


def _record_count_once(db: Session, user: User, session_id: UUID,
                       data: CountIn) -> tuple[InventoryCount, UUID, bool]:
    session = get_session(db, user, session_id, lock="share")
    existing = db.scalar(select(InventoryCount).where(
        InventoryCount.session_id == session.id, InventoryCount.counted_by_id == user.id,
        InventoryCount.client_key == data.client_key))
    if existing is not None:
        # Reenvio do aparelho: devolve o registro original, mesmo que a sessão já tenha encerrado.
        item = db.get(InventoryItem, existing.item_id)
        if (item.product_id != data.product_id or existing.quantity != data.quantity
                or existing.unit_id != data.unit_id):
            raise ConflictError("Esta chave já foi usada em outra contagem.")
        return existing, item.product_id, False

    _require_active(session)
    ensure_can_count(db, user, session)
    product = db.scalar(select(ProductReference).where(
        ProductReference.id == data.product_id,
        ProductReference.organization_id == user.organization_id))
    if product is None or not product.is_active:
        raise InvalidError("Produto inválido ou inativo.")
    unit = db.scalar(select(UnitReference).where(
        UnitReference.id == data.unit_id, UnitReference.organization_id == user.organization_id))
    if unit is None:
        raise InvalidError("Unidade inválida.")

    item = db.scalar(select(InventoryItem).where(
        InventoryItem.session_id == session.id, InventoryItem.product_id == product.id
    ).with_for_update().execution_options(populate_existing=True))
    if session.status == SessionStatus.IN_REVIEW and (
            item is None or item.status != ItemStatus.RECOUNT_REQUESTED):
        raise ConflictError("Sessão em revisão: só aceita contagens de reconferência solicitada.")
    if item is None:
        item = InventoryItem(
            organization_id=session.organization_id, session_id=session.id, product_id=product.id,
            unit_id=product.unit_id, status=ItemStatus.COUNTED, current_round=1, review_reasons=[])
        db.add(item)
        db.flush()
    elif item.status == ItemStatus.RECOUNT_REQUESTED and user.role == Role.OPERATOR:
        assigned = db.scalar(select(InventoryRecount.assigned_user_id).where(
            InventoryRecount.item_id == item.id, InventoryRecount.round == item.current_round))
        if assigned is not None and assigned != user.id:
            raise ForbiddenError("Esta reconferência foi atribuída a outra pessoa.")

    count = InventoryCount(
        organization_id=session.organization_id, session_id=session.id, item_id=item.id,
        round=item.current_round, quantity=data.quantity, unit_id=unit.id,
        unit_issue=_unit_issue(data, item, product, unit), counted_by_id=user.id,
        client_key=data.client_key, note=data.note)
    db.add(count)
    db.flush()
    _evaluate(db, item)
    audit.record(db, action="inventory.count_recorded", organization_id=user.organization_id,
                 actor_user_id=user.id, entity_type="inventory_count", entity_id=count.id,
                 details={"session_id": str(session.id), "product_id": str(product.id),
                          "round": count.round, "quantity": str(count.quantity),
                          "unit_id": str(unit.id), "unit_issue": count.unit_issue})
    db.commit()
    return count, product.id, True


def record_count(db: Session, user: User, session_id: UUID,
                 data: CountIn) -> tuple[InventoryCount, UUID, bool]:
    """Devolve (contagem, id do produto, criada?). Reenvio com a mesma chave não cria outra."""
    try:
        return _record_count_once(db, user, session_id, data)
    except IntegrityError:
        # Corrida: outra requisição criou o item ou usou a chave. A segunda tentativa enxerga o registro.
        db.rollback()
        return _record_count_once(db, user, session_id, data)


def list_my_counts(db: Session, user: User, session_id: UUID) -> list[tuple[InventoryCount, UUID]]:
    """Retomada: o que ESTE usuário já contou. Nunca inclui contagens de outras pessoas."""
    session = get_session(db, user, session_id)
    rows = db.execute(select(InventoryCount, InventoryItem.product_id).join(
        InventoryItem, InventoryItem.id == InventoryCount.item_id
    ).where(InventoryCount.session_id == session.id, InventoryCount.counted_by_id == user.id
            ).order_by(InventoryCount.counted_at, InventoryCount.id))
    return [(c, pid) for c, pid in rows]


def list_pending_recounts(db: Session, user: User, session_id: UUID) -> list[dict]:
    session = get_session(db, user, session_id)
    ensure_can_count(db, user, session)
    stmt = select(InventoryItem, ProductReference.name, InventoryRecount).join(
        ProductReference, ProductReference.id == InventoryItem.product_id
    ).join(InventoryRecount, (InventoryRecount.item_id == InventoryItem.id)
           & (InventoryRecount.round == InventoryItem.current_round)
    ).where(InventoryItem.session_id == session.id,
            InventoryItem.status == ItemStatus.RECOUNT_REQUESTED)
    if user.role == Role.OPERATOR:
        stmt = stmt.where(or_(InventoryRecount.assigned_user_id.is_(None),
                              InventoryRecount.assigned_user_id == user.id))
    return [
        {"item_id": item.id, "product_id": item.product_id, "product_name": name,
         "round": recount.round, "reason": recount.reason,
         "assigned_user_id": recount.assigned_user_id}
        for item, name, recount in db.execute(stmt.order_by(ProductReference.name))
    ]


# ---------------- Revisão ----------------
def list_items(db: Session, user: User, session_id: UUID, status: ItemStatus | None,
               limit: int, offset: int) -> list[tuple[InventoryItem, str]]:
    session = get_session(db, user, session_id)
    stmt = select(InventoryItem, ProductReference.name).join(
        ProductReference, ProductReference.id == InventoryItem.product_id
    ).where(InventoryItem.session_id == session.id)
    if status is not None:
        stmt = stmt.where(InventoryItem.status == status)
    stmt = stmt.order_by(ProductReference.name, InventoryItem.id).limit(limit).offset(offset)
    return [(item, name) for item, name in db.execute(stmt)]


def get_item_detail(db: Session, user: User, session_id: UUID, item_id: UUID) -> dict:
    session = get_session(db, user, session_id)
    item = get_item(db, session, item_id)
    return {
        "item": item,
        "product_name": db.get(ProductReference, item.product_id).name,
        "counts": list(db.scalars(select(InventoryCount).where(InventoryCount.item_id == item.id)
                                  .order_by(InventoryCount.counted_at, InventoryCount.id))),
        "recounts": list(db.scalars(select(InventoryRecount).where(InventoryRecount.item_id == item.id)
                                    .order_by(InventoryRecount.round))),
        "erp_checks": list(db.scalars(select(InventoryErpCheck).where(InventoryErpCheck.item_id == item.id)
                                      .order_by(InventoryErpCheck.read_at, InventoryErpCheck.id))),
    }


def request_recount(db: Session, actor: User, session_id: UUID, data: RecountIn) -> InventoryRecount:
    session = get_session(db, actor, session_id, lock="share")
    _require_active(session)
    item = get_item(db, session, data.item_id, lock=True)
    if item.status == ItemStatus.RECOUNT_REQUESTED:
        raise ConflictError("Já existe reconferência pendente para este item.")
    if data.assigned_user_id is not None:
        _validate_assignee(db, session, data.assigned_user_id)
    item.current_round += 1
    item.status = ItemStatus.RECOUNT_REQUESTED
    item.review_reasons = []
    _clear_decision(item)
    recount = InventoryRecount(
        organization_id=session.organization_id, session_id=session.id, item_id=item.id,
        round=item.current_round, reason=data.reason.strip(), requested_by_id=actor.id,
        assigned_user_id=data.assigned_user_id)
    db.add(recount)
    db.flush()
    audit.record(db, action="inventory.recount_requested", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_item", entity_id=item.id,
                 details={"round": recount.round, "reason": recount.reason})
    db.commit()
    return recount


def resolve_item(db: Session, actor: User, session_id: UUID, item_id: UUID, data: ResolveIn) -> InventoryItem:
    session = get_session(db, actor, session_id, lock="share")
    _require_active(session)
    item = get_item(db, session, item_id, lock=True)
    if item.status != ItemStatus.NEEDS_REVIEW:
        raise ConflictError("Este item não está aguardando revisão.")
    count = db.scalar(select(InventoryCount).where(
        InventoryCount.id == data.count_id, InventoryCount.item_id == item.id))
    if count is None or count.round != item.current_round:
        raise InvalidError("Escolha uma contagem da rodada atual deste item.")
    if count.unit_issue is not None:
        raise ConflictError("Contagem com unidade incompatível não pode ser aceita: peça reconferência.")
    item.status = ItemStatus.RESOLVED
    item.accepted_count_id = count.id
    item.accepted_quantity = count.quantity
    item.resolved_by_id = actor.id
    item.resolved_at = utcnow()
    item.resolution_note = data.justification.strip()
    audit.record(db, action="inventory.item_resolved", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_item", entity_id=item.id,
                 details={"count_id": str(count.id), "quantity": str(count.quantity),
                          "previous_reasons": item.review_reasons,
                          "justification": item.resolution_note})
    item.review_reasons = []
    db.commit()
    return item


def check_item_against_erp(db: Session, actor: User, session_id: UUID, item_id: UUID,
                           erp: ErpAdapter) -> ErpCheckResult:
    """Lê o saldo no ERP e compara. Se o ERP falhar, o erro sobe e nada muda aqui (nunca vira zero)."""
    session = get_session(db, actor, session_id)
    _require_active(session)
    item = get_item(db, session, item_id)
    product = db.get(ProductReference, item.product_id)
    location = db.get(StockLocation, session.location_id)
    if not location.external_code:
        raise InvalidError("Local sem código do ERP mapeado.")
    # Encerra a transação: a leitura pode demorar por causa do limite do ERP e não deve prender banco.
    db.commit()
    balance = erp.get_stock_balance(product.external_id, location.external_code)

    session = get_session(db, actor, session_id, lock="share")
    _require_active(session)
    item = get_item(db, session, item_id, lock=True)
    quantity = balance.quantity.quantize(_QTY, rounding=ROUND_HALF_UP)
    changed_at = balance.last_changed_at
    stored_changed_at = (changed_at.astimezone(UTC).replace(tzinfo=None)
                         if changed_at is not None and changed_at.tzinfo else changed_at)
    previous = db.scalar(select(InventoryErpCheck).where(
        InventoryErpCheck.item_id == item.id, InventoryErpCheck.round == item.current_round
    ).order_by(InventoryErpCheck.read_at.desc(), InventoryErpCheck.id).limit(1))
    changed = previous is not None and (
        previous.erp_quantity != quantity or previous.erp_last_changed_at != stored_changed_at)
    difference = None if item.accepted_quantity is None else item.accepted_quantity - quantity

    counts = _round_counts(db, item)
    if counts and item.status != ItemStatus.RECOUNT_REQUESTED:
        new_reasons: set[str] = set()
        if item.status == ItemStatus.RESOLVED:
            # A pessoa já decidiu vendo o saldo; só reabre se o ERP mudou desde a última leitura.
            if changed:
                new_reasons.add(ERP_MOVEMENT)
        else:
            first_count_at = counts[0].counted_at
            if changed or (changed_at is not None and _maybe_after(changed_at, first_count_at)):
                new_reasons.add(ERP_MOVEMENT)
            elif changed_at is None:
                new_reasons.add(ERP_UNVERIFIABLE)
            if difference is not None and difference != 0:
                new_reasons.add(ERP_DIFFERENCE)
        if new_reasons:
            _clear_decision(item)
            item.review_reasons = sorted(set(item.review_reasons or []) | new_reasons)
            item.status = ItemStatus.NEEDS_REVIEW

    check = InventoryErpCheck(
        organization_id=session.organization_id, session_id=session.id, item_id=item.id,
        round=item.current_round, erp_quantity=quantity, erp_reserved_quantity=balance.reserved_quantity,
        read_at=balance.as_of, erp_last_changed_at=stored_changed_at, checked_by_id=actor.id)
    db.add(check)
    db.flush()
    audit.record(db, action="inventory.erp_checked", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_item", entity_id=item.id,
                 details={"erp_quantity": str(quantity), "item_status": item.status.value,
                          "review_reasons": item.review_reasons})
    db.commit()
    return ErpCheckResult(check=check, item=item, difference=difference)
