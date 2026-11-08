import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { useUser } from "../../lib/auth";
import { newClientKey } from "../../lib/countQueue";
import { formatDate, formatQty, parseQty } from "../../lib/format";
import { LOSS_REASON, LOSS_STATUS, type Loss } from "../../lib/losses";
import type { Location, Product, Unit } from "../../lib/types";
import { MovementTabs } from "../consumption/MovementTabs";
import { NoLocations, useLocations } from "../inventory/SessionsPage";
import { ProductPicker } from "../receiving/ReceivingPage";
import { LossFieldsForm, lossPayload, type LossFields } from "./LossForm";

type Loc = Location & { storeName: string };

function NewLossForm({ locations, onCancel }: { locations: Loc[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const active = locations.filter((l) => l.is_active);
  const units = useLoad(() => api<Unit[]>("/units"), []);
  const key = useRef(newClientKey());
  const [locationId, setLocationId] = useState(active[0]?.id ?? "");
  const [product, setProduct] = useState<Product | null>(null);
  const [fields, setFields] = useState<LossFields>({ reason: "damaged_package", quantity: "", lot: "", expiry: "", note: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (active.length === 0) return <NoLocations onCancel={onCancel} />;
  const unit = product ? units.data?.find((u) => u.id === product.unit_id) : undefined;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!product) return;
    const qty = parseQty(fields.quantity, 3);
    if (!qty || Number(qty) <= 0) {
      setError("Informe uma quantidade maior que zero.");
      return;
    }
    if (unit && !unit.allows_fraction && !Number.isInteger(Number(qty))) {
      setError(`A unidade ${unit.code} não aceita quantidade fracionada.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api<Loss>("/stock-losses", {
        method: "POST",
        body: { client_request_id: key.current, location_id: locationId, ...lossPayload(fields, product.id, qty) },
      });
      navigate(`/perdas/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Nova perda ou avaria</h2>
      <label>
        Local de estoque
        <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
          {active.map((l) => <option key={l.id} value={l.id}>{l.storeName} · {l.name}</option>)}
        </select>
      </label>
      {product ? (
        <div className="row between">
          <strong>{product.name}</strong>
          <button type="button" className="link" onClick={() => setProduct(null)}>Trocar</button>
        </div>
      ) : <ProductPicker onChoose={setProduct} />}
      {product && <LossFieldsForm fields={fields} set={setFields} unitCode={unit?.code} />}
      <p className="muted small">Toda perda aguarda aprovação do responsável. Nada é enviado ao ERP nesta versão.</p>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={onCancel}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy || !product}>Registrar perda</button>
      </div>
    </form>
  );
}

export function LossesPage() {
  const user = useUser();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("pending_approval");
  const list = useLoad(() => api<Loss[]>(`/stock-losses${filter ? `?status=${filter}` : ""}`), [filter]);
  const locations = useLocations(user.id);

  return (
    <Shell title="MercadoPilot" subtitle="Perdas e avarias">
      <MovementTabs />
      {user.role !== "auditor" && !creating && (
        <button className="primary block" onClick={() => setCreating(true)}>Nova perda</button>
      )}
      {creating && (locations.data
        ? <NewLossForm locations={locations.data} onCancel={() => setCreating(false)} />
        : <ErrorBox message={locations.error ?? "Carregando…"} />)}
      <label>
        Mostrar
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="pending_approval">Aguardando aprovação</option>
          <option value="needs_correction">Correção pedida</option>
          <option value="approved">Aprovadas</option>
          <option value="rejected">Rejeitadas</option>
          <option value="cancelled">Canceladas</option>
          <option value="">Todas</option>
        </select>
      </label>
      <Loaded state={list}>
        {(rows) => (
          <>
            <ul className="list">
              {rows.map((c) => (
                <li key={c.id}>
                  <Link to={`/perdas/${c.id}`} className="card link-card">
                    <div className="row between">
                      <strong>{c.product_name}</strong>
                      <Badge status={c.status} label={LOSS_STATUS[c.status]} />
                    </div>
                    <span>{formatQty(c.quantity)} {c.unit_code} · {LOSS_REASON[c.reason]}</span>
                    <span className="muted small">{c.registered_by_name} · {formatDate(c.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            {rows.length === 0 && <p className="empty">Nenhuma perda nesta situação.</p>}
          </>
        )}
      </Loaded>
    </Shell>
  );
}
