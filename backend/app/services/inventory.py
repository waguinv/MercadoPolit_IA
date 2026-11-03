"""Regras do inventário colaborativo.

Invariantes (cada uma coberta por teste):
- Nenhuma escrita no ERP. Só leituras do saldo (baseline e conferência final).
- Contagens são append-only; NUNCA são somadas. Contadores divergentes => divergência para decisão humana.
- Ausência de saldo no ERP != zero.
- Saldo do ERP que mudou depois da abertura invalida a contagem anterior => só recontagem ou manter ERP.
- Tudo respeita organização/loja no servidor; operador não vê o saldo do ERP (contagem às cegas).
- Finalizar só com zero divergências abertas; gera PROPOSTAS de ajuste, não ajustes.
"""
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.integrations.erp_port import ErpAdapter
from app.models.catalog import ProductReference, StockLocation, UnitReference
from app.models.inventory import (
    DiscrepancyKind,
    DiscrepancyStatus,
    InventoryAdjustmentProposal,
    InventoryBaseline,
    InventoryCount,
    InventoryDiscrepancy,
    InventoryRecountRequest,
    InventorySession,
    SessionStatus,
)
from app.models.user import Role, User
from app.schemas.inventory import CountCreate, ResolveIn, SessionCreate
from app.services import audit
from app.services import locations as loc_svc

Q3 = Decimal("0.001")


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    """409: a operação não é permitida no estado atual."""


class RuleError(Exception):
    """422: dado inválido para as regras de negócio."""


def q(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Q3)


# ------------------------------------------------------------------ acesso
def get_session_for(db: Session, user: User, session_id: UUID, *, lock: bool = False) -> InventorySession:
    stmt = select(InventorySession).where(
        InventorySession.id == session_id,
        InventorySession.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(InventorySession.store_id == user.store_id)
    if lock:
        stmt = stmt.with_for_update()  # PostgreSQL serializa contagem x fechamento; SQLite ignora
    inv = db.scalar(stmt)
    if inv is None:
        raise NotFoundError("Sessão de inventário não encontrada.")
    return inv


def list_sessions(db: Session, user: User, store_id: UUID | None, status: str | None):
    stmt = select(InventorySession).where(InventorySession.organization_id == user.organization_id)
    if user.store_id is not None:
        stmt = stmt.where(InventorySession.store_id == user.store_id)
    if store_id is not None:
        stmt = stmt.where(InventorySession.store_id == store_id)
    if status:
        stmt = stmt.where(InventorySession.status == status)
    return list(db.scalars(stmt.order_by(InventorySession.created_at.desc()).limit(200)))


def _products_by_external(db: Session, org_id: UUID) -> dict[str, ProductReference]:
    return {p.external_id: p for p in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == org_id))}


def _read_location(adapter: ErpAdapter, location: StockLocation):
    if not location.external_code:
        raise RuleError("O local não tem código do ERP mapeado (external_code).")
    return adapter.list_location_balances(location.external_code)


def _latest_baseline(db: Session, session_id: UUID, upto_round: int) -> dict[UUID, InventoryBaseline]:
    rows = db.scalars(select(InventoryBaseline).where(
        InventoryBaseline.session_id == session_id, InventoryBaseline.round_no <= upto_round
    ).order_by(InventoryBaseline.round_no))
    return {r.product_id: r for r in rows}  # a rodada mais nova sobrescreve


