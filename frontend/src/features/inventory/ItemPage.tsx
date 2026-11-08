import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, cachedGet, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import { formatDate, formatErpDate, formatQty, ITEM_STATUS, REVIEW_REASON } from "../../lib/format";
import type { AssignableUser, ErpCheckResult, ItemDetail, SessionDetail, Unit } from "../../lib/types";

export function ItemPage() {
  const { sessionId = "", itemId = "" } = useParams();
  const user = useUser();
  const base = `/inventory-sessions/${sessionId}`;
  const state = useLoad(async () => ({
    item: await api<ItemDetail>(`${base}/items/${itemId}`),
    session: await api<SessionDetail>(base),
    units: (await cachedGet<Unit[]>("/units", user.id)).data,
  }), [base, itemId, user.id]);
  const [error, setError] = useState<string | null>(null);
  const [lastDifference, setLastDifference] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function checkErp() {
    setChecking(true);
    setError(null);
    try {
      // O servidor pode esperar o limite de requisições do ERP antes de responder.
      const result = await api<ErpCheckResult>(`${base}/items/${itemId}/erp-check`, { method: "POST", timeoutMs: 120_000 });
      setLastDifference(result.difference);
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setChecking(false);
    }
  }

  return (
    <Shell title={state.data?.item.product_name ?? "Item"} subtitle="Revisão do item" back={`/sessoes/${sessionId}`}>
      <Loaded state={state}>
        {({ item, session, units }) => {
          const unitCode = (id: string) => units.find((u) => u.id === id)?.code ?? "";
          const active = session.status === "open" || session.status === "in_review";
          const manage = canManage(user) && active;
          const current = item.counts.filter((c) => c.round === item.current_round);
          const older = item.counts.filter((c) => c.round !== item.current_round);
          const lastCheck = item.erp_checks.at(-1);
          return (
            <div className="stack">
              <div>
                <Badge status={item.status} label={ITEM_STATUS[item.status]} />
              </div>
              {item.review_reasons.length > 0 && (
                <ul className="notice danger reasons">
                  {item.review_reasons.map((r) => <li key={r}>{REVIEW_REASON[r] ?? r}</li>)}
                </ul>
              )}
              {item.accepted_quantity !== null && (
                <div className="card">
                  <p>Quantidade aceita: <strong>{formatQty(item.accepted_quantity)} {unitCode(item.unit_id)}</strong></p>
                  {item.resolution_note && <p className="muted small">Justificativa: {item.resolution_note}</p>}
                </div>
              )}
              {error && <ErrorBox message={error} />}

              <section className="stack">
                <h2>Contagens da rodada {item.current_round}</h2>
                {current.length === 0 && <p className="empty">Aguardando a reconferência.</p>}
                {manage && item.status === "needs_review"
                  ? <ResolveForm base={base} item={item} unitCode={unitCode} onDone={state.reload} />
                  : (
                    <ul className="plain card">
                      {current.map((c) => (
                        <li key={c.id} className="row between line">
                          <span>{c.counted_by_name}<br /><span className="muted small">{formatDate(c.counted_at)}{c.note ? ` · ${c.note}` : ""}</span></span>
                          <strong>{formatQty(c.quantity)} {unitCode(c.unit_id)}</strong>
                        </li>
                      ))}
                    </ul>
                  )}
              </section>

              <section className="stack">
                <h2>Saldo no ERP</h2>
                {lastCheck ? (
                  <div className="card">
                    <p>Saldo lido: <strong>{formatQty(lastCheck.erp_quantity)} {unitCode(item.unit_id)}</strong></p>
                    {lastDifference !== null && <p>Diferença da contagem aceita: <strong>{formatQty(lastDifference)}</strong></p>}
                    <p className="muted small">Lido em {formatDate(lastCheck.read_at)}. Último movimento: {formatErpDate(lastCheck.erp_last_changed_at)}.</p>
                  </div>
                ) : <p className="muted">Ainda não consultado.</p>}
                {manage && (
                  <button disabled={checking} onClick={() => void checkErp()}>
                    {checking ? "Consultando…" : lastCheck ? "Consultar de novo" : "Consultar saldo no ERP"}
                  </button>
                )}
                <p className="muted small">Consulta somente leitura: nada é alterado no ERP.</p>
              </section>

              {manage && item.status !== "recount_requested" && (
                <RecountForm base={base} item={item} onDone={state.reload} />
              )}

              {(older.length > 0 || item.recounts.length > 0) && (
                <section className="stack">
                  <h2>Histórico</h2>
                  <ul className="plain card">
                    {item.recounts.map((r) => (
                      <li key={r.id} className="line">
                        Reconferência pedida (rodada {r.round}): {r.reason}
                        <br /><span className="muted small">{formatDate(r.requested_at)}</span>
                      </li>
                    ))}
                    {older.map((c) => (
                      <li key={c.id} className="row between line">
                        <span>{c.counted_by_name}<br /><span className="muted small">Rodada {c.round} · {formatDate(c.counted_at)}</span></span>
                        <strong>{formatQty(c.quantity)} {unitCode(c.unit_id)}</strong>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          );
        }}
      </Loaded>
    </Shell>
  );
}

function ResolveForm({ base, item, unitCode, onDone }: {
  base: string; item: ItemDetail; unitCode: (id: string) => string; onDone: () => Promise<void>;
}) {
  const counts = item.counts.filter((c) => c.round === item.current_round);
  const [countId, setCountId] = useState("");
  const [justification, setJustification] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const acceptable = counts.some((c) => !c.unit_issue);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/items/${item.id}/resolve`, { method: "POST", body: { count_id: countId, justification } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <ul className="plain">
        {counts.map((c) => (
          <li key={c.id} className="line">
            <label className="check">
              <input type="radio" name="count" required disabled={!!c.unit_issue} checked={countId === c.id} onChange={() => setCountId(c.id)} />
              <span className="grow">
                {c.counted_by_name}
                <br /><span className="muted small">
                  {formatDate(c.counted_at)}{c.note ? ` · ${c.note}` : ""}
                  {c.unit_issue ? ` · ${REVIEW_REASON[c.unit_issue]}` : ""}
                </span>
              </span>
              <strong>{formatQty(c.quantity)} {unitCode(c.unit_id)}</strong>
            </label>
          </li>
        ))}
      </ul>
      {acceptable ? (
        <>
          <label>
            Justificativa
            <input required minLength={3} maxLength={1000} value={justification} onChange={(e) => setJustification(e.target.value)} />
          </label>
          {error && <ErrorBox message={error} />}
          <button className="primary" disabled={busy || !countId}>Aceitar a contagem escolhida</button>
        </>
      ) : <p className="notice warn">Nenhuma contagem desta rodada pode ser aceita: peça reconferência.</p>}
    </form>
  );
}

function RecountForm({ base, item, onDone }: { base: string; item: ItemDetail; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const users = useLoad(() => api<AssignableUser[]>(`${base}/assignable-users`), [base]);

  if (!open) return <button onClick={() => setOpen(true)}>Pedir reconferência</button>;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/recounts`, {
        method: "POST", body: { item_id: item.id, reason, assigned_user_id: assignee || null },
      });
      setOpen(false);
      setReason("");
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Pedir reconferência</h2>
      <label>
        Motivo
        <input required minLength={3} maxLength={1000} value={reason} placeholder="Não escreva a quantidade esperada"
          onChange={(e) => setReason(e.target.value)} />
      </label>
      <label>
        Quem deve recontar
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Qualquer pessoa da sessão</option>
          {(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
        </select>
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={() => setOpen(false)}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy}>Pedir reconferência</button>
      </div>
    </form>
  );
}
