"""Regras de devoluções e trocas com fornecedor (Etapa 5.3 da Central de Movimentações).

Invariantes (cada uma coberta por teste):
- Toda devolução nasce aguardando aprovação; só gerente/admin decide; com segregação, quem registrou
  não aprova.
- Saída devolvida (coleta) e reposição são eventos distintos; coleta só depois de aprovada.
- Reposição só em troca já coletada e só apontando um recebimento FINALIZADO, de outra nota, da mesma loja e
  do mesmo fornecedor, com o produto aceito.
- Nem a devolução nem a reposição podem somar mais que a nota comporta: evita duplicar o mesmo movimento.
- Depois da coleta não há cancelamento: a mercadoria já saiu.
- Criação idempotente por client_request_id. Nenhum documento fiscal é gerado.
- Nenhuma escrita no ERP: erp_status fica "not_sent".
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.organization import Organization
from app.models.receiving import Receiving, ReceivingItem, ReceivingStatus
from app.models.supplier_return import (
    ReturnModality,
    ReturnReason,
    ReturnStatus,
    SupplierReturn,
    SupplierReturnReplacement,
)
from app.models.user import Role, User
from app.schemas.supplier_return import ReplacementIn, ReturnCreate, ReturnData
from app.services import audit
from app.services import locations as loc_svc
from app.services import receiving as receiving_svc
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

_INACTIVE = (ReturnStatus.REJECTED.value, ReturnStatus.CANCELLED.value)


def _scoped(user: User):
    stmt = select(SupplierReturn).where(SupplierReturn.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(SupplierReturn.store_id == user.store_id)
    if user.role == Role.OPERATOR:
        stmt = stmt.where(SupplierReturn.registered_by == user.id)
    return stmt


def get_return(db: Session, user: User, return_id: UUID, *, lock: bool = False) -> SupplierReturn:
    stmt = _scoped(user).where(SupplierReturn.id == return_id)
    if lock:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Devolução não encontrada.")
    return item


def list_returns(db: Session, user: User, status: str | None) -> list[SupplierReturn]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(SupplierReturn.status == status)
    return list(db.scalars(stmt.order_by(SupplierReturn.created_at.desc()).limit(200)))


def replacements_of(db: Session, return_ids: list[UUID]) -> list[SupplierReturnReplacement]:
    if not return_ids:
        return []
    return list(db.scalars(select(SupplierReturnReplacement).where(
        SupplierReturnReplacement.return_id.in_(return_ids)).order_by(SupplierReturnReplacement.created_at)))


def _locked_receiving(db: Session, actor: User, receiving_id: UUID, store_id: UUID) -> Receiving:
    try:
        rec = receiving_svc.get_receiving(db, actor, receiving_id, lock=True)
    except receiving_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if rec.store_id != store_id:
        raise NotFoundError("Recebimento não encontrado.")
    if rec.status != ReceivingStatus.FINALIZED.value:
        raise ConflictError("Só recebimento finalizado pode ser usado.")
    return rec


def _product_total(db: Session, receiving_id: UUID, product_id: UUID, column) -> Decimal:
    total = db.scalar(select(func.coalesce(func.sum(column), 0)).where(
        ReceivingItem.receiving_id == receiving_id, ReceivingItem.product_id == product_id))
    return Decimal(total).quantize(Q3)


def _check_origin(db: Session, actor: User, data: ReturnData, store_id: UUID, qty: Decimal,
                  own_id: UUID | None) -> Receiving | None:
    """Confere a nota de origem e impede devolver mais do que ela trouxe."""
    if data.receiving_id is None:
        return None
    rec = _locked_receiving(db, actor, data.receiving_id, store_id)
    received = _product_total(db, rec.id, data.product_id, ReceivingItem.received_quantity)
    if received <= 0:
        raise RuleError("Este produto não consta no recebimento de origem.")
    stmt = select(func.coalesce(func.sum(SupplierReturn.quantity), 0)).where(
        SupplierReturn.receiving_id == rec.id, SupplierReturn.product_id == data.product_id,
        SupplierReturn.status.not_in(_INACTIVE))
    if own_id is not None:
        stmt = stmt.where(SupplierReturn.id != own_id)
    used = Decimal(db.scalar(stmt)).quantize(Q3)
    if used + qty > received:
        raise RuleError(f"A devolução passa do recebido nesta nota. Disponível: {received - used}.")
    return rec


def _check_rules(data: ReturnData) -> None:
    if data.reason == ReturnReason.OTHER.value and not data.note:
        raise RuleError("No motivo Outros, descreva o que aconteceu na observação.")
    if data.receiving_id is None and not data.supplier_name:
        raise RuleError("Informe o fornecedor ou escolha o recebimento de origem.")
    if data.expected_replacement_date is not None and data.modality != ReturnModality.EXCHANGE.value:
        raise RuleError("Previsão de reposição só existe na modalidade troca.")


def _apply(item: SupplierReturn, data: ReturnData, product, qty: Decimal, origin: Receiving | None) -> None:
    item.product_id, item.unit_id, item.quantity = product.id, product.unit_id, qty
    item.reason, item.modality = data.reason, data.modality
    item.supplier_name = origin.supplier_name if origin else data.supplier_name
    item.supplier_document = origin.supplier_document if origin else None
    item.receiving_id = origin.id if origin else None
    item.origin_document = origin.document_number if origin else data.origin_document
    item.lot, item.expiry_date, item.note = data.lot, data.expiry_date, data.note
    item.expected_pickup_date = data.expected_pickup_date
    item.expected_replacement_date = data.expected_replacement_date


def _find_retry(db: Session, actor: User, data: ReturnCreate) -> SupplierReturn | None:
    existing = db.scalar(select(SupplierReturn).where(
        SupplierReturn.organization_id == actor.organization_id, SupplierReturn.registered_by == actor.id,
        SupplierReturn.client_request_id == data.client_request_id))
    if existing is not None and (existing.product_id != data.product_id
                                 or existing.quantity != Decimal(data.quantity).quantize(Q3)
                                 or existing.location_id != data.location_id
                                 or existing.reason != data.reason or existing.modality != data.modality):
        raise ConflictError("client_request_id já usado com dados diferentes.")
    return existing


def create(db: Session, actor: User, data: ReturnCreate) -> tuple[SupplierReturn, bool]:
    existing = _find_retry(db, actor, data)
    if existing is not None:
        return existing, False
    _check_rules(data)
    try:
        location = loc_svc.get_accessible_location(db, actor, data.location_id)
    except loc_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if not location.is_active:
        raise ConflictError("O local está inativo.")
    product, qty = _product_and_unit(db, actor.organization_id, data.product_id, data.quantity)
    origin = _check_origin(db, actor, data, location.store_id, qty, None)
    item = SupplierReturn(
        organization_id=actor.organization_id, store_id=location.store_id, location_id=location.id,
        registered_by=actor.id, client_request_id=data.client_request_id)
    _apply(item, data, product, qty, origin)
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find_retry(db, actor, data)
        if existing is not None:
            return existing, False
        raise
    audit.record(db, action="supplier_return.created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty), "reason": data.reason,
                          "modality": data.modality})
    db.commit()
    return item, True


def _decide(
    db: Session, actor: User, return_id: UUID, new_status: ReturnStatus, note: str | None
) -> SupplierReturn:
    item = get_return(db, actor, return_id, lock=True)
    if item.status != ReturnStatus.PENDING_APPROVAL.value:
        raise ConflictError("Só devolução aguardando aprovação pode ser decidida.")
    org = db.get(Organization, actor.organization_id)
    if new_status == ReturnStatus.APPROVED and org.consumption_segregation and actor.id == item.registered_by:
        raise ForbiddenError("Quem registrou não pode aprovar a própria devolução.")
    item.status = new_status.value
    item.decided_by = actor.id
    item.decided_at = utcnow()
    item.decision_note = note
    audit.record(db, action=f"supplier_return.{new_status.value}", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"note": note} if note else None)
    db.commit()
    return item


def approve(db: Session, actor: User, return_id: UUID) -> SupplierReturn:
    return _decide(db, actor, return_id, ReturnStatus.APPROVED, None)


def reject(db: Session, actor: User, return_id: UUID, reason: str) -> SupplierReturn:
    return _decide(db, actor, return_id, ReturnStatus.REJECTED, reason)


def request_correction(db: Session, actor: User, return_id: UUID, reason: str) -> SupplierReturn:
    return _decide(db, actor, return_id, ReturnStatus.NEEDS_CORRECTION, reason)


def correct(db: Session, actor: User, return_id: UUID, data: ReturnData) -> SupplierReturn:
    item = get_return(db, actor, return_id, lock=True)
    if item.registered_by != actor.id:
        raise ForbiddenError("Só quem registrou pode corrigir.")
    if item.status != ReturnStatus.NEEDS_CORRECTION.value:
        raise ConflictError("Só devolução com correção pedida pode ser alterada.")
    _check_rules(data)
    product, qty = _product_and_unit(db, actor.organization_id, data.product_id, data.quantity)
    origin = _check_origin(db, actor, data, item.store_id, qty, item.id)
    _apply(item, data, product, qty, origin)
    item.status = ReturnStatus.PENDING_APPROVAL.value
    audit.record(db, action="supplier_return.corrected", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty), "reason": data.reason,
                          "modality": data.modality})
    db.commit()
    return item


def cancel(db: Session, actor: User, return_id: UUID, reason: str) -> SupplierReturn:
    item = get_return(db, actor, return_id, lock=True)
    if item.registered_by != actor.id and actor.role not in MANAGERS:
        raise ForbiddenError("Só quem registrou ou um gerente pode cancelar.")
    if item.status in (ReturnStatus.PICKED_UP.value, ReturnStatus.COMPLETED.value):
        raise ConflictError("A mercadoria já saiu da loja: esta devolução não pode ser cancelada.")
    if item.status in _INACTIVE:
        raise ConflictError("Esta devolução já está encerrada.")
    item.status = ReturnStatus.CANCELLED.value
    item.decision_note = reason
    audit.record(db, action="supplier_return.cancelled", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"reason": reason})
    db.commit()
    return item


def _finish(item: SupplierReturn, actor: User, note: str | None) -> None:
    item.status = ReturnStatus.COMPLETED.value
    item.completed_by = actor.id
    item.completed_at = utcnow()
    item.completion_note = note


def pickup(db: Session, actor: User, return_id: UUID, note: str | None) -> SupplierReturn:
    item = get_return(db, actor, return_id, lock=True)
    if item.status != ReturnStatus.APPROVED.value:
        raise ConflictError("Só devolução aprovada pode ter a coleta registrada.")
    item.status = ReturnStatus.PICKED_UP.value
    item.picked_up_by = actor.id
    item.picked_up_at = utcnow()
    item.pickup_note = note
    # Sem reposição, a saída encerra o processo.
    if item.modality == ReturnModality.NO_REPLACEMENT.value:
        _finish(item, actor, None)
    audit.record(db, action="supplier_return.picked_up", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"quantity": str(item.quantity), "modality": item.modality})
    db.commit()
    return item


def _same_supplier(item: SupplierReturn, rec: Receiving) -> bool:
    if item.supplier_document and rec.supplier_document:
        return item.supplier_document == rec.supplier_document
    return item.supplier_name.strip().casefold() == rec.supplier_name.strip().casefold()


def add_replacement(db: Session, actor: User, return_id: UUID, data: ReplacementIn) -> SupplierReturn:
    item = get_return(db, actor, return_id, lock=True)
    if item.modality != ReturnModality.EXCHANGE.value:
        raise ConflictError("Reposição só existe na modalidade troca.")
    if item.status != ReturnStatus.PICKED_UP.value:
        raise ConflictError("Registre a coleta antes da reposição.")
    rec = _locked_receiving(db, actor, data.receiving_id, item.store_id)
    if rec.id == item.receiving_id:
        raise RuleError("A reposição precisa ser de outro recebimento, não da nota de origem.")
    if not _same_supplier(item, rec):
        raise RuleError("Este recebimento é de outro fornecedor.")
    _, qty = _product_and_unit(db, actor.organization_id, item.product_id, data.quantity)
    accepted = _product_total(db, rec.id, item.product_id, ReceivingItem.accepted_quantity)
    if accepted <= 0:
        raise RuleError("Este produto não foi aceito nesse recebimento.")
    already = db.scalar(select(SupplierReturnReplacement.id).where(
        SupplierReturnReplacement.return_id == item.id, SupplierReturnReplacement.receiving_id == rec.id))
    if already is not None:
        raise ConflictError("Este recebimento já foi usado nesta devolução.")
    used = Decimal(db.scalar(select(func.coalesce(func.sum(SupplierReturnReplacement.quantity), 0)).join(
        SupplierReturn, SupplierReturn.id == SupplierReturnReplacement.return_id).where(
        SupplierReturnReplacement.receiving_id == rec.id,
        SupplierReturn.product_id == item.product_id))).quantize(Q3)
    if used + qty > accepted:
        raise RuleError(f"A reposição passa do aceito nesse recebimento. Disponível: {accepted - used}.")
    done = sum((r.quantity for r in replacements_of(db, [item.id])), Decimal(0))
    if done + qty > item.quantity:
        raise RuleError(f"A reposição passa do devolvido. Falta repor: {item.quantity - done}.")
    replacement = SupplierReturnReplacement(
        organization_id=actor.organization_id, return_id=item.id, receiving_id=rec.id, quantity=qty,
        registered_by=actor.id)
    db.add(replacement)
    if done + qty == item.quantity:
        _finish(item, actor, None)
    db.flush()
    audit.record(db, action="supplier_return.replacement_added", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"receiving_id": str(rec.id), "quantity": str(qty)})
    db.commit()
    return item


def complete(db: Session, actor: User, return_id: UUID, reason: str) -> SupplierReturn:
    """Crédito recebido, ou troca encerrada sem a reposição completa: sempre com justificativa."""
    item = get_return(db, actor, return_id, lock=True)
    if item.status != ReturnStatus.PICKED_UP.value:
        raise ConflictError("Só devolução já coletada e em aberto pode ser encerrada.")
    _finish(item, actor, reason)
    audit.record(db, action="supplier_return.completed", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="supplier_return", entity_id=item.id,
                 details={"reason": reason})
    db.commit()
    return item