# ------------------------------------------------------------------ sessão
def create_session(db: Session, actor: User, adapter: ErpAdapter, data: SessionCreate) -> InventorySession:
    store = loc_svc.get_accessible_store(db, actor, data.store_id)
    try:
        location = loc_svc.get_accessible_location(db, actor, data.location_id)
    except loc_svc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    if location.store_id != store.id:
        raise NotFoundError("Local não encontrado.")
    if not location.is_active:
        raise ConflictError("O local está inativo.")
    # Leitura do ERP ANTES de gravar qualquer coisa: se falhar, nada é criado.
    balances = _read_location(adapter, location)
    mirror = _products_by_external(db, actor.organization_id)

    inv = InventorySession(organization_id=actor.organization_id, store_id=store.id,
                           location_id=location.id, name=data.name.strip(), created_by=actor.id)
    db.add(inv)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Já existe inventário ativo para este local.") from exc
    unmapped = 0
    for b in balances:
        prod = mirror.get(b.product_external_id)
        if prod is None:
            unmapped += 1  # produto do ERP ainda não sincronizado: reportado, não inventado
            continue
        db.add(InventoryBaseline(
            organization_id=actor.organization_id, session_id=inv.id, round_no=1, product_id=prod.id,
            quantity=q(b.quantity), erp_last_changed_at=b.last_changed_at, read_at=b.as_of))
    inv.baseline_unmapped_count = unmapped
    audit.record(db, action="inventory.session_created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=inv.id,
                 details={"store_id": str(store.id), "location_id": str(location.id),
                          "baseline_items": len(balances) - unmapped, "baseline_unmapped": unmapped})
    db.commit()
    return inv


def progress(db: Session, inv: InventorySession) -> dict:
    count = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w)) or 0  # noqa: E731
    return {
        "baseline_items": count(InventoryBaseline, InventoryBaseline.session_id == inv.id,
                                InventoryBaseline.round_no == 1),
        "counts_total": count(InventoryCount, InventoryCount.session_id == inv.id),
        "products_counted": db.scalar(select(func.count(func.distinct(InventoryCount.product_id)))
                                      .where(InventoryCount.session_id == inv.id)) or 0,
        "discrepancies_open": count(InventoryDiscrepancy, InventoryDiscrepancy.session_id == inv.id,
                                    InventoryDiscrepancy.status == DiscrepancyStatus.OPEN.value),
    }


# ------------------------------------------------------------------ contagem
def add_count(db: Session, actor: User, session_id: UUID, data: CountCreate) -> tuple[InventoryCount, bool]:
    """Salva na hora. Idempotente por (sessão, usuário, client_request_id) -> (contagem, criada)."""
    inv = get_session_for(db, actor, session_id, lock=True)
    existing = db.scalar(select(InventoryCount).where(
        InventoryCount.session_id == inv.id, InventoryCount.counted_by == actor.id,
        InventoryCount.client_request_id == data.client_request_id))
    if existing is not None:
        if existing.product_id != data.product_id or q(existing.quantity) != q(data.quantity):
            raise ConflictError("client_request_id já usado com dados diferentes.")
        return existing, False  # repetição segura (ex.: celular reenviou após queda de rede)
    if inv.status != SessionStatus.OPEN.value:
        raise ConflictError("A contagem desta sessão não está aberta.")
    product = db.scalar(select(ProductReference).where(
        ProductReference.id == data.product_id,
        ProductReference.organization_id == actor.organization_id))
    if product is None or not product.is_active:
        raise NotFoundError("Produto não encontrado ou inativo.")
    if inv.current_round > 1:
        allowed = db.scalar(select(InventoryRecountRequest.id).where(
            InventoryRecountRequest.session_id == inv.id,
            InventoryRecountRequest.round_no == inv.current_round,
            InventoryRecountRequest.product_id == product.id))
        if allowed is None:
            raise RuleError("Nesta rodada só podem ser recontados os produtos solicitados.")
    unit = db.get(UnitReference, product.unit_id)
    qty = q(data.quantity)
    if unit is not None and not unit.allows_fraction and qty != qty.to_integral_value():
        raise RuleError(f"A unidade {unit.code} não aceita quantidade fracionada.")
    count = InventoryCount(
        organization_id=actor.organization_id, session_id=inv.id, round_no=inv.current_round,
        product_id=product.id, unit_id=product.unit_id, quantity=qty, counted_by=actor.id,
        client_request_id=data.client_request_id, note=data.note)
    db.add(count)
    db.commit()
    return count, True


