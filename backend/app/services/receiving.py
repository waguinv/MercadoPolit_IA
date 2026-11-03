"""Regras do recebimento manual.

Invariantes (cada uma coberta por teste):
- Aceito + rejeitado == recebido; rejeição exige motivo; produto vencido não pode ser aceito.
- Falta/excesso são CALCULADOS (esperado x recebido), nunca digitados.
- Só rascunho é editável; finalizar/cancelar exigem gerente; finalizado não é cancelado (exigiria movimento
  compensatório, que depende do ERP) e nunca é excluído.
- NF-e (chave de acesso) e fornecedor+série+número não podem ser recebidos duas vezes (índices parciais).
- Criação e itens são idempotentes por client_request_id; reuso com dados diferentes = 409.
- Nenhuma escrita no ERP: erp_application_status fica "not_applied".
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.catalog import ProductReference, UnitReference
from app.models.receiving import Receiving, ReceivingItem, ReceivingStatus
from app.models.user import User
from app.schemas.receiving import FinalizeIn, ItemCreate, ItemUpdate, ReceivingCreate
from app.services import audit
from app.services import locations as loc_svc

Q3 = Decimal("0.001")


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    """409"""


class RuleError(Exception):
    """422"""


def q(v: Decimal | None) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(Q3)


# ----------------------------------------------------------------- acesso
def get_receiving(db: Session, user: User, receiving_id: UUID, *, lock: bool = False) -> Receiving:
    stmt = select(Receiving).where(Receiving.id == receiving_id,
                                   Receiving.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(Receiving.store_id == user.store_id)
    if lock:
        stmt = stmt.with_for_update()
    rec = db.scalar(stmt)
    if rec is None:
        raise NotFoundError("Recebimento não encontrado.")
    return rec


def list_receivings(db: Session, user: User, store_id: UUID | None, status: str | None):
    stmt = select(Receiving).where(Receiving.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(Receiving.store_id == user.store_id)
    if store_id is not None:
        stmt = stmt.where(Receiving.store_id == store_id)
    if status:
        stmt = stmt.where(Receiving.status == status)
    return list(db.scalars(stmt.order_by(Receiving.created_at.desc()).limit(200)))


def items_of(db: Session, rec: Receiving) -> list[ReceivingItem]:
    return list(db.scalars(select(ReceivingItem).where(ReceivingItem.receiving_id == rec.id)
                           .order_by(ReceivingItem.created_at, ReceivingItem.id)))


def with_diffs(item: ReceivingItem) -> dict:
    exp, got = item.expected_quantity, item.received_quantity
    if exp is None:
        return {"shortage_quantity": None, "excess_quantity": None}
    return {"shortage_quantity": q(max(exp - got, Decimal(0))),
            "excess_quantity": q(max(got - exp, Decimal(0)))}


# ----------------------------------------------------------------- criação
def _find_retry(db: Session, actor: User, data: ReceivingCreate) -> Receiving | None:
    """Reenvio do mesmo pedido: devolve o registro original; a mesma chave com outros dados é erro."""
    existing = db.scalar(select(Receiving).where(
        Receiving.organization_id == actor.organization_id, Receiving.created_by == actor.id,
        Receiving.client_request_id == data.client_request_id))
    if existing is not None:
        same = (existing.store_id == data.store_id and existing.location_id == data.location_id
                and existing.supplier_name == data.supplier_name.strip()
                and existing.access_key == data.access_key
                and existing.document_number == data.document_number)
        if not same:
            raise ConflictError("client_request_id já usado com dados diferentes.")
    return existing


def create(db: Session, actor: User, data: ReceivingCreate) -> tuple[Receiving, bool]:
    existing = _find_retry(db, actor, data)
    if existing is not None:
        return existing, False
    store = loc_svc.get_accessible_store(db, actor, data.store_id)
    try:
        location = loc_svc.get_accessible_location(db, actor, data.location_id)
    except loc_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if location.store_id != store.id:
        raise NotFoundError("Local não encontrado.")
    if not location.is_active:
        raise ConflictError("O local está inativo.")
    rec = Receiving(
        organization_id=actor.organization_id, store_id=store.id, location_id=location.id,
        supplier_name=data.supplier_name.strip(), supplier_document=data.supplier_document,
        document_number=data.document_number, document_series=data.document_series.strip(),
        access_key=data.access_key, received_at=data.received_at or utcnow(), note=data.note,
        client_request_id=data.client_request_id, created_by=actor.id)
    db.add(rec)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Corrida: dois envios simultâneos do mesmo pedido não são uma nota duplicada.
        existing = _find_retry(db, actor, data)
        if existing is not None:
            return existing, False
        raise ConflictError("Esta nota (chave de acesso ou fornecedor/série/número) já foi "
                            "registrada em outro recebimento não cancelado.") from exc
    audit.record(db, action="receiving.created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving", entity_id=rec.id,
                 details={"store_id": str(store.id), "has_access_key": rec.access_key is not None})
    db.commit()
    return rec, True


# ----------------------------------------------------------------- itens
def _check_quantities(unit: UnitReference | None, received: Decimal, accepted: Decimal, rejected: Decimal,
                      reason: str | None, expiry, received_on, expected: Decimal | None) -> None:
    if accepted + rejected != received:
        raise RuleError("Aceito + rejeitado deve ser igual ao recebido.")
    if rejected > 0 and reason is None:
        raise RuleError("Informe o motivo da rejeição.")
    if rejected == 0 and reason is not None:
        raise RuleError("Motivo de rejeição informado sem quantidade rejeitada.")
    if expiry is not None and expiry < received_on and accepted > 0:
        raise RuleError("Produto vencido não pode ser aceito: rejeite a quantidade (motivo 'expired').")
    if unit is not None and not unit.allows_fraction:
        for name, v in (("recebida", received), ("aceita", accepted), ("rejeitada", rejected),
                        ("esperada", expected)):
            if v is not None and v != v.to_integral_value():
                raise RuleError(f"A unidade {unit.code} não aceita quantidade {name} fracionada.")


def add_item(db: Session, actor: User, receiving_id: UUID, data: ItemCreate) -> tuple[ReceivingItem, bool]:
    rec = get_receiving(db, actor, receiving_id, lock=True)
    existing = db.scalar(select(ReceivingItem).where(
        ReceivingItem.receiving_id == rec.id, ReceivingItem.client_request_id == data.client_request_id))
    if existing is not None:
        if existing.product_id != data.product_id or existing.received_quantity != q(data.received_quantity):
            raise ConflictError("client_request_id já usado com dados diferentes.")
        return existing, False
    if rec.status != ReceivingStatus.DRAFT.value:
        raise ConflictError("Só recebimentos em rascunho podem ser alterados.")
    product = db.scalar(select(ProductReference).where(
        ProductReference.id == data.product_id, ProductReference.organization_id == actor.organization_id))
    if product is None or not product.is_active:
        raise NotFoundError("Produto não encontrado ou inativo.")
    received, rejected = q(data.received_quantity), q(data.rejected_quantity)
    accepted = q(data.accepted_quantity) if data.accepted_quantity is not None else received - rejected
    if accepted < 0:
        raise RuleError("Rejeitado maior que o recebido.")
    unit = db.get(UnitReference, product.unit_id)
    lot = data.lot.strip()
    _check_quantities(unit, received, accepted, rejected, data.rejection_reason, data.expiry_date,
                      rec.received_at.date(), q(data.expected_quantity))
    dup = db.scalar(select(ReceivingItem.id).where(
        ReceivingItem.receiving_id == rec.id, ReceivingItem.product_id == product.id,
        ReceivingItem.lot == lot,
        ReceivingItem.expiry_date.is_(None) if data.expiry_date is None
        else ReceivingItem.expiry_date == data.expiry_date))
    if dup is not None:
        raise ConflictError("Este produto (mesmo lote e validade) já está no recebimento: edite o item.")
    item = ReceivingItem(
        organization_id=actor.organization_id, receiving_id=rec.id, product_id=product.id,
        unit_id=product.unit_id, expected_quantity=q(data.expected_quantity), received_quantity=received,
        accepted_quantity=accepted, rejected_quantity=rejected, rejection_reason=data.rejection_reason,
        lot=lot, expiry_date=data.expiry_date, note=data.note,
        client_request_id=data.client_request_id, created_by=actor.id)
    db.add(item)
    db.flush()
    audit.record(db, action="receiving.item_added", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving_item", entity_id=item.id,
                 details={"receiving_id": str(rec.id), "product_id": str(product.id),
                          "received": str(received), "accepted": str(accepted), "rejected": str(rejected)})
    db.commit()
    return item, True


def update_item(
    db: Session, actor: User, receiving_id: UUID, item_id: UUID, data: ItemUpdate
) -> ReceivingItem:
    rec = get_receiving(db, actor, receiving_id, lock=True)
    if rec.status != ReceivingStatus.DRAFT.value:
        raise ConflictError("Só recebimentos em rascunho podem ser alterados.")
    item = db.scalar(select(ReceivingItem).where(ReceivingItem.id == item_id,
                                                 ReceivingItem.receiving_id == rec.id))
    if item is None:
        raise NotFoundError("Item não encontrado.")
    fields = data.model_dump(exclude_unset=True)
    received = q(fields["received_quantity"]) if fields.get("received_quantity") is not None \
        else item.received_quantity
    rejected = q(fields["rejected_quantity"]) if fields.get("rejected_quantity") is not None \
        else item.rejected_quantity
    if fields.get("accepted_quantity") is not None:
        accepted = q(fields["accepted_quantity"])
    elif "received_quantity" in fields or "rejected_quantity" in fields:
        accepted = received - rejected  # reajusta o aceito junto
        if accepted < 0:
            raise RuleError("Rejeitado maior que o recebido.")
    else:
        accepted = item.accepted_quantity
    reason = fields["rejection_reason"] if "rejection_reason" in fields else item.rejection_reason
    if rejected == 0 and "rejection_reason" not in fields:
        reason = None
    expected = q(fields["expected_quantity"]) if "expected_quantity" in fields else item.expected_quantity
    unit = db.get(UnitReference, item.unit_id)
    _check_quantities(unit, received, accepted, rejected, reason, item.expiry_date,
                      rec.received_at.date(), expected)
    before = {"received": str(item.received_quantity), "accepted": str(item.accepted_quantity),
              "rejected": str(item.rejected_quantity)}
    item.expected_quantity, item.received_quantity = expected, received
    item.accepted_quantity, item.rejected_quantity, item.rejection_reason = accepted, rejected, reason
    if "note" in fields:
        item.note = fields["note"]
    audit.record(db, action="receiving.item_updated", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving_item", entity_id=item.id,
                 details={"receiving_id": str(rec.id), "before": before,
                          "after": {"received": str(received), "accepted": str(accepted),
                                    "rejected": str(rejected)}})
    db.commit()
    return item


def delete_item(db: Session, actor: User, receiving_id: UUID, item_id: UUID) -> None:
    rec = get_receiving(db, actor, receiving_id, lock=True)
    if rec.status != ReceivingStatus.DRAFT.value:
        raise ConflictError("Só recebimentos em rascunho podem ser alterados.")
    item = db.scalar(select(ReceivingItem).where(ReceivingItem.id == item_id,
                                                 ReceivingItem.receiving_id == rec.id))
    if item is None:
        raise NotFoundError("Item não encontrado.")
    audit.record(db, action="receiving.item_removed", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving_item", entity_id=item.id,
                 details={"receiving_id": str(rec.id), "product_id": str(item.product_id),
                          "received": str(item.received_quantity)})
    db.delete(item)
    db.commit()


# ----------------------------------------------------------------- fechamento
def finalize(db: Session, actor: User, receiving_id: UUID, data: FinalizeIn) -> Receiving:
    rec = get_receiving(db, actor, receiving_id, lock=True)
    if rec.status != ReceivingStatus.DRAFT.value:
        raise ConflictError("Só recebimentos em rascunho podem ser finalizados.")
    items = items_of(db, rec)
    if not items:
        raise RuleError("O recebimento não tem itens.")
    if all(i.received_quantity == 0 for i in items):
        raise RuleError("Nenhuma quantidade foi recebida: cancele o recebimento em vez de finalizar.")
    shortages = sum(1 for i in items if (with_diffs(i)["shortage_quantity"] or 0) > 0)
    excesses = sum(1 for i in items if (with_diffs(i)["excess_quantity"] or 0) > 0)
    rec.status, rec.is_partial = ReceivingStatus.FINALIZED.value, data.partial
    rec.finalized_by, rec.finalized_at = actor.id, utcnow()
    rec.erp_application_status = "not_applied"
    audit.record(db, action="receiving.finalized", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving", entity_id=rec.id,
                 details={"partial": data.partial, "items": len(items), "items_with_shortage": shortages,
                          "items_with_excess": excesses, "erp_application": "not_applied"})
    db.commit()
    return rec


def cancel(db: Session, actor: User, receiving_id: UUID, reason: str) -> Receiving:
    rec = get_receiving(db, actor, receiving_id, lock=True)
    if rec.status == ReceivingStatus.FINALIZED.value:
        raise ConflictError("Recebimento finalizado não pode ser cancelado (exigiria movimento "
                            "compensatório autorizado, ainda não suportado).")
    if rec.status != ReceivingStatus.DRAFT.value:
        raise ConflictError("O recebimento já está cancelado.")
    rec.status, rec.cancelled_at = ReceivingStatus.CANCELLED.value, utcnow()
    rec.cancelled_by, rec.cancel_reason = actor.id, reason.strip()
    audit.record(db, action="receiving.cancelled", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="receiving", entity_id=rec.id,
                 details={"reason": reason.strip()})
    db.commit()
    return rec
