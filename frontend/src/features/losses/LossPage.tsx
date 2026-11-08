import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import type { ConsumptionConfig } from "../../lib/consumption";
import { formatDate, formatDay, formatQty, parseQty } from "../../lib/format";
import { canApproveLoss, LOSS_REASON, LOSS_STATUS, type Loss } from "../../lib/losses";
import type { Product } from "../../lib/types";
import { ReasonForm } from "../consumption/ConsumptionPage";
import { ProductPicker } from "../receiving/ReceivingPage";
import { LossFieldsForm, lossPayload, type LossFields } from "./LossForm";

type Panel = null | "reject" | "request-correction" | "cancel" | "correct";

function CorrectionForm({ item, onDone, onBack }: { item: Loss; onDone: () => Promise<void>; onBack: () => void }) {
  const [product, setProduct] = useState<Pick<Product, "id" | "name">>({ id: item.product_id, name: item.product_name });
  const [picking, setPicking] = useState(false);
  const [fields, setFields] = useState<LossFields>({
    reason: item.reason, quantity: formatQty(item.quantity), lot: item.lot ?? "", expiry: item.expiry_date ?? "",
    note: item.note ?? "",
  });
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
      await api(`/stock-losses/${item.id}`, { method: "PATCH", body: lossPayload(fields, product.id, qty) });
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
      <LossFieldsForm fields={fields} set={setFields} />
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={onBack}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy || picking}>Reenviar para aprovação</button>
      </div>
    </form>
  );
}

export function LossPage() {
  const { lossId = "" } = useParams();
  const user = useUser();
  const state = useLoad(async () => ({
    item: await api<Loss>(`/stock-losses/${lossId}`),
    config: await api<ConsumptionConfig>("/consumption-config"),
  }), [lossId]);
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
      await api(`/stock-losses/${lossId}/approve`, { method: "POST" });
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="MercadoPilot" subtitle="Perdas e avarias" back="/perdas">
      <Loaded state={state}>
        {({ item, config }) => {
          const manager = canManage(user);
          const open = item.status === "pending_approval" || item.status === "needs_correction" || item.status === "approved";
          const mine = item.registered_by === user.id;
          const approvable = canApproveLoss(user, item, config.segregation);
          return (
            <div className="stack">
              <div className="row between">
                <h2>{item.product_name}</h2>
                <Badge status={item.status} label={LOSS_STATUS[item.status]} />
              </div>
              <div className="card stack">
                <p>Quantidade: <strong>{formatQty(item.quantity)} {item.unit_code}</strong></p>
                <p>Motivo: <strong>{LOSS_REASON[item.reason]}</strong></p>
                <p>Local: {item.store_name} · {item.location_name}</p>
                {(item.lot || item.expiry_date) && (
                  <p>{item.lot ? `Lote: ${item.lot}` : ""}{item.lot && item.expiry_date ? " · " : ""}{item.expiry_date ? `Validade: ${formatDay(item.expiry_date)}` : ""}</p>
                )}
                <p className="muted small">Registrado por {item.registered_by_name} em {formatDate(item.created_at)}</p>
                {item.decided_by_name && (
                  <p className="muted small">Decidido por {item.decided_by_name} em {formatDate(item.decided_at)}</p>
                )}
                {item.note && <p className="muted small">Observação: {item.note}</p>}
              </div>
              {item.decision_note && (
                <p className={`notice ${item.status === "needs_correction" ? "warn" : "danger"}`}>
                  {item.status === "needs_correction" ? "Correção pedida: " : item.status === "cancelled" ? "Cancelada: " : "Motivo: "}
                  {item.decision_note}
                </p>
              )}
              {item.status === "approved" && (
                <p className="notice">Aprovada. O estoque do ERP ainda não é baixado: a integração de perdas depende de homologação.</p>
              )}
              {manager && item.status === "pending_approval" && !approvable && (
                <p className="notice warn">Você registrou esta perda: outra pessoa precisa aprovar.</p>
              )}
              {error && <ErrorBox message={error} />}

              {panel === "correct" && <CorrectionForm item={item} onDone={done} onBack={() => setPanel(null)} />}
              {(panel === "reject" || panel === "request-correction" || panel === "cancel") && (
                <ReasonForm path={`/stock-losses/${item.id}`} kind={panel} cancelLabel="Cancelar perda" onDone={done} onBack={() => setPanel(null)} />
              )}

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
                  {item.status === "needs_correction" && mine && (
                    <button className="primary block" onClick={() => setPanel("correct")}>Corrigir</button>
                  )}
                  {open && (mine || manager) && (
                    <button className="link danger-text" onClick={() => setPanel("cancel")}>Cancelar perda</button>
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
