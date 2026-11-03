"""Corridas reais entre conexões. Só rodam em PostgreSQL (defina TEST_DATABASE_URL): o SQLite ignora as travas."""
import threading
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEvent,
    InventoryCount,
    InventoryItem,
    InventorySession,
    Organization,
    ProductReference,
    Role,
    SessionStatus,
    StockLocation,
    Store,
    UnitReference,
    User,
)
from app.models.receiving import Receiving, ReceivingItem
from app.schemas.inventory import CountIn, RecountIn, ResolveIn, SessionCreate
from app.schemas.receiving import FinalizeIn, ItemCreate, ReceivingCreate
from app.services import auth as auth_svc
from app.services import inventory as inv
from app.services import login_throttle, refresh_tokens
from app.services import receiving as rec_svc
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="exige PostgreSQL (TEST_DATABASE_URL)")

ROUNDS = 10


def run_all(fns):
    """Solta todas as funções ao mesmo tempo. Exceções viram resultado, para o teste examinar."""
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def wrap(index, fn):
        barrier.wait()
        try:
            results[index] = fn()
        except Exception as exc:  # noqa: BLE001
            results[index] = exc

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert not any(t.is_alive() for t in threads), "corrida travou"
    return results


def failures(results, *expected):
    return [r for r in results if isinstance(r, Exception) and not isinstance(r, expected)]


class Scenario:
    def __init__(self, factory):
        self.factory = factory
        with factory() as db:
            org = Organization(name="Rede")
            db.add(org)
            db.flush()
            store = Store(organization_id=org.id, code="L1", name="Loja")
            unit = UnitReference(organization_id=org.id, code="UN", name="Unidade")
            db.add_all([store, unit])
            db.flush()
            users = [User(organization_id=org.id, email=f"op{i}@a.com", full_name=f"op{i}", password_hash="x",
                          role=Role.OPERATOR) for i in range(8)]
            manager = User(organization_id=org.id, email="ger@a.com", full_name="ger", password_hash="x",
                           role=Role.MANAGER)
            location = StockLocation(organization_id=org.id, store_id=store.id, code="DEP", name="Depósito")
            db.add_all([*users, manager, location])
            db.commit()
            self.org_id, self.store_id, self.unit_id = org.id, store.id, unit.id
            self.location_id, self.manager_id = location.id, manager.id
            self.user_ids = [u.id for u in users]

    def call(self, fn, user_id, *args):
        with self.factory() as db:
            return fn(db, db.get(User, user_id), *args)

    def product(self):
        with self.factory() as db:
            p = ProductReference(organization_id=self.org_id, external_id=uuid4().hex, name="P",
                                 unit_id=self.unit_id, last_synced_at=datetime.now(UTC))
            db.add(p)
            db.commit()
            return p.id

    def session(self, sector=None):
        return self.call(inv.create_session, self.manager_id,
                         SessionCreate(location_id=self.location_id, name="S", sector=sector)).id

    def count_call(self, user_id, session_id, product_id, qty, key=None):
        body = CountIn(product_id=product_id, unit_id=self.unit_id, quantity=Decimal(qty),
                       client_key=key or uuid4().hex)
        return lambda: self.call(inv.record_count, user_id, session_id, body)

    def item(self, product_id):
        with self.factory() as db:
            return db.scalars(select(InventoryItem).where(InventoryItem.product_id == product_id)).all()

    def counts(self, item_id):
        with self.factory() as db:
            return db.scalar(select(func.count()).select_from(InventoryCount).where(InventoryCount.item_id == item_id))


@pytest.fixture()
def sc(db_factory):
    return Scenario(db_factory)


# ---------- contagens ----------
def test_simultaneous_different_counts_always_conflict(sc):
    # Sem trava no item, cada contagem se avaliaria sozinha e a divergência sumiria.
    sid = sc.session()
    for _ in range(ROUNDS):
        pid = sc.product()
        results = run_all([sc.count_call(u, sid, pid, str(i + 1)) for i, u in enumerate(sc.user_ids)])
        assert failures(results) == []
        items = sc.item(pid)
        assert len(items) == 1, "dois itens para o mesmo produto"
        assert sc.counts(items[0].id) == len(sc.user_ids)
        assert items[0].status.value == "needs_review"
        assert "count_conflict" in items[0].review_reasons
        assert items[0].accepted_quantity is None