def list_counts(db: Session, user: User, inv: InventorySession, product_id: UUID | None,
                round_no: int | None) -> list[InventoryCount]:
    stmt = select(InventoryCount).where(InventoryCount.session_id == inv.id)
    if user.role == Role.OPERATOR:
        stmt = stmt.where(InventoryCount.counted_by == user.id)  # operador vê só as próprias
    if product_id:
        stmt = stmt.where(InventoryCount.product_id == product_id)
    if round_no:
        stmt = stmt.where(InventoryCount.round_no == round_no)
    return list(db.scalars(stmt.order_by(InventoryCount.counted_at).limit(1000)))


def _effective(counts: list[InventoryCount]) -> dict[UUID, dict[UUID, Decimal]]:
    """produto -> {contador -> última quantidade dele}. Nunca soma entre contadores."""
    latest: dict[tuple[UUID, UUID], InventoryCount] = {}
    for c in sorted(counts, key=lambda c: c.counted_at):
        latest[(c.product_id, c.counted_by)] = c
    out: dict[UUID, dict[UUID, Decimal]] = defaultdict(dict)
    for (prod, user), c in latest.items():
        out[prod][user] = q(c.quantity)
    return out


# ------------------------------------------------------------------ fechamento
def close_counting(db: Session, actor: User, adapter: ErpAdapter, session_id: UUID):
    inv = get_session_for(db, actor, session_id, lock=True)
    if inv.status != SessionStatus.OPEN.value:
        raise ConflictError("A contagem desta sessão não está aberta.")
    counts = list(db.scalars(select(InventoryCount).where(
        InventoryCount.session_id == inv.id, InventoryCount.round_no == inv.current_round)))
    if not counts:
        raise RuleError("Nenhuma contagem registrada nesta rodada.")
    location = db.get(StockLocation, inv.location_id)
    erp_rows = _read_location(adapter, location)  # falha => nada muda (ainda não gravamos)
    mirror = _products_by_external(db, actor.organization_id)
    current: dict[UUID, Decimal] = {}
    for r in erp_rows:
        prod = mirror.get(r.product_external_id)
        if prod is not None:
            current[prod.id] = q(r.quantity)
    baselines = _latest_baseline(db, inv.id, inv.current_round)
    effective = _effective(counts)

    clean = created = 0
    for product_id, per_user in effective.items():
        base_row = baselines.get(product_id)
        base = base_row.quantity if base_row else None
        cur = current.get(product_id)
        values = sorted(set(per_user.values()))
        erp_changed = (base is None) != (cur is None) or (base is not None and cur != base)
        details: dict = {"erp_changed": erp_changed}
        counted: Decimal | None = values[0] if len(values) == 1 else None
        if len(values) > 1:
            kind = DiscrepancyKind.CONFLICTING_COUNTS
            details["counted_values"] = [str(v) for v in values]
        elif base is None and cur is None:
            kind = DiscrepancyKind.NO_ERP_BALANCE
        elif erp_changed:
            kind = DiscrepancyKind.ERP_CHANGED
        elif counted != base:
            kind = DiscrepancyKind.DIFFERS_FROM_ERP
        else:
            kind = None
        # a decisão anterior (se houver) deixa de valer: este fechamento é a verdade da rodada
        db.execute(update(InventoryDiscrepancy).where(
            InventoryDiscrepancy.session_id == inv.id, InventoryDiscrepancy.product_id == product_id,
            InventoryDiscrepancy.status == DiscrepancyStatus.OPEN.value
        ).values(status=DiscrepancyStatus.SUPERSEDED.value))
        if kind is None:
            clean += 1
            continue
        created += 1
        db.add(InventoryDiscrepancy(
            organization_id=actor.organization_id, session_id=inv.id, product_id=product_id,
            round_no=inv.current_round, kind=kind.value, baseline_quantity=base, counted_quantity=counted,
            erp_current_quantity=cur, details=details))
    inv.status = SessionStatus.REVIEW.value
    inv.counting_closed_at = utcnow()
    not_counted = len([p for p, b in baselines.items() if p not in effective])
    audit.record(db, action="inventory.counting_closed", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=inv.id,
                 details={"round": inv.current_round, "products_checked": len(effective),
                          "clean": clean, "discrepancies": created})
    db.commit()
    return {"products_checked": len(effective), "clean": clean, "discrepancies_created": created,
            "baseline_items_not_counted": not_counted}


