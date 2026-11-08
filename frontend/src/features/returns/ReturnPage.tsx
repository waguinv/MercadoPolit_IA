import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import type { ConsumptionConfig } from "../../lib/consumption";
import { formatDate, formatDay, formatQty, parseQty } from "../../lib/format";
import {
  canApproveReturn, receivingLabel, remainingToReplace, replacementCandidates, RETURN_MODALITY, RETURN_REASON,
  RETURN_STATUS, type SupplierReturn,
} from "../../lib/returns";
import type { Product, Receiving } from "../../lib/types";
import { ReasonForm } from "../consumption/ConsumptionPage";
import { ProductPicker } from "../receiving/ReceivingPage";
import { fieldsOf, ReturnFieldsForm, returnPayload, type ReturnFields } from "./ReturnForm";

type Panel = null | "reject" | "request-correction" | "cancel" | "correct" | "pickup" | "replacement" | "complete";

function Back({ onBack }: { onBack: () => void }) {
  return <button className="voltar" type="button" onClick={onBack}><span aria-hidden>←</span> Voltar</button>;
}

function CorrectionForm({ item, onDone, onBack }: { item: SupplierReturn; onDone: () => Promise<void>; onBack: () => void }) {
  const [product, setProduct] = useState<Pick<Product, "id" | "name">>({ id: item.product_id, name: item.product_name });
  const [picking, setPicking] = useState(false);
  const [fields, setFields] = useState<ReturnFields>(fieldsOf(item, formatQty(item.quantity)));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const qty = parseQty(fields.quantity, 3);
    if (!qty || Number(qty) <= 0) {
      setError("Informe uma quantidade maior que zero.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/supplier-returns/${item.id}`, { method: "PATCH", body: returnPayload(fields, product.id, qty) });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Corrigir e reenviar</h2>
      {picking ? <ProductPicker onChoose={(p) => { setProduct(p); setPicking(false); }} /> : (
        <div className="row between">
          <strong>{product.name}</strong>
          <button type="button" className="link" onClick={() => setPicking(true)}>Trocar</button>
        </div>
      )}
      <ReturnFieldsForm fields={fields} set={setFields} storeId={item.store_id} unitCode={item.unit_code} />
      {error && <ErrorBox message={error} />}
      <div className="row">
        <Back onBack={onBack} />
        <button className="primary grow" disabled={busy || picking}>Reenviar para aprovação</button>
      </div>
    </form>
  );
}

/** Coleta (observação opcional) e encerramento (justificativa obrigatória). */
function NoteForm({ path, title, label, field, required, submit: submitLabel, onDone, onBack }: {
  path: string; title: string; label: string; field: "note" | "reason"; required: boolean; submit: string;
  onDone: () => Promise<void>; onBack: () => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(path, { method: "POST", body: { [field]: text.trim() || null } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }
  return (
    <form className="card stack" onSubmit={submit}>
      <h2>{title}</h2>
      <label>
        {label}
        <input autoFocus required={required} minLength={required ? 3 : undefined} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <Back onBack={onBack} />
        <button className="primary grow" disabled={busy}>{submitLabel}</button>
      </div>
    </form>
  );
}

function ReplacementForm({ item, onDone, onBack }: { item: SupplierReturn; onDone: () => Promise<void>; onBack: () => void }) {
  const navigate = useNavigate();
  const receivings = useLoad(() => api<Receiving[]>("/receivings?status=finalized"), []);
  const remaining = remainingToReplace(item);
  const [receivingId, setReceivingId] = useState("");
  const [quantity, setQuantity] = useState(formatQty(remaining));
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
      await api(`/supplier-returns/${item.id}/replacements`, { method: "POST", body: { receiving_id: receivingId, quantity: qty } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Loaded state={receivings}>
      {(rows) => {
        const options = replacementCandidates(item, rows);
        if (options.length === 0) {
          return (
            <div className="card stack">
              <h2>Registrar reposição</h2>
              <p>A reposição só entra depois de recebida e conferida. Não há recebimento finalizado de {item.supplier_name} nesta loja.</p>
              <div className="row">
                <Back onBack={onBack} />
                <button type="button" className="primary grow" onClick={() => navigate("/recebimentos")}>Ir para Recebimentos</button>
              </div>
            </div>
          );
        }
        return (
          <form className="card stack" onSubmit={submit}>
            <h2>Registrar reposição</h2>
            <label>
              Recebimento conferido
              <select required value={receivingId} onChange={(e) => setReceivingId(e.target.value)}>
                <option value="">Escolha o recebimento</option>
                {options.map((r) => <option key={r.id} value={r.id}>{receivingLabel(r)}</option>)}
              </select>
            </label>
            <label>
              Quantidade reposta ({item.unit_code}), falta {formatQty(remaining)}
              <input inputMode="decimal" required value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </label>
            {error && <ErrorBox message={error} />}
            <div className="row">
              <Back onBack={onBack} />
              <button className="primary grow" disabled={busy}>Registrar reposição</button>
            </div>
          </form>
        );
      }}
    </Loaded>
  );
}

export function ReturnPage() {
  const { returnId = "" } = useParams();
  const user = useUser();
  const state = useLoad(async () => ({
    item: await api<SupplierReturn>(`/supplier-returns/${returnId}`),
    config: await api<ConsumptionConfig>("/consumption-config"),
  }), [returnId]);
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
      await api(`/supplier-returns/${returnId}/approve`, { method: "POST" });
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="MercadoPilot" subtitle="Devolução ou troca" back="/devolucoes">
      <Loaded state={state}>
        {({ item, config }) => {
          const manager = canManage(user);
          const cancellable = item.status === "pending_approval" || item.status === "needs_correction" || item.status === "approved";
          const mine = item.registered_by === user.id;
          const approvable = canApproveReturn(user, item, config.segregation);
          const path = `/supplier-returns/${item.id}`;
          const exchange = item.modality === "exchange";
          const openPickedUp = item.status === "picked_up";
          return (
            <div className="stack">
              <div className="row between">
                <h2>{item.product_name}</h2>
                <Badge status={item.status} label={RETURN_STATUS[item.status]} />
              </div>
              <div className="card stack">
                <p>Quantidade: <strong>{formatQty(item.quantity)} {item.unit_code}</strong></p>
                <p>Fornecedor: <strong>{item.supplier_name}</strong></p>
                <p>Modalidade: <strong>{RETURN_MODALITY[item.modality]}</strong> · Motivo: <strong>{RETURN_REASON[item.reason]}</strong></p>
                <p>Local: {item.store_name} · {item.location_name}</p>
                {item.receiving_id
                  ? <p>Origem: <Link to={`/recebimentos/${item.receiving_id}`}>{item.origin_document ? `NF ${item.origin_document}` : "Ver recebimento"}</Link></p>
                  : item.origin_document && <p>NF de origem: {item.origin_document}</p>}
                {(item.lot || item.expiry_date) && (
                  <p>{item.lot ? `Lote: ${item.lot}` : ""}{item.lot && item.expiry_date ? " · " : ""}{item.expiry_date ? `Validade: ${formatDay(item.expiry_date)}` : ""}</p>
                )}
                {item.expected_pickup_date && <p>Coleta prevista: {formatDay(item.expected_pickup_date)}</p>}
                {item.expected_replacement_date && <p>Reposição prevista: {formatDay(item.expected_replacement_date)}</p>}
                <p className="muted small">Registrado por {item.registered_by_name} em {formatDate(item.created_at)}</p>
                {item.decided_by_name && <p className="muted small">Decidido por {item.decided_by_name} em {formatDate(item.decided_at)}</p>}
                {item.picked_up_at && (
                  <p className="muted small">Coleta registrada por {item.picked_up_by_name} em {formatDate(item.picked_up_at)}{item.pickup_note ? `: ${item.pickup_note}` : ""}</p>
                )}
                {item.note && <p className="muted small">Observação: {item.note}</p>}
              </div>

              {exchange && item.picked_up_at && (
                <div className="card stack">
                  <h2>Reposição</h2>
                  <p>Reposto: <strong>{formatQty(item.replaced_quantity)} de {formatQty(item.quantity)} {item.unit_code}</strong></p>
                  {item.replacements.map((r) => (
                    <p key={r.id} className="muted small">
                      {formatQty(r.quantity)} {item.unit_code} · <Link to={`/recebimentos/${r.receiving_id}`}>{r.document_number ? `NF ${r.document_number}` : "Ver recebimento"}</Link> · {r.registered_by_name} em {formatDate(r.created_at)}
                    </p>
                  ))}
                </div>
              )}

              {item.decision_note && (
                <p className={`notice ${item.status === "needs_correction" ? "warn" : "danger"}`}>
                  {item.status === "needs_correction" ? "Correção pedida: " : item.status === "cancelled" ? "Cancelada: " : "Motivo: "}
                  {item.decision_note}
                </p>
              )}
              {item.completion_note && <p className="notice">Encerramento: {item.completion_note} ({item.completed_by_name})</p>}
              {item.status !== "rejected" && item.status !== "cancelled" && item.status !== "pending_approval" && item.status !== "needs_correction" && (
                <p className="notice">O estoque do ERP não é movimentado e nenhuma nota é emitida: a integração de devoluções depende de homologação.</p>
              )}
              {manager && item.status === "pending_approval" && !approvable && (
                <p className="notice warn">Você registrou esta devolução: outra pessoa precisa aprovar.</p>
              )}
              {error && <ErrorBox message={error} />}

              {panel === "correct" && <CorrectionForm item={item} onDone={done} onBack={() => setPanel(null)} />}
              {(panel === "reject" || panel === "request-correction" || panel === "cancel") && (
                <ReasonForm path={path} kind={panel} cancelLabel="Cancelar devolução" onDone={done} onBack={() => setPanel(null)} />
              )}
              {panel === "pickup" && (
                <NoteForm path={`${path}/pickup`} title="Registrar coleta" label="Observação (opcional: motorista, transportadora)" field="note"
                  required={false} submit="Confirmar saída da mercadoria" onDone={done} onBack={() => setPanel(null)} />
              )}
              {panel === "complete" && (
                <NoteForm path={`${path}/complete`} title={exchange ? "Encerrar sem reposição completa" : "Registrar crédito recebido"}
                  label={exchange ? "Justificativa" : "Como o crédito foi recebido"} field="reason" required submit="Encerrar devolução" onDone={done} onBack={() => setPanel(null)} />
              )}
              {panel === "replacement" && <ReplacementForm item={item} onDone={done} onBack={() => setPanel(null)} />}

              {panel === null && (
                <div className="stack">
                  {approvable && (
                    <>
                      <button className="primary block" disabled={busy} onClick={() => void approve()}>Aprovar</button>
                      <div className="row">
                        <button className="grow" onClick={() => setPanel("request-correction")}>Pedir correção</button>
                        <button className="grow danger" onClick={() => setPanel("reject")}>Rejeitar</button>
                      </div>
                    </>
                  )}
                  {item.status === "approved" && manager && (
                    <button className="primary block" onClick={() => setPanel("pickup")}>Registrar coleta</button>
                  )}
                  {openPickedUp && manager && exchange && (
                    <button className="primary block" onClick={() => setPanel("replacement")}>Registrar reposição</button>
                  )}
                  {openPickedUp && manager && (
                    <button className={exchange ? "block" : "primary block"} onClick={() => setPanel("complete")}>
                      {exchange ? "Encerrar sem reposição completa" : "Registrar crédito recebido"}
                    </button>
                  )}
                  {item.status === "needs_correction" && mine && (
                    <button className="primary block" onClick={() => setPanel("correct")}>Corrigir</button>
                  )}
                  {cancellable && (mine || manager) && (
                    <button className="link danger-text" onClick={() => setPanel("cancel")}>Cancelar devolução</button>
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
