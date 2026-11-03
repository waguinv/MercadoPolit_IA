import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { Badge, ErrorBox, Loaded, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import { formatDate, formatQty, ITEM_STATUS, REVIEW_REASON, ROLE } from "../../lib/format";
import type { AssignableUser, Assignment, Item, SessionDetail } from "../../lib/types";

export function ReviewTab({ session, onChanged }: { session: SessionDetail; onChanged: () => void | Promise<void> }) {
  const user = useUser();
  const base = `/inventory-sessions/${session.id}`;
  const [filter, setFilter] = useState("");
  const items = useLoad(() => api<Item[]>(`${base}/items?limit=200${filter ? `&status=${filter}` : ""}`), [base, filter]);
  const active = session.status === "open" || session.status === "in_review";
  const pending = (session.items_by_status.needs_review ?? 0) + (session.items_by_status.recount_requested ?? 0);

  // Outras pessoas contam ao mesmo tempo: o resumo precisa acompanhar a lista ao abrir a aba.
  useEffect(() => {
    void onChanged();
  }, [onChanged]);

  return (
    <div className="stack">
      <div className="stats">
        <div><strong>{session.items_total}</strong><span>itens</span></div>
        <div><strong>{session.items_by_status.needs_review ?? 0}</strong><span>em revisão</span></div>
        <div><strong>{session.items_by_status.recount_requested ?? 0}</strong><span>reconferência</span></div>
      </div>

      {session.status === "closed" && (
        <p className="notice">Sessão encerrada em {formatDate(session.closed_at)}. A contagem foi aprovada, mas <strong>nada foi enviado ao ERP</strong>.</p>
      )}
      {session.status === "cancelled" && <p className="notice">Cancelada: {session.cancel_reason}</p>}

      {canManage(user) && active && (
        <SessionActions session={session} pending={pending} onChanged={async () => {
          await onChanged();
          await items.reload();
        }} />
      )}

      <label>
        Filtrar itens
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Todos</option>
          {Object.entries(ITEM_STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>

      <Loaded state={items}>
        {(rows) => (
          <>
            <ul className="list">
              {rows.map((item) => (
                <li key={item.id}>
                  <Link to={`/sessoes/${session.id}/itens/${item.id}`} className="card link-card">
                    <div className="row between">
                      <strong>{item.product_name}</strong>
                      <Badge status={item.status} label={ITEM_STATUS[item.status]} />
                    </div>
                    {item.accepted_quantity !== null && <span>Aceito: <strong>{formatQty(item.accepted_quantity)}</strong></span>}
                    {item.review_reasons.map((r) => <span key={r} className="muted small">{REVIEW_REASON[r] ?? r}</span>)}
                  </Link>
                </li>
              ))}
            </ul>
            {rows.length === 0 && <p className="empty">Nenhum item aqui. Só produtos contados aparecem; os não contados não viram saldo zero.</p>}
          </>
        )}
      </Loaded>

      {canManage(user) && active && <Assignments session={session} />}
    </div>
  );
}

function SessionActions({ session, pending, onChanged }: {
  session: SessionDetail; pending: number; onChanged: () => Promise<void>;
}) {
  const base = `/inventory-sessions/${session.id}`;
  const [panel, setPanel] = useState<"close" | "cancel" | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/${path}`, { method: "POST", body });
      setPanel(null);
      await onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack">
      {error && <ErrorBox message={error} />}
      {session.status === "open" && (
        <button className="primary block" disabled={busy} onClick={() => void run("submit-review")}>Terminar contagem e revisar</button>
      )}
      {session.status === "in_review" && panel === null && (
        <button className="primary block" disabled={pending > 0 || session.items_total === 0} onClick={() => setPanel("close")}>
          {pending > 0 ? `Encerrar (faltam ${pending} item(ns))` : "Encerrar sessão"}
        </button>
      )}
      {panel === "close" && (
        <div className="card stack">
          <p>Encerrar aprova as quantidades aceitas. Depois disso a sessão não muda mais. <strong>Nada será enviado ao ERP.</strong></p>
          <div className="row">
            <button onClick={() => setPanel(null)}>Voltar</button>
            <button className="primary grow" disabled={busy} onClick={() => void run("close")}>Confirmar encerramento</button>
          </div>
        </div>
      )}
      {panel === null && <button className="link danger-text" onClick={() => setPanel("cancel")}>Cancelar sessão</button>}
      {panel === "cancel" && (
        <form className="card stack" onSubmit={(e: FormEvent) => {
          e.preventDefault();
          void run("cancel", { reason });
        }}>
          <label>
            Motivo do cancelamento
            <input required minLength={3} maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="row">
            <button type="button" onClick={() => setPanel(null)}>Voltar</button>
            <button className="danger grow" disabled={busy}>Confirmar cancelamento</button>
          </div>
        </form>
      )}
    </section>
  );
}

function Assignments({ session }: { session: SessionDetail }) {
  const base = `/inventory-sessions/${session.id}`;
  const state = useLoad(async () => ({
    assigned: await api<Assignment[]>(`${base}/assignments`),
    users: await api<AssignableUser[]>(`${base}/assignable-users`),
  }), [base]);
  const [error, setError] = useState<string | null>(null);

  async function change(run: () => Promise<unknown>) {
    setError(null);
    try {
      await run();
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <section className="stack">
      <h2>Quem pode contar</h2>
      {error && <ErrorBox message={error} />}
      <Loaded state={state}>
        {({ assigned, users }) => {
          const ids = new Set(assigned.map((a) => a.user_id));
          const operators = users.filter((u) => u.role === "operator");
          return (
            <>
              <p className="muted small">
                {ids.size === 0 ? "Sem atribuição: todos os operadores da loja podem contar." : "Só os marcados (e gestores) podem contar."}
              </p>
              <ul className="plain card">
                {operators.map((u) => (
                  <li key={u.id} className="line">
                    <label className="check">
                      <input type="checkbox" checked={ids.has(u.id)} onChange={() => void change(() => ids.has(u.id)
                        ? api(`${base}/assignments/${u.id}`, { method: "DELETE" })
                        : api(`${base}/assignments`, { method: "POST", body: { user_id: u.id } }))} />
                      {u.full_name} <span className="muted small">{ROLE[u.role]}</span>
                    </label>
                  </li>
                ))}
              </ul>
              {operators.length === 0 && <p className="empty">Nenhum operador disponível para esta loja.</p>}
            </>
          );
        }}
      </Loaded>
    </section>
  );
}
