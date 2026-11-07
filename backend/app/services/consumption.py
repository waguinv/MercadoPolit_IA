"""Regras do consumo interno (Etapa 5.1 da Central de Movimentações).

Invariantes (cada uma coberta por teste):
- Todo consumo nasce aguardando aprovação; só gerente/admin aprova, rejeita ou pede correção.
- Com a segregação ligada (padrão), quem registrou ou quem consumiu não aprova.
- Departamento em modo "centralizado": só gerente/admin registra. Em "autoatendimento", o operador registra
  apenas o próprio consumo.
- Consumidor, registrador e aprovador ficam gravados separadamente.
- Rejeitado e cancelado são finais; correção só por quem registrou, e volta para aprovação.
- Criação idempotente por client_request_id; o mesmo id com outros dados = 409.
- Nenhuma escrita no ERP: erp_status fica "not_sent". Aprovar NÃO movimenta o estoque do ERP.
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.catalog import ProductReference, UnitReference
from app.models.consumption import (
    ConsumptionDepartment,
    ConsumptionPurpose,
    ConsumptionStatus,
    DepartmentMode,
    InternalConsumption,
)
from app.models.organization import Organization
from app.models.user import Role, User
from app.schemas.consumption import (
    ConsumptionCorrection,
    ConsumptionCreate,
    DepartmentIn,
    DepartmentUpdate,
    PurposeIn,
    PurposeUpdate,
)
from app.services import audit
from app.services import locations as loc_svc

Q3 = Decimal("0.001")
MANAGERS = (Role.ADMIN, Role.MANAGER)

# Lista sugerida pela especificação (docs/MERCADOPILOT_AI_ARQUITETURA.md, 7.4).
DEFAULT_DEPARTMENTS = ("Refeitório", "Escritório/administrativo", "Operacional", "Frutaria", "Padaria",
                       "Açougue", "Limpeza")
DEFAULT_PURPOSES = ("Almoço de funcionários", "Café/lanche", "Uso administrativo", "Uso na operação",
                    "Limpeza/higienização")


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    """409"""


class RuleError(Exception):
    """422"""


class ForbiddenError(Exception):
    """403"""


# ----------------------------------------------------------------- configuração
def config(db: Session, user: User) -> tuple[bool, list[ConsumptionDepartment], list[ConsumptionPurpose]]:
    org = db.get(Organization, user.organization_id)
    deps = list(db.scalars(select(ConsumptionDepartment).where(
        ConsumptionDepartment.organization_id == user.organization_id).order_by(ConsumptionDepartment.name)))
    purs = list(db.scalars(select(ConsumptionPurpose).where(
        ConsumptionPurpose.organization_id == user.organization_id).order_by(ConsumptionPurpose.name)))
    return org.consumption_segregation, deps, purs


def set_segregation(db: Session, actor: User, value: bool) -> None:
    org = db.get(Organization, actor.organization_id)
    if org.consumption_segregation != value:
        org.consumption_segregation = value
        audit.record(db, action="consumption.segregation_changed", organization_id=org.id,
                     actor_user_id=actor.id, details={"segregation": value})
    db.commit()


def _name_taken(db: Session, model, org_id: UUID, name: str, exclude: UUID | None = None) -> bool:
    stmt = select(model.id).where(model.organization_id == org_id, model.name == name)
    if exclude is not None:
        stmt = stmt.where(model.id != exclude)
    return db.scalar(stmt) is not None


def create_department(db: Session, actor: User, data: DepartmentIn) -> ConsumptionDepartment:
    if _name_taken(db, ConsumptionDepartment, actor.organization_id, data.name):
        raise ConflictError("Já existe um departamento com esse nome.")
    dep = ConsumptionDepartment(organization_id=actor.organization_id, name=data.name, mode=data.mode)
    db.add(dep)
    db.flush()
    audit.record(db, action="consumption.department_created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="consumption_department", entity_id=dep.id,
                 details={"name": dep.name, "mode": dep.mode})
    db.commit()
    return dep


def update_department(
    db: Session, actor: User, dep_id: UUID, data: DepartmentUpdate
) -> ConsumptionDepartment:
    dep = db.scalar(select(ConsumptionDepartment).where(
        ConsumptionDepartment.id == dep_id, ConsumptionDepartment.organization_id == actor.organization_id))
    if dep is None:
        raise NotFoundError("Departamento não encontrado.")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if _name_taken(db, ConsumptionDepartment, actor.organization_id, changes["name"], dep.id):
            raise ConflictError("Já existe um departamento com esse nome.")
    for key, value in changes.items():
        setattr(dep, key, value)
    audit.record(db, action="consumption.department_updated", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="consumption_department", entity_id=dep.id,
                 details={k: v for k, v in changes.items()})
    db.commit()
    return dep


def create_purpose(db: Session, actor: User, data: PurposeIn) -> ConsumptionPurpose:
    if _name_taken(db, ConsumptionPurpose, actor.organization_id, data.name):
        raise ConflictError("Já existe uma finalidade com esse nome.")
    pur = ConsumptionPurpose(organization_id=actor.organization_id, name=data.name)
    db.add(pur)
    db.flush()
    audit.record(db, action="consumption.purpose_created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="consumption_purpose", entity_id=pur.id,
                 details={"name": pur.name})
    db.commit()
    return pur


def update_purpose(db: Session, actor: User, pur_id: UUID, data: PurposeUpdate) -> ConsumptionPurpose:
    pur = db.scalar(select(ConsumptionPurpose).where(
        ConsumptionPurpose.id == pur_id, ConsumptionPurpose.organization_id == actor.organization_id))
    if pur is None:
        raise NotFoundError("Finalidade não encontrada.")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if _name_taken(db, ConsumptionPurpose, actor.organization_id, changes["name"], pur.id):
            raise ConflictError("Já existe uma finalidade com esse nome.")
    for key, value in changes.items():
        setattr(pur, key, value)
    audit.record(db, action="consumption.purpose_updated", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="consumption_purpose", entity_id=pur.id,
                 details=dict(changes))
    db.commit()
    return pur


def add_defaults(db: Session, actor: User) -> None:
    """Acrescenta a lista sugerida, sem duplicar nem reativar o que o admin desativou."""
    org_id = actor.organization_id
    added = []
    for name in DEFAULT_DEPARTMENTS:
        if not _name_taken(db, ConsumptionDepartment, org_id, name):
            db.add(ConsumptionDepartment(organization_id=org_id, name=name))
            added.append(name)
    for name in DEFAULT_PURPOSES:
        if not _name_taken(db, ConsumptionPurpose, org_id, name):
            db.add(ConsumptionPurpose(organization_id=org_id, name=name))
            added.append(name)
    audit.record(db, action="consumption.defaults_added", organization_id=org_id, actor_user_id=actor.id,
                 details={"added": added})
    db.commit()


def consumers(db: Session, user: User) -> list[User]:
    """Pessoas que podem constar como consumidoras: usuários ativos visíveis para quem registra."""
    stmt = select(User).where(User.organization_id == user.organization_id, User.is_active.is_(True))
    if user.store_id is not None:
        stmt = stmt.where(or_(User.store_id == user.store_id, User.store_id.is_(None)))
    return list(db.scalars(stmt.order_by(User.full_name)))


# ----------------------------------------------------------------- acesso
def _scoped(user: User):
    stmt = select(InternalConsumption).where(InternalConsumption.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(InternalConsumption.store_id == user.store_id)
    if user.role == Role.OPERATOR:
        # Operador vê só o que registrou ou o que foi lançado no nome dele.
        stmt = stmt.where(or_(InternalConsumption.registered_by == user.id,
                              InternalConsumption.consumer_user_id == user.id))
    return stmt


def get_consumption(
    db: Session, user: User, consumption_id: UUID, *, lock: bool = False
) -> InternalConsumption:
    stmt = _scoped(user).where(InternalConsumption.id == consumption_id)
    if lock:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Consumo não encontrado.")
    return item


def list_consumptions(db: Session, user: User, status: str | None) -> list[InternalConsumption]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(InternalConsumption.status == status)
    return list(db.scalars(stmt.order_by(InternalConsumption.created_at.desc()).limit(200)))


# ----------------------------------------------------------------- registro
def _active(db: Session, model, org_id: UUID, obj_id: UUID, label: str):
    obj = db.scalar(select(model).where(model.id == obj_id, model.organization_id == org_id))
    if obj is None or not obj.is_active:
        raise NotFoundError(f"{label} não encontrado ou inativo.")
    return obj


def _product_and_unit(db: Session, org_id: UUID, product_id: UUID, quantity: Decimal):
    product = _active(db, ProductReference, org_id, product_id, "Produto")
    unit = db.get(UnitReference, product.unit_id)
    qty = Decimal(quantity).quantize(Q3)
    if unit is not None and not unit.allows_fraction and qty != qty.to_integral_value():
        raise RuleError(f"A unidade {unit.code} não aceita quantidade fracionada.")
    return product, qty


def _find_retry(db: Session, actor: User, data: ConsumptionCreate) -> InternalConsumption | None:
    existing = db.scalar(select(InternalConsumption).where(
        InternalConsumption.organization_id == actor.organization_id,
        InternalConsumption.registered_by == actor.id,
        InternalConsumption.client_request_id == data.client_request_id))
    if existing is not None and (existing.product_id != data.product_id
                                 or existing.quantity != Decimal(data.quantity).quantize(Q3)
                                 or existing.location_id != data.location_id):
        raise ConflictError("client_request_id já usado com dados diferentes.")
    return existing


def create(db: Session, actor: User, data: ConsumptionCreate) -> tuple[InternalConsumption, bool]:
    existing = _find_retry(db, actor, data)
    if existing is not None:
        return existing, False
    org_id = actor.organization_id
    dep = _active(db, ConsumptionDepartment, org_id, data.department_id, "Departamento")
    purpose = _active(db, ConsumptionPurpose, org_id, data.purpose_id, "Finalidade")
    try:
        location = loc_svc.get_accessible_location(db, actor, data.location_id)
    except loc_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if not location.is_active:
        raise ConflictError("O local está inativo.")
    product, qty = _product_and_unit(db, org_id, data.product_id, data.quantity)

    manager = actor.role in MANAGERS
    if dep.mode == DepartmentMode.CENTRALIZED.value and not manager:
        raise ForbiddenError("Neste departamento o consumo é registrado só pelo responsável.")
    if manager:
        if data.consumer_user_id is not None:
            consumer = next((u for u in consumers(db, actor) if u.id == data.consumer_user_id), None)
            if consumer is None:
                raise NotFoundError("Consumidor não encontrado.")
            consumer_id, consumer_name = consumer.id, consumer.full_name
        elif data.consumer_name:
            consumer_id, consumer_name = None, data.consumer_name
        else:
            raise RuleError("Informe quem consumiu.")
    else:
        # Autoatendimento: o funcionário registra apenas o próprio consumo.
        if data.consumer_user_id not in (None, actor.id) or data.consumer_name:
            raise ForbiddenError("No autoatendimento, registre apenas o seu próprio consumo.")
        consumer_id, consumer_name = actor.id, actor.full_name

    item = InternalConsumption(
        organization_id=org_id, store_id=location.store_id, location_id=location.id, department_id=dep.id,
        purpose_id=purpose.id, product_id=product.id, unit_id=product.unit_id, quantity=qty,
        consumer_user_id=consumer_id, consumer_name=consumer_name, registered_by=actor.id, note=data.note,
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
    audit.record(db, action="consumption.created", organization_id=org_id, actor_user_id=actor.id,
                 entity_type="internal_consumption", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty), "department": dep.name,
                          "consumer_user_id": str(consumer_id) if consumer_id else None})
    db.commit()
    return item, True


# ----------------------------------------------------------------- decisões
def _decide(db: Session, actor: User, consumption_id: UUID, new_status: ConsumptionStatus,
            note: str | None) -> InternalConsumption:
    item = get_consumption(db, actor, consumption_id, lock=True)
    if item.status != ConsumptionStatus.PENDING_APPROVAL.value:
        raise ConflictError("Só consumo aguardando aprovação pode ser decidido.")
    org = db.get(Organization, actor.organization_id)
    if (new_status == ConsumptionStatus.APPROVED and org.consumption_segregation
            and actor.id in (item.registered_by, item.consumer_user_id)):
        raise ForbiddenError("Quem registrou ou consumiu não pode aprovar o próprio consumo.")
    item.status = new_status.value
    item.decided_by = actor.id
    item.decided_at = utcnow()
    item.decision_note = note
    audit.record(db, action=f"consumption.{new_status.value}", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="internal_consumption", entity_id=item.id,
                 details={"note": note} if note else None)
    db.commit()
    return item


def approve(db: Session, actor: User, consumption_id: UUID) -> InternalConsumption:
    return _decide(db, actor, consumption_id, ConsumptionStatus.APPROVED, None)


def reject(db: Session, actor: User, consumption_id: UUID, reason: str) -> InternalConsumption:
    return _decide(db, actor, consumption_id, ConsumptionStatus.REJECTED, reason)


def request_correction(db: Session, actor: User, consumption_id: UUID, reason: str) -> InternalConsumption:
    return _decide(db, actor, consumption_id, ConsumptionStatus.NEEDS_CORRECTION, reason)


def correct(
    db: Session, actor: User, consumption_id: UUID, data: ConsumptionCorrection
) -> InternalConsumption:
    item = get_consumption(db, actor, consumption_id, lock=True)
    if item.registered_by != actor.id:
        raise ForbiddenError("Só quem registrou pode corrigir.")
    if item.status != ConsumptionStatus.NEEDS_CORRECTION.value:
        raise ConflictError("Só consumo com correção pedida pode ser alterado.")
    org_id = actor.organization_id
    dep = _active(db, ConsumptionDepartment, org_id, data.department_id, "Departamento")
    if dep.mode == DepartmentMode.CENTRALIZED.value and actor.role not in MANAGERS:
        raise ForbiddenError("Neste departamento o consumo é registrado só pelo responsável.")
    purpose = _active(db, ConsumptionPurpose, org_id, data.purpose_id, "Finalidade")
    product, qty = _product_and_unit(db, org_id, data.product_id, data.quantity)
    item.department_id, item.purpose_id = dep.id, purpose.id
    item.product_id, item.unit_id, item.quantity, item.note = product.id, product.unit_id, qty, data.note
    item.status = ConsumptionStatus.PENDING_APPROVAL.value
    audit.record(db, action="consumption.corrected", organization_id=org_id, actor_user_id=actor.id,
                 entity_type="internal_consumption", entity_id=item.id,
                 details={"product_id": str(product.id), "quantity": str(qty)})
    db.commit()
    return item


def cancel(db: Session, actor: User, consumption_id: UUID, reason: str) -> InternalConsumption:
    item = get_consumption(db, actor, consumption_id, lock=True)
    if item.registered_by != actor.id and actor.role not in MANAGERS:
        raise ForbiddenError("Só quem registrou ou um gerente pode cancelar.")
    if item.status in (ConsumptionStatus.REJECTED.value, ConsumptionStatus.CANCELLED.value):
        raise ConflictError("Este consumo já está encerrado.")
    if item.erp_status != "not_sent":
        # Depois de integrado, só movimento compensatório autorizado (etapa futura).
        raise ConflictError("Consumo já enviado ao ERP não pode ser cancelado aqui.")
    item.status = ConsumptionStatus.CANCELLED.value
    item.decision_note = reason
    audit.record(db, action="consumption.cancelled", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="internal_consumption", entity_id=item.id,
                 details={"reason": reason})
    db.commit()
    return item
