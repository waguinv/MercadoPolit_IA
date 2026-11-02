"""Sincronização do catálogo (unidades e produtos) a partir do ERP.

Garantias:
- Valida todos os dados do ERP ANTES de tocar no banco.
- Tudo-ou-nada: uma falha não deixa sincronização parcial.
- Idempotente: rodar duas vezes seguidas não altera nada na segunda.
- Produto que sumiu do ERP (ou veio inativo) é desativado, nunca apagado.
"""
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.erp_port import ErpAdapter, ErpDataError, ErpError
from app.models.catalog import ProductReference, UnitReference
from app.models.user import User
from app.services import audit


class SyncConflictError(Exception):
    pass


@dataclass
class SyncResult:
    units_created: int = 0
    units_updated: int = 0
    units_deactivated: int = 0
    products_created: int = 0
    products_updated: int = 0
    products_deactivated: int = 0
    products_unchanged: int = 0


def _validate(units, products) -> None:
    unit_codes = [u.code for u in units]
    if any(not c.strip() for c in unit_codes) or len(set(unit_codes)) != len(unit_codes):
        raise ErpDataError("Unidades do ERP vazias ou duplicadas.")
    ext_ids = [p.external_id for p in products]
    if any(not i.strip() for i in ext_ids) or len(set(ext_ids)) != len(ext_ids):
        raise ErpDataError("Produtos do ERP sem id ou com id duplicado.")
    unknown = {p.unit_code for p in products} - set(unit_codes)
    if unknown:
        raise ErpDataError(f"Produtos referenciam unidades inexistentes: {sorted(unknown)}")


def sync_catalog(db: Session, actor: User, adapter: ErpAdapter) -> SyncResult:
    org_id = actor.organization_id
    try:
        erp_units = adapter.list_units()
        erp_products = adapter.list_products()
        _validate(erp_units, erp_products)
    except ErpError as exc:
        audit.record(db, action="catalog.sync_failed", organization_id=org_id,
                     actor_user_id=actor.id, details={"error": type(exc).__name__})
        db.commit()
        raise

    now = datetime.now(UTC)
    res = SyncResult()

    units = {u.code: u for u in db.scalars(
        select(UnitReference).where(UnitReference.organization_id == org_id))}
    seen_units = set()
    for eu in erp_units:
        seen_units.add(eu.code)
        cur = units.get(eu.code)
        if cur is None:
            cur = UnitReference(organization_id=org_id, code=eu.code, name=eu.name,
                                allows_fraction=eu.allows_fraction)
            db.add(cur)
            units[eu.code] = cur
            res.units_created += 1
        elif (cur.name, cur.allows_fraction, cur.is_active) != (eu.name, eu.allows_fraction, True):
            cur.name, cur.allows_fraction, cur.is_active = eu.name, eu.allows_fraction, True
            res.units_updated += 1
    for code, cur in units.items():
        if code not in seen_units and cur.is_active:
            cur.is_active = False
            res.units_deactivated += 1
    db.flush()  # garante ids das unidades novas

    products = {p.external_id: p for p in db.scalars(
        select(ProductReference).where(ProductReference.organization_id == org_id))}
    seen = set()
    for ep in erp_products:
        seen.add(ep.external_id)
        unit_id = units[ep.unit_code].id
        cur = products.get(ep.external_id)
        if cur is None:
            db.add(ProductReference(
                organization_id=org_id, external_id=ep.external_id, sku=ep.sku,
                barcode=ep.barcode, name=ep.name, unit_id=unit_id, is_active=ep.active,
                last_synced_at=now))
            res.products_created += 1
            continue
        new = (ep.sku, ep.barcode, ep.name, unit_id, ep.active)
        if (cur.sku, cur.barcode, cur.name, cur.unit_id, cur.is_active) != new:
            cur.sku, cur.barcode, cur.name, cur.unit_id, cur.is_active = new
            res.products_updated += 1
        else:
            res.products_unchanged += 1
        cur.last_synced_at = now
    for ext_id, cur in products.items():
        if ext_id not in seen and cur.is_active:
            cur.is_active = False
            res.products_deactivated += 1

    audit.record(db, action="catalog.synced", organization_id=org_id, actor_user_id=actor.id,
                 details=res.__dict__.copy())
    try:
        db.commit()
    except IntegrityError as exc:  # sincronizações concorrentes
        db.rollback()
        raise SyncConflictError("Outra sincronização está em andamento. Tente novamente.") from exc
    return res