def test_simultaneous_recounts_with_different_values_always_conflict(sc):
    # O caso em que só a trava protege: o item já existe e a rodada nova está vazia, então cada
    # contagem, sem enxergar a outra, se acharia a única e a divergência seria aceita em silêncio.
    sid = sc.session()
    for _ in range(ROUNDS):
        pid = sc.product()
        sc.count_call(sc.user_ids[0], sid, pid, "100")()
        item_id = sc.item(pid)[0].id
        sc.call(inv.request_recount, sc.manager_id, sid, RecountIn(item_id=item_id, reason="ver"))
        results = run_all([sc.count_call(u, sid, pid, str(i + 1)) for i, u in enumerate(sc.user_ids)])
        assert failures(results) == []
        item = sc.item(pid)[0]
        assert item.status.value == "needs_review"
        assert "count_conflict" in item.review_reasons
        assert item.accepted_quantity is None
        assert sc.counts(item_id) == 1 + len(sc.user_ids)


def test_count_never_lands_after_session_is_cancelled(sc):
    # Sem a trava na sessão, a contagem que já passou pela checagem de estado grava depois do cancelamento.
    for _ in range(ROUNDS):
        sid = sc.session()
        pids = [sc.product() for _ in sc.user_ids]
        fns = [sc.count_call(u, sid, p, "1") for u, p in zip(sc.user_ids, pids, strict=True)]
        results = run_all([*fns, lambda: sc.call(inv.cancel_session, sc.manager_id, sid, "fim da rodada")])
        assert failures(results, inv.ConflictError) == []
        with sc.factory() as db:
            closed_at = db.get(InventorySession, sid).closed_at
            last = db.scalar(select(func.max(InventoryCount.counted_at)).where(InventoryCount.session_id == sid))
        assert last is None or last <= closed_at, "contagem gravada depois do cancelamento"


def test_simultaneous_equal_counts_are_accepted_once_without_sum(sc):
    sid = sc.session()
    for _ in range(ROUNDS):
        pid = sc.product()
        results = run_all([sc.count_call(u, sid, pid, "5") for u in sc.user_ids])
        assert failures(results) == []
        item = sc.item(pid)[0]
        assert item.status.value == "counted" and item.review_reasons == []
        assert item.accepted_quantity == Decimal("5")


def test_simultaneous_retries_with_same_key_create_one_count(sc):
    sid = sc.session()
    for _ in range(ROUNDS):
        pid, key = sc.product(), uuid4().hex
        results = run_all([sc.count_call(sc.user_ids[0], sid, pid, "5", key) for _ in range(8)])
        assert failures(results) == []
        assert sum(r[2] for r in results) == 1
        assert len({r[0].id for r in results}) == 1
        assert sc.counts(sc.item(pid)[0].id) == 1


def test_count_racing_with_submit_review_is_all_or_nothing(sc):
    for _ in range(ROUNDS):
        sid = sc.session()
        pids = [sc.product() for _ in sc.user_ids]
        fns = [sc.count_call(u, sid, p, "1") for u, p in zip(sc.user_ids, pids, strict=True)]
        results = run_all([*fns, lambda: sc.call(inv.submit_for_review, sc.manager_id, sid)])
        assert failures(results, inv.ConflictError) == []
        assert isinstance(results[-1], InventorySession)
        for pid, result in zip(pids, results, strict=False):
            # Recusada não deixa rastro; aceita deixa item e contagem.
            assert len(sc.item(pid)) == (0 if isinstance(result, inv.ConflictError) else 1)
        sc.call(inv.cancel_session, sc.manager_id, sid, "fim da rodada")


# ---------- revisão ----------
def _session_with_conflict(sc, sid=None):
    sid = sid or sc.session()
    pid = sc.product()
    a = sc.count_call(sc.user_ids[0], sid, pid, "1")()
    b = sc.count_call(sc.user_ids[1], sid, pid, "2")()
    return sid, pid, sc.item(pid)[0].id, a[0].id, b[0].id


