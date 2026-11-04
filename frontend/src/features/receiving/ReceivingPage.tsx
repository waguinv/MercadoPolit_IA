import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canCount, canManage, useUser } from "../../lib/auth";
import { formatDate, formatDay, formatQty, isPositiveQty, localDay } from "../../lib/format";
import { acceptedOf, RECEIVING_STATUS, REJECTION_REASON, RequestKey, validateItem } from "../../lib/receiving";
import type { Product, ReceivingDetail, ReceivingItem, RejectionReason, Unit } from "../../lib/types";
import { useLocations } from "../inventory/SessionsPage";

function productInfo(p: Product) {
  return [p.sku, p.barcode].filter(Boolean).join(" · ");
}

function ProductPicker({ onChoose }: { onChoose: (p: Product) => void }) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const term = search.trim();
    setError(null);
    if (term.length < 2) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const looksLikeBarcode = /^\d{8,14}$/.test(term);
      try {
        const params = new URLSearchParams(looksLikeBarcode ? { barcode: term } : { q: term, limit: "20" });
        const found = await api<Product[]>(`/products?${params}`);
        if (cancelled) return;
        setResults(found);
        if (looksLikeBarcode && found.length === 1) onChoose(found[0]);
      } catch (err) {
        if (cancelled) return;
        setError(errorMessage(err));
        setResults(null);
      }
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search, onChoose]);

  return (
    <section className="stack">
      <label>
        Adicionar produto
        <input type="search" value={search} placeholder="Nome, código ou código de barras" onChange={(e) => setSearch(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      {results?.length === 0 && <p className="empty">Nenhum produto encontrado.</p>}
      {results && results.length > 0 && (
        <ul className="plain card">
          {results.map((p) => (
            <li key={p.id}>
              <button className="row-button" onClick={() => onChoose(p)}>
                <strong>{p.name}</strong>
                <span className="muted small">{productInfo(p)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Formulário de item: novo (com `product`) ou edição (com `item`). Lote e validade só existem no lançamento. */
function ItemFormCard({ base, receivedOn, units, product, item, onDone, onCancel }: {
  base: string;
  receivedOn: string;
  units: Unit[];
  product?: Product;
  item?: ReceivingItem;
  onDone: (message: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const requestKey = useRef(new RequestKey());
  const startQty = (value: string | null | undefined) => (value ? formatQty(value) : "");
  const [expected, setExpected] = useState(startQty(item?.expected_quantity));
  const [received, setReceived] = useState(startQty(item?.received_quantity));
  const [rejected, setRejected] = useState(isPositiveQty(item?.rejected_quantity) ? startQty(item?.rejected_quantity) : "");
  const [reason, setReason] = useState<RejectionReason | "">(item?.rejection_reason ?? "");
  const [lot, setLot] = useState("");
  const [expiry, setExpiry] = useState("");
  const [note, setNote] = useState(item?.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const name = item?.product_name ?? product?.name ?? "";
  const unit = units.find((u) => u.id === (item?.unit_id ?? product?.unit_id));
  const accepted = acceptedOf(received, rejected);
  const hasRejection = isPositiveQty(rejected.replace(",", "."));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!unit) return;
    const result = validateItem({
      expected, received, rejected, reason, receivedOn, expiry: item ? (item.expiry_date ?? "") : expiry,
      unitCode: unit.code, allowsFraction: unit.allows_fraction,
    });
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const quantities = {
      expected_quantity: result.expected, received_quantity: result.received, rejected_quantity: result.rejected,
      rejection_reason: result.reason, note: note.trim() || null,
    };
    setBusy(true);
    setError(null);
    try {
      if (item) {
        await api(`${base}/items/${item.id}`, { method: "PATCH", body: quantities });
      } else {
        const body = { ...quantities, product_id: product?.id, lot: lot.trim(), expiry_date: expiry || null };
        await api(`${base}/items`, { method: "POST", body: { ...body, client_request_id: requestKey.current.for(body) } });
        requestKey.current.reset();
      }
      await onDone(`Salvo: ${name}, ${formatQty(result.accepted)} ${unit.code} aceito(s).`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div>
        <h2>{name}</h2>
        {product && <span className="muted small">{productInfo(product)}</span>}
      </div>
      <div className="fields">
        <label>
          Recebido{unit ? ` (${unit.code})` : ""}
          <input autoFocus className="qty" inputMode="decimal" autoComplete="off" required value={received} onChange={(e) => setReceived(e.target.value)} />
        </label>
        <label>
          Esperado (opcional)
          <input className="qty" inputMode="decimal" autoComplete="off" value={expected} onChange={(e) => setExpected(e.target.value)} />
        </label>
        <label>
          Rejeitado
          <input className="qty" inputMode="decimal" autoComplete="off" placeholder="0" value={rejected} onChange={(e) => setRejected(e.target.value)} />
        </label>
        <div className="computed">
          <span className="muted small">Aceito (calculado)</span>
          <strong>{accepted === null ? "—" : formatQty(accepted)}</strong>
        </div>
      </div>
      {hasRejection && (
        <label>
          Motivo da rejeição
          <select required value={reason} onChange={(e) => setReason(e.target.value as RejectionReason | "")}>
            <option value="">Selecione</option>
            {Object.entries(REJECTION_REASON).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      )}
      {item ? (
        (item.lot || item.expiry_date) && (
          <p className="muted small">
            {item.lot ? `Lote ${item.lot}` : "Sem lote"} · Validade {formatDay(item.expiry_date)}. Para mudar, remova o item e lance de novo.
          </p>
        )
      ) : (
        <div className="fields">
          <label>
            Lote (opcional)
            <input maxLength={64} autoComplete="off" value={lot} onChange={(e) => setLot(e.target.value)} />
          </label>
          <label>
            Validade (opcional)
            <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
          </label>
        </div>
      )}
      <label>
        Observação (opcional)
        <input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" onClick={onCancel}>Voltar</button>
        <button className="primary grow" disabled={busy || !unit}>Salvar item</button>
      </div>
    </form>
  );
}

function ItemLine({ item, unitCode, editable, onEdit, onRemove }: {
  item: ReceivingItem;
  unitCode: string;
  editable: boolean;
  onEdit: () => void;
  onRemove: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const details = [
    item.expected_quantity !== null ? `Esperado ${formatQty(item.expected_quantity)}` : null,
    isPositiveQty(item.rejected_quantity)
      ? `Rejeitado ${formatQty(item.rejected_quantity)}${item.rejection_reason ? ` (${REJECTION_REASON[item.rejection_reason]})` : ""}`
      : null,
    item.lot ? `Lote ${item.lot}` : null,
    item.expiry_date ? `Validade ${formatDay(item.expiry_date)}` : null,
  ].filter(Boolean).join(" · ");

  async function remove() {
    setBusy(true);
    await onRemove();
    setBusy(false);
    setConfirming(false);
  }

  return (
    <li className="line stack-tight">
      <div className="row between">
        <span>{item.product_name}</span>
        <strong>Aceito {formatQty(item.accepted_quantity)} {unitCode}</strong>
      </div>
      <span className="muted small">
        Recebido {formatQty(item.received_quantity)} {unitCode}{details ? ` · ${details}` : ""}
      </span>
      {isPositiveQty(item.shortage_quantity) && <span className="badge warn fit">Falta {formatQty(item.shortage_quantity)} {unitCode}</span>}
      {isPositiveQty(item.excess_quantity) && <span className="badge warn fit">Excesso {formatQty(item.excess_quantity)} {unitCode}</span>}
      {item.note && <span className="muted small">{item.note}</span>}
      {editable && (
        <div className="row">
          {confirming ? (
            <>
              <button type="button" className="link" disabled={busy} onClick={() => setConfirming(false)}>Manter</button>
              <button type="button" className="link danger-text" disabled={busy} onClick={() => void remove()}>Confirmar remoção</button>
            </>
          ) : (
            <>
              <button type="button" className="link" onClick={onEdit}>Editar</button>
              <button type="button" className="link danger-text" onClick={() => setConfirming(true)}>Remover</button>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function FinalizePanel({ base, items, onDone }: { base: string; items: ReceivingItem[]; onDone: () => void | Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [partial, setPartial] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const shortages = items.filter((i) => isPositiveQty(i.shortage_quantity)).length;
  const excesses = items.filter((i) => isPositiveQty(i.excess_quantity)).length;
  const rejections = items.filter((i) => isPositiveQty(i.rejected_quantity)).length;

  if (!open) return <button className="primary block" onClick={() => setOpen(true)}>Finalizar recebimento</button>;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/finalize`, { method: "POST", body: { partial: shortages > 0 && partial === true } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      await onDone();
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h2>Finalizar recebimento</h2>
      <ul className="reasons small">
        <li>{items.length} item(ns) conferido(s)</li>
        <li>{shortages} com falta</li>
        <li>{excesses} com excesso</li>
        <li>{rejections} com rejeição</li>
      </ul>
      {shortages > 0 && (
        <fieldset className="choice">
          <legend>O que fazer com as faltas?</legend>
          <label className="check">
            <input type="radio" name="faltas" checked={partial === true} onChange={() => setPartial(true)} />
            Entrega parcial: as faltas ficam pendentes
          </label>
          <label className="check">
            <input type="radio" name="faltas" checked={partial === false} onChange={() => setPartial(false)} />
            Entrega encerrada: as faltas são definitivas
          </label>
        </fieldset>
      )}
      <p className="notice warn">Depois de finalizar, este recebimento não pode ser alterado nem cancelado.</p>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" disabled={busy} onClick={() => setOpen(false)}>Voltar</button>
        <button className="primary grow" disabled={busy || (shortages > 0 && partial === null)} onClick={() => void submit()}>
          Confirmar finalização
        </button>
      </div>
    </section>
  );
}

function CancelPanel({ base, onDone }: { base: string; onDone: () => void | Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) return <button className="link danger-text" onClick={() => setOpen(true)}>Cancelar recebimento</button>;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim().length < 5) {
      setError("Explique o motivo com pelo menos 5 letras.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/cancel`, { method: "POST", body: { reason: reason.trim() } });
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
      await onDone();
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Cancelar recebimento</h2>
      <label>
        Motivo
        <input autoFocus required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" disabled={busy} onClick={() => setOpen(false)}>Voltar</button>
        <button className="danger grow" disabled={busy}>Confirmar cancelamento</button>
      </div>
    </form>
  );
}

function outcome(r: ReceivingDetail): string | null {
  if (r.status === "cancelled") return `Cancelado em ${formatDate(r.cancelled_at)}. Motivo: ${r.cancel_reason ?? "não informado"}`;
  if (r.status !== "finalized") return null;
  const hasShortage = r.items.some((i) => isPositiveQty(i.shortage_quantity));
  const shortageText = !hasShortage ? "" : r.is_partial ? " Entrega parcial: as faltas estão pendentes." : " As faltas são definitivas.";
  return `Finalizado em ${formatDate(r.finalized_at)}.${shortageText}`;
}

function ReceivingContent({ receiving, base, units, locationLabel, reload }: {
  receiving: ReceivingDetail;
  base: string;
  units: Unit[];
  locationLabel: string;
  reload: () => Promise<void>;
}) {
  const user = useUser();
  const [product, setProduct] = useState<Product | null>(null);
  const [editing, setEditing] = useState<ReceivingItem | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isDraft = receiving.status === "draft";
  const editable = isDraft && canCount(user);
  const unitCode = (id: string) => units.find((u) => u.id === id)?.code ?? "";
  const documentText = [
    receiving.document_number && `Nota ${receiving.document_number}`,
    receiving.document_series && `Série ${receiving.document_series}`,
    receiving.supplier_document && `Documento ${receiving.supplier_document}`,
  ].filter(Boolean).join(" · ");
  const result = outcome(receiving);

  // Estável: a busca de produto refaz a consulta quando esta função muda.
  const choose = useCallback((p: Product) => {
    setProduct(p);
    setFeedback(null);
  }, []);

  async function saved(message: string) {
    await reload();
    setProduct(null);
    setEditing(null);
    setFeedback(message);
  }

  async function remove(item: ReceivingItem) {
    setError(null);
    setFeedback(null);
    try {
      await api(`${base}/items/${item.id}`, { method: "DELETE" });
    } catch (err) {
      setError(errorMessage(err));
    }
    await reload();
  }

  return (
    <div className="stack">
      <section className="card">
        <div className="row between">
          <h2>{receiving.supplier_name}</h2>
          <Badge status={receiving.status} label={RECEIVING_STATUS[receiving.status]} />
        </div>
        <span className="muted">{locationLabel}</span>
        {documentText && <span className="muted small">{documentText}</span>}
        {receiving.access_key && <span className="muted small break">Chave {receiving.access_key}</span>}
        <span className="muted small">Recebido em {formatDate(receiving.received_at)}</span>
        {receiving.note && <span className="muted small">{receiving.note}</span>}
      </section>

      {result && <p className="notice">{result}</p>}
      {receiving.status !== "cancelled" && (
        <p className="notice">Registro de conferência: este recebimento não altera o estoque no ERP.</p>
      )}
      {feedback && <p className="notice ok" role="status">{feedback}</p>}
      {error && <ErrorBox message={error} />}

      {editable && !product && !editing && <ProductPicker onChoose={choose} />}
      {editable && product && (
        <ItemFormCard key={product.id} base={base} receivedOn={localDay(receiving.received_at)} units={units} product={product}
          onDone={saved} onCancel={() => setProduct(null)} />
      )}
      {editable && editing && (
        <ItemFormCard key={editing.id} base={base} receivedOn={localDay(receiving.received_at)} units={units} item={editing}
          onDone={saved} onCancel={() => setEditing(null)} />
      )}

      <section className="stack">
        <h2>Itens</h2>
        {receiving.items.length === 0 ? (
          <p className="empty">Nenhum item lançado ainda.</p>
        ) : (
          <ul className="plain card">
            {receiving.items.map((item) => (
              <ItemLine key={item.id} item={item} unitCode={unitCode(item.unit_id)} editable={editable && !product && !editing}
                onEdit={() => { setFeedback(null); setEditing(item); }} onRemove={() => remove(item)} />
            ))}
          </ul>
        )}
      </section>

      {isDraft && canManage(user) && !product && !editing && (
        <>
          {receiving.items.length > 0 && <FinalizePanel base={base} items={receiving.items} onDone={reload} />}
          <CancelPanel base={base} onDone={reload} />
        </>
      )}
      {isDraft && !canManage(user) && <p className="notice">Finalizar ou cancelar é com um gerente ou administrador.</p>}
    </div>
  );
}

export function ReceivingPage() {
  const user = useUser();
  const { receivingId } = useParams();
  const base = `/receivings/${receivingId}`;
  const receiving = useLoad(() => api<ReceivingDetail>(base), [base]);
  const units = useLoad(() => api<Unit[]>("/units"), []);
  const locations = useLocations(user.id);

  return (
    <Shell title="Recebimento" subtitle={receiving.data?.supplier_name ?? ""} back="/recebimentos">
      {units.error && <ErrorBox message={units.error} retry={units.reload} />}
      <Loaded state={receiving}>
        {(data) => {
          const location = locations.data?.find((l) => l.id === data.location_id);
          return (
            <ReceivingContent receiving={data} base={base} units={units.data ?? []} reload={receiving.reload}
              locationLabel={location ? `${location.storeName} · ${location.name}` : "Local"} />
          );
        }}
      </Loaded>
    </Shell>
  );
}