def list_discrepancies(db: Session, inv: InventorySession, status: str | None):
    stmt = select(InventoryDiscrepancy).where(InventoryDiscrepancy.session_id == inv.id)
    if status:
        stmt = stmt.where(InventoryDiscrepancy.status == status)
    return list(db.scalars(stmt.order_by(InventoryDiscrepancy.created_at, InventoryDiscrepancy.id)))


# ------------------------------------------------------------------ resolução
def resolve(db: Session, actor: User, session_id: UUID, discrepancy_id: UUID, data: ResolveIn):
    inv = get_session_for(db, actor, session_id, lock=True)
    if inv.status != SessionStatus.REVIEW.value:
        raise ConflictError("A sessão não está em revisão.")
    d = db.scalar(select(InventoryDiscrepancy).where(
        InventoryDiscrepancy.id == discrepancy_id, InventoryDiscrepancy.session_id == inv.id))
    if d is None:
        raise NotFoundError("Divergência não encontrada.")
    if d.status != DiscrepancyStatus.OPEN.value:
        raise ConflictError("A divergência não está aberta.")
    accepted: Decimal | None = None
    if data.decision == "accept_count":
        if (d.details or {}).get("erp_changed") and d.kind != DiscrepancyKind.NO_ERP_BALANCE.value:
            raise ConflictError("O saldo do ERP mudou depois da abertura: a contagem pode estar "
                                "desatualizada. Solicite recontagem ou mantenha o saldo do ERP.")
        if d.kind == DiscrepancyKind.CONFLICTING_COUNTS.value:
            options = {Decimal(v) for v in (d.details or {}).get("counted_values", [])}
            if data.accepted_quantity is None or q(data.accepted_quantity) not in options:
                raise RuleError("Informe accepted_quantity igual a UM dos valores contados (sem somar).")
            accepted = q(data.accepted_quantity)
        else:
            if data.accepted_quantity is not None and q(data.accepted_quantity) != d.counted_quantity:
                raise RuleError("accepted_quantity só é aceito para contagens conflitantes.")
            accepted = d.counted_quantity
    elif data.accepted_quantity is not None:
        raise RuleError("keep_erp não aceita accepted_quantity.")
    d.status = DiscrepancyStatus.RESOLVED.value
    d.decision, d.accepted_quantity = data.decision, accepted
    d.resolved_by, d.resolved_at, d.resolution_note = actor.id, utcnow(), data.note.strip()
    audit.record(db, action="inventory.discrepancy_resolved", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_discrepancy", entity_id=d.id,
                 details={"session_id": str(inv.id), "decision": data.decision, "kind": d.kind,
                          "accepted_quantity": None if accepted is None else str(accepted)})
    db.commit()
    return d