def test_close_never_passes_a_recount_requested_at_same_time(sc):
    for _ in range(ROUNDS):
        sid = sc.session()
        pid = sc.product()
        sc.count_call(sc.user_ids[0], sid, pid, "1")()
        item_id = sc.item(pid)[0].id
        sc.call(inv.submit_for_review, sc.manager_id, sid)
        results = run_all([
            lambda: sc.call(inv.close_session, sc.manager_id, sid),
            lambda: sc.call(inv.request_recount, sc.manager_id, sid, RecountIn(item_id=item_id, reason="ver")),
        ])
        assert failures(results, inv.ConflictError) == []
        closed, recount = results
        assert isinstance(closed, inv.ConflictError) != isinstance(recount, inv.ConflictError)
        status = sc.item(pid)[0].status.value
        if isinstance(closed, InventorySession):
            assert status == "counted"
        else:
            assert status == "recount_requested"
            sc.call(inv.cancel_session, sc.manager_id, sid, "fim da rodada")


def test_simultaneous_recount_requests_open_one_round(sc):
    for _ in range(ROUNDS):
        sid, pid, item_id, _, _ = _session_with_conflict(sc)
        results = run_all([
            lambda: sc.call(inv.request_recount, sc.manager_id, sid, RecountIn(item_id=item_id, reason="ver"))
            for _ in range(4)
        ])
        assert failures(results, inv.ConflictError) == []
        assert sum(not isinstance(r, Exception) for r in results) == 1
        assert sc.item(pid)[0].current_round == 2
        sc.call(inv.cancel_session, sc.manager_id, sid, "fim da rodada")


def test_simultaneous_decisions_accept_only_one(sc):
    for _ in range(ROUNDS):
        sid, pid, item_id, a, b = _session_with_conflict(sc)
        results = run_all([
            lambda c=c: sc.call(inv.resolve_item, sc.manager_id, sid, item_id,
                                ResolveIn(count_id=c, justification="conferido"))
            for c in (a, b)
        ])
        assert failures(results, inv.ConflictError) == []
        winners = [r for r in results if not isinstance(r, Exception)]
        assert len(winners) == 1
        assert sc.item(pid)[0].accepted_count_id == winners[0].accepted_count_id
        sc.call(inv.cancel_session, sc.manager_id, sid, "fim da rodada")


def test_simultaneous_session_creation_creates_one(sc):
    for _ in range(ROUNDS):
        results = run_all([
            lambda: sc.call(inv.create_session, sc.manager_id, SessionCreate(location_id=sc.location_id, name="S"))
            for _ in range(6)
        ])
        assert failures(results, inv.ConflictError) == []
        created = [r for r in results if isinstance(r, InventorySession)]
        assert len(created) == 1
        with sc.factory() as db:
            active = db.scalar(select(func.count()).select_from(InventorySession).where(
                InventorySession.status.in_([SessionStatus.OPEN, SessionStatus.IN_REVIEW])))
        assert active == 1
        sc.call(inv.cancel_session, sc.manager_id, created[0].id, "fim da rodada")


# ---------- login ----------
def test_simultaneous_refresh_of_same_token_keeps_login(sc):
    # Duas abas renovando juntas não podem derrubar o login nem parecer roubo.
    def rotate(raw):
        with sc.factory() as db:
            return refresh_tokens.rotate(db, raw)

    for _ in range(ROUNDS):
        with sc.factory() as db:
            raw = refresh_tokens.issue(db, db.get(User, sc.manager_id))
            db.commit()
        results = run_all([lambda: rotate(raw) for _ in range(6)])
        assert failures(results) == []
        assert len({r[1] for r in results}) == 6
        assert all(rotate(r[1]) for r in results)


