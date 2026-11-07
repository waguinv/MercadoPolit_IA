import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import {
  canApprove, canRegisterIn, CONSUMPTION_STATUS,
  type Consumption, type ConsumptionConfig,
} from "../../lib/consumption";
import { formatDate, formatQty, parseQty } from "../../lib/format";
import type { Product } from "../../lib/types";
import { ProductPicker } from "../receiving/ReceivingPage";

type Panel = null | "reject" | "request-correction" | "cancel" | "correct";

const REASON_TITLE: Record<"reject" | "request-correction" | "cancel", [string, string]> = {
  reject: ["Motivo da rejeição", "Rejeitar"],
  "request-correction": ["O que precisa ser corrigido", "Pedir correção"],
  cancel: ["Motivo do cancelamento", "Cancelar consumo"],
};

function ReasonForm({ id, kind, onDone, onBack }: {
  id: string; kind: "reject" | "request-correction" | "cancel";
  onDone: () => Promise<void>; onBack: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [label, button] = REASON_TITLE[kind];
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/internal-consumptions/${id}/${kind}`, { method: "POST", body: { reason: reason.trim() } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }
  return (
    <form className="card stack" onSubmit={submit}>
      <label>
        {label}
        <input autoFocus required minLength={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" onClick={onBack}>Voltar</button>
        <button className={kind === "request-correction" ? "primary grow" : "danger grow"} disabled={busy}>{button}</button>
      </div>
    </form>
  );
}

function CorrectionForm({ item, config, onDone, onBack }: {
  item: Consumption; config: ConsumptionConfig; onDone: () => Promise<void>; onBack: () => void;
}) {
  const user = useUser();
  const departments = config.departments.filter((d) => canRegisterIn(user.role, d));
  const purposes = config.purposes.filter((p) => p.is_active);
  const [departmentId, setDepartmentId] = useState(
    departments.some((d) => d.id === item.department_id) ? item.department_id : departments[0]?.id ?? "");
  const [purposeId, setPurposeId] = useState(
    purposes.some((p) => p.id === item.purpose_id) ? item.purpose_id : purposes[0]?.id ?? "");
  const [product, setProduct] = useState<Pick<Product, "id" | "name">>({ id: item.product_id, name: item.product_name });
  const [picking, setPicking] = useState(false);
  const [quantity, setQuantity] = useState(formatQty(item.quantity));
  const [note, setNote] = useState(item.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const qty = parseQty(quantity, 3);
    if (!qty || Number(qty) <= 0) {
      setError("Informe uma quantidade maior que zero.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/internal-consumptions/${item.id}`, {
        method: "PATCH",
        body: { department_id: departmentId, purpose_id: purposeId, product_id: product.id, quantity: qty,
          note: note.trim() || null },
      });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Corrigir e reenviar</h2>
      <label>
        Departamento
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
          {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <label>
        Finalidade
        <select value={purposeId} onChange={(e) => setPurposeId(e.target.value)}>
          {purposes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      {picking ? <ProductPicker onChoose={(p) => { setProduct(p); setPicking(false); }} /> : (
        <div className="row between">
          <strong>{product.name}</strong>
          <button type="button" className="link" onClick={() => setPicking(true)}>Trocar</button>
        </div>
      )}
      <label>
        Quantidade
        <input inputMode="decimal" required value={quantity} onChange={(e) => setQuantity(e.target.value)} />
      </label>
      <label>
        Observação (opcional)
        <input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" onClick={onBack}>Voltar</button>
        <button className="primary grow" disabled={busy || picking}>Reenviar para aprovação</button>
      </div>
    </form>
  );
}

export function ConsumptionPage() {
  const { consumptionId = "" } = useParams();
  const user = useUser();
  const state = useLoad(async () => ({
    item: await api<Consumption>(`/internal-consumptions/${consumptionId}`),
    config: await api<ConsumptionConfig>("/consumption-config"),
  }), [consumptionId]);
  const [panel, setPanel] = useState<Panel>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = async () => {
    setPanel(null);
    await state.reload();
  };

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      await api(`/internal-consumptions/${consumptionId}/approve`, { method: "POST" });
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="MercadoPilot" subtitle="Consumo interno" back="/consumo">
      <Loaded state={state}>
        {({ item, config }) => {
          const manager = canManage(user);
          const open = item.status === "pending_approval" || item.status === "needs_correction" || item.status === "approved";
          const mine = item.registered_by === user.id;
          const blockedBySegregation = manager && item.status === "pending_approval" && !canApprove(user, item, config.segregation);
          return (
            <div className="stack">
              <div className="row between">
                <h2>{item.product_name}</h2>
                <Badge status={item.status} label={CONSUMPTION_STATUS[item.status]} />
              </div>
              <div className="card stack">
                <p>Quantidade: <strong>{formatQty(item.quantity)} {item.unit_code}</strong></p>
                <p>Departamento: {item.department_name} · Finalidade: {item.purpose_name}</p>
                <p>Local: {item.store_name} · {item.location_name}</p>
                <p>Consumidor: <strong>{item.consumer_name}</strong></p>
                <p className="muted small">Registrado por {item.registered_by_name} em {formatDate(item.created_at)}</p>
                {item.decided_by_name && (
                  <p className="muted small">Decidido por {item.decided_by_name} em {formatDate(item.decided_at)}</p>
                )}
                {item.note && <p className="muted small">Observação: {item.note}</p>}
              </div>
              {item.decision_note && (
                <p className={`notice ${item.status === "needs_correction" ? "warn" : "danger"}`}>
                  {item.status === "needs_correction" ? "Correção pedida: " : item.status === "cancelled" ? "Cancelado: " : "Motivo: "}
                  {item.decision_note}
                </p>
              )}
              {item.status === "approved" && (
                <p className="notice">Aprovado. O estoque do ERP ainda não é baixado: a integração do consumo interno depende de homologação.</p>
              )}
              {blockedBySegregation && (
                <p className="notice warn">Você registrou ou consumiu este item: outra pessoa precisa aprovar.</p>
              )}
              {error && <ErrorBox message={error} />}

              {panel === "correct" && <CorrectionForm item={item} config={config} onDone={done} onBack={() => setPanel(null)} />}
              {(panel === "reject" || panel === "request-correction" || panel === "cancel") && (
                <ReasonForm id={item.id} kind={panel} onDone={done} onBack={() => setPanel(null)} />
              )}

              {panel === null && (
                <div className="stack">
                  {canApprove(user, item, config.segregation) && (
                    <>
                      <button className="primary block" disabled={busy} onClick={() => void approve()}>Aprovar</button>
                      <div className="row">
                        <button className="grow" onClick={() => setPanel("request-correction")}>Pedir correção</button>
                        <button className="grow danger" onClick={() => setPanel("reject")}>Rejeitar</button>
                      </div>
                    </>
                  )}
                  {item.status === "needs_correction" && mine && (
                    <button className="primary block" onClick={() => setPanel("correct")}>Corrigir</button>
                  )}
                  {open && (mine || manager) && (
                    <button className="link danger-text" onClick={() => setPanel("cancel")}>Cancelar consumo</button>
                  )}
                </div>
              )}
            </div>
          );
        }}
      </Loaded>
    </Shell>
  );
}
