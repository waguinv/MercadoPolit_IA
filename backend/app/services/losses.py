"""Regras de perdas e avarias (Etapa 5.2 da Central de Movimentações).

Invariantes (cada uma coberta por teste):
- Perda é saída classificada, não ajuste de inventário: sempre tem motivo.
- Toda perda nasce aguardando aprovação (decisão do dono, 2026-11-08); só gerente/admin decide.
- Com a segregação da organização ligada, quem registrou não aprova.
- Motivo "outros" exige observação.
- Correção só por quem registrou, e volta para aprovação. Rejeitado e cancelado são finais.
- Criação idempotente por client_request_id; o mesmo id com outros dados = 409.
- Nenhuma escrita no ERP: erp_status fica "not_sent". Aprovar NÃO movimenta o estoque do ERP.
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.consumption import ConsumptionStatus as Status
from app.models.loss import LossReason, StockLoss
from app.models.organization import Organization
from app.models.user import Role, User
from app.schemas.loss import LossCreate, LossData
from app.services import audit
from app.services import locations as loc_svc
from app.services.consumption import (
    MANAGERS,
    Q3,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RuleError,
    _product_and_unit,
)

__all__ = ["ConflictError", "ForbiddenError", "NotFoundError", "RuleError"]


def _scoped(user: User):
    stmt = select(StockLoss).where(StockLoss.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(StockLoss.store_id == user.store_id)
    if user.role == Role.OPERATOR:
        stmt = stmt.where(StockLoss.registered_by == user.id)
    return stmt


def get_loss(db: Session, user: User, loss_id: UUID, *, lock: bool = False) -> StockLoss:
    stmt = _scoped(user).where(StockLoss.id == loss_id)
    if lock:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Perda não encontrada.")
    return item


def list_losses(db: Session, user: User, status: str | None) -> list[StockLoss]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(StockLoss.status == status)
    return list(db.scalars(stmt.order_by(StockLoss.created_at.desc()).limit(200)))


def _check_reason(data: LossData) -> None:
    if data.reason == LossReason.OTHER.value and not data.note:
        raise RuleError("No motivo Outros, descreva o que aconteceu na observação.")


def _find_retry(db: Session, actor: User, data: LossCreate) -> StockLoss | None:
    existing = db.scalar(select(StockLoss).where(
        StockLoss.organization_id == actor.organization_id, StockLoss.registered_by == actor.id,
        StockLoss.client_request_id == data.client_request_id))
    if existing is not None and (existing.product_id != data.product_id
                                 or existing.quantity != Decimal(data.quantity).quantize(Q3)
                                 or existing.location_id != data.location_id
                                 or existing.reason != data.reason):
        raise ConflictError("client_request_id já usado com dados diferentes.")
    return existing


def create(db: Session, actor: User, data: LossCreate) -> tuple[StockLoss, bool]:
    existing = _find_retry(db, actor, data)
    if existing is not None:
        return existing, False
    _check_reason(data)
    try:
        location = loc_svc.get_accessible_location(db, actor, data.location_id)
    except loc_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if not location.is_active:
        raise ConflictError("O local está inativo.")
    product, qty = _product_and_unit(db, actor.organization_id, data.product_id, data.quantity)
    item = StockLoss(
        organization_id=actor.organization_id, store_id=location.store_id, location_id=location.id,
        product_id=product.id, unit_id=product.unit_id, quantity=qty, reason=data.reason, lot=data.lot,
        expiry_date=data.expiry_date, note=data.note, registered_by=actor.id,
        client_request_id=data.client_request_id)
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find_retry(db, actor, data)
        if existing is not None:
            return existing, False
        raise
    audit.record(db, action="loss.created", organization_id=actor.organization_id, actor_user_id=actor.id,
                 entity_type="stock_loss", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty), "reason": data.reason})
    db.commit()
    return item, True


def _decide(db: Session, actor: User, loss_id: UUID, new_status: Status, note: str | None) -> StockLoss:
    item = get_loss(db, actor, loss_id, lock=True)
    if item.status != Status.PENDING_APPROVAL.value:
        raise ConflictError("Só perda aguardando aprovação pode ser decidida.")
    org = db.get(Organization, actor.organization_id)
    if new_status == Status.APPROVED and org.consumption_segregation and actor.id == item.registered_by:
        raise ForbiddenError("Quem registrou não pode aprovar a própria perda.")
    item.status = new_status.value
    item.decided_by = actor.id
    item.decided_at = utcnow()
    item.decision_note = note
    audit.record(db, action=f"loss.{new_status.value}", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="stock_loss", entity_id=item.id,
                 details={"note": note} if note else None)
    db.commit()
    return item


def approve(db: Session, actor: User, loss_id: UUID) -> StockLoss:
    return _decide(db, actor, loss_id, Status.APPROVED, None)


def reject(db: Session, actor: User, loss_id: UUID, reason: str) -> StockLoss:
    return _decide(db, actor, loss_id, Status.REJECTED, reason)


def request_correction(db: Session, actor: User, loss_id: UUID, reason: str) -> StockLoss:
    return _decide(db, actor, loss_id, Status.NEEDS_CORRECTION, reason)


def correct(db: Session, actor: User, loss_id: UUID, data: LossData) -> StockLoss:
    item = get_loss(db, actor, loss_id, lock=True)
    if item.registered_by != actor.id:
        raise ForbiddenError("Só quem registrou pode corrigir.")
    if item.status != Status.NEEDS_CORRECTION.value:
        raise ConflictError("Só perda com correção pedida pode ser alterada.")
    _check_reason(data)
    product, qty = _product_and_unit(db, actor.organization_id, data.product_id, data.quantity)
    item.product_id, item.unit_id, item.quantity = product.id, product.unit_id, qty
    item.reason, item.lot, item.expiry_date, item.note = data.reason, data.lot, data.expiry_date, data.note
    item.status = Status.PENDING_APPROVAL.value
    audit.record(db, action="loss.corrected", organization_id=actor.organization_id, actor_user_id=actor.id,
                 entity_type="stock_loss", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty), "reason": data.reason})
    db.commit()
    return item


def cancel(db: Session, actor: User, loss_id: UUID, reason: str) -> StockLoss:
    item = get_loss(db, actor, loss_id, lock=True)
    if item.registered_by != actor.id and actor.role not in MANAGERS:
        raise ForbiddenError("Só quem registrou ou um gerente pode cancelar.")
    if item.status in (Status.REJECTED.value, Status.CANCELLED.value):
        raise ConflictError("Esta perda já está encerrada.")
    if item.erp_status != "not_sent":
        raise ConflictError("Perda já enviada ao ERP não pode ser cancelada aqui.")
    item.status = Status.CANCELLED.value
    item.decision_note = reason
    audit.record(db, action="loss.cancelled", organization_id=actor.organization_id, actor_user_id=actor.id,
                 entity_type="stock_loss", entity_id=item.id, details={"reason": reason})
    db.commit()
    return item