def test_burst_of_simultaneous_logins_does_not_escape_the_limit(sc):
    # O contador é reservado sob trava antes de conferir a senha: um lote não passa todo de uma vez.
    def attempt(email):
        with sc.factory() as db:
            return auth_svc.login(db, email, "senha-errada-123", "10.0.0.1")

    for round_number in range(ROUNDS):
        email = f"alvo{round_number}@a.com"
        results = run_all([lambda: attempt(email) for _ in range(20)])
        assert failures(results, login_throttle.TooManyAttempts) == []
        checked = [r for r in results if not isinstance(r, Exception)]
        assert len(checked) == 5, f"{len(checked)} tentativas conferidas, limite é 5"


# ---------- recebimento ----------
def _access_key(prefix: int) -> str:
    body = f"{prefix:043d}"
    total, weight = 0, 2
    for digit in reversed(body):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    return f"{body}{0 if rest in (0, 1) else 11 - rest}"


def _receiving_body(sc, key=None, access_key=None):
    return ReceivingCreate(client_request_id=key or uuid4().hex, store_id=sc.store_id, location_id=sc.location_id,
                           supplier_name="Fornecedor", access_key=access_key)


def test_simultaneous_receiving_retries_with_same_key_create_one(sc):
    for _ in range(ROUNDS):
        body = _receiving_body(sc)
        results = run_all([lambda: sc.call(rec_svc.create, sc.manager_id, body) for _ in range(6)])
        assert failures(results) == [], "reenvio com a mesma chave deve devolver o registro original"
        assert sum(r[1] for r in results) == 1
        assert len({r[0].id for r in results}) == 1


def test_same_invoice_received_at_same_time_creates_one(sc):
    for round_number in range(ROUNDS):
        access_key = _access_key(round_number + 1)
        results = run_all([
            lambda: sc.call(rec_svc.create, sc.manager_id, _receiving_body(sc, access_key=access_key))
            for _ in range(6)
        ])
        assert failures(results, rec_svc.ConflictError) == []
        assert sum(not isinstance(r, Exception) for r in results) == 1
        with sc.factory() as db:
            assert db.scalar(select(func.count()).select_from(Receiving).where(Receiving.access_key == access_key)) == 1


def test_item_racing_with_finalize_stays_consistent(sc):
    for _ in range(ROUNDS):
        rec = sc.call(rec_svc.create, sc.manager_id, _receiving_body(sc))[0]
        first = ItemCreate(client_request_id=uuid4().hex, product_id=sc.product(), received_quantity=Decimal("1"))
        sc.call(rec_svc.add_item, sc.manager_id, rec.id, first)
        late = [ItemCreate(client_request_id=uuid4().hex, product_id=sc.product(), received_quantity=Decimal("1"))
                for _ in range(4)]
        fns = [lambda body=body: sc.call(rec_svc.add_item, sc.manager_id, rec.id, body) for body in late]
        results = run_all([*fns, lambda: sc.call(rec_svc.finalize, sc.manager_id, rec.id, FinalizeIn(partial=False))])
        assert failures(results, rec_svc.ConflictError) == []
        accepted = sum(not isinstance(r, Exception) for r in results[:-1])
        with sc.factory() as db:
            total = db.scalar(select(func.count()).select_from(ReceivingItem).where(ReceivingItem.receiving_id == rec.id))
            finalized = db.scalar(select(AuditEvent).where(
                AuditEvent.action == "receiving.finalized", AuditEvent.entity_id == str(rec.id)))
        assert total == 1 + accepted
        # O que a finalização registrou precisa ser tudo o que existe: nada entra depois dela.
        assert finalized.details["items"] == total, "item entrou depois da finalização"


def test_simultaneous_finalize_happens_once(sc):
    for _ in range(ROUNDS):
        rec = sc.call(rec_svc.create, sc.manager_id, _receiving_body(sc))[0]
        item = ItemCreate(client_request_id=uuid4().hex, product_id=sc.product(), received_quantity=Decimal("1"))
        sc.call(rec_svc.add_item, sc.manager_id, rec.id, item)
        results = run_all([
            lambda: sc.call(rec_svc.finalize, sc.manager_id, rec.id, FinalizeIn(partial=False)) for _ in range(4)
        ])
        assert failures(results, rec_svc.ConflictError) == []
        assert sum(not isinstance(r, Exception) for r in results) == 1