def request_recount(db: Session, actor: User, adapter: ErpAdapter, session_id: UUID,
                    discrepancy_ids: list[UUID], reason: str) -> InventorySession:
    inv = get_session_for(db, actor, session_id, lock=True)
    if inv.status != SessionStatus.REVIEW.value:
        raise ConflictError("A sessão não está em revisão.")
    ids = set(discrepancy_ids)
    ds = list(db.scalars(select(InventoryDiscrepancy).where(
        InventoryDiscrepancy.session_id == inv.id, InventoryDiscrepancy.id.in_(ids))))
    if len(ds) != len(ids):
        raise NotFoundError("Divergência não encontrada.")
    if any(d.status != DiscrepancyStatus.OPEN.value for d in ds):
        raise ConflictError("Só divergências abertas podem ser recontadas.")
    location = db.get(StockLocation, inv.location_id)
    erp_rows = _read_location(adapter, location)  # leitura fresca: novo baseline da recontagem
    mirror = _products_by_external(db, actor.organization_id)
    current = {mirror[r.product_external_id].id: r for r in erp_rows if r.product_external_id in mirror}
    new_round = inv.current_round + 1
    for d in ds:
        row = current.get(d.product_id)
        db.add(InventoryRecountRequest(
            organization_id=actor.organization_id, session_id=inv.id, round_no=new_round,
            product_id=d.product_id, requested_by=actor.id, reason=reason.strip()))
        db.add(InventoryBaseline(
            organization_id=actor.organization_id, session_id=inv.id, round_no=new_round,
            product_id=d.product_id, quantity=q(row.quantity) if row else None,
            erp_last_changed_at=row.last_changed_at if row else None,
            read_at=row.as_of if row else utcnow()))
        d.status = DiscrepancyStatus.SUPERSEDED.value
        d.decision = "recount_requested"
    inv.current_round, inv.status = new_round, SessionStatus.OPEN.value
    audit.record(db, action="inventory.recount_requested", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=inv.id,
                 details={"round": new_round, "products": len(ds), "reason": reason.strip()})
    db.commit()
    return inv


# ------------------------------------------------------------------ encerramento
def finalize(db: Session, actor: User, session_id: UUID) -> InventorySession:
    inv = get_session_for(db, actor, session_id, lock=True)
    if inv.status != SessionStatus.REVIEW.value:
        raise ConflictError("A sessão não está em revisão.")
    open_count = db.scalar(select(func.count()).select_from(InventoryDiscrepancy).where(
        InventoryDiscrepancy.session_id == inv.id,
        InventoryDiscrepancy.status == DiscrepancyStatus.OPEN.value)) or 0
    if open_count:
        raise ConflictError(
            f"Há {open_count} divergência(s) aberta(s). Resolva ou peça recontagem antes de finalizar.")
    baselines = _latest_baseline(db, inv.id, inv.current_round)
    accepted = db.scalars(select(InventoryDiscrepancy).where(
        InventoryDiscrepancy.session_id == inv.id,
        InventoryDiscrepancy.status == DiscrepancyStatus.RESOLVED.value,
        InventoryDiscrepancy.decision == "accept_count"))
    proposals = 0
    for d in accepted:
        product = db.get(ProductReference, d.product_id)
        base_row = baselines.get(d.product_id)
        base = base_row.quantity if base_row else None
        db.add(InventoryAdjustmentProposal(
            organization_id=actor.organization_id, session_id=inv.id, product_id=d.product_id,
            unit_id=product.unit_id, baseline_quantity=base, counted_quantity=d.accepted_quantity,
            delta=None if base is None else q(d.accepted_quantity - base)))
        proposals += 1
    inv.status, inv.finalized_at, inv.finalized_by = SessionStatus.FINALIZED.value, utcnow(), actor.id
    inv.erp_application_status = "not_applied"  # NADA foi (nem será, nesta etapa) enviado ao ERP
    audit.record(db, action="inventory.session_finalized", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=inv.id,
                 details={"proposals": proposals, "erp_application": "not_applied"})
    db.commit()
    return inv


def cancel(db: Session, actor: User, session_id: UUID, reason: str) -> InventorySession:
    inv = get_session_for(db, actor, session_id, lock=True)
    if inv.status not in (SessionStatus.OPEN.value, SessionStatus.REVIEW.value):
        raise ConflictError("Só sessões abertas ou em revisão podem ser canceladas.")
    inv.status, inv.cancelled_at = SessionStatus.CANCELLED.value, utcnow()
    inv.cancelled_by, inv.cancel_reason = actor.id, reason.strip()
    audit.record(db, action="inventory.session_cancelled", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="inventory_session", entity_id=inv.id,
                 details={"reason": reason.strip()})
    db.commit()
    return inv


def list_proposals(db: Session, inv: InventorySession):
    return list(db.scalars(select(InventoryAdjustmentProposal).where(
        InventoryAdjustmentProposal.session_id == inv.id).order_by(InventoryAdjustmentProposal.id)))
