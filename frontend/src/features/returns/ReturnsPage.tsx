import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { useUser } from "../../lib/auth";
import { newClientKey } from "../../lib/countQueue";
import { formatDate, formatQty, parseQty } from "../../lib/format";
import { RETURN_MODALITY, RETURN_STATUS, type SupplierReturn } from "../../lib/returns";
import type { Location, Product, Unit } from "../../lib/types";
import { MovementTabs } from "../consumption/MovementTabs";
import { NoLocations, useLocations } from "../inventory/SessionsPage";
import { ProductPicker } from "../receiving/ReceivingPage";
import { emptyReturnFields, ReturnFieldsForm, returnPayload, type ReturnFields } from "./ReturnForm";

type Loc = Location & { storeName: string };

function NewReturnForm({ locations, onCancel }: { locations: Loc[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const active = locations.filter((l) => l.is_active);
  const units = useLoad(() => api<Unit[]>("/units"), []);
  const key = useRef(newClientKey());
  const [locationId, setLocationId] = useState(active[0]?.id ?? "");
  const [product, setProduct] = useState<Product | null>(null);
  const [fields, setFields] = useState<ReturnFields>(emptyReturnFields);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (active.length === 0) return <NoLocations onCancel={onCancel} />;
  const unit = product ? units.data?.find((u) => u.id === product.unit_id) : undefined;
  const storeId = active.find((l) => l.id === locationId)?.store_id ?? "";

  function chooseLocation(id: string) {
    setLocationId(id);
    setFields((f) => ({ ...f, receivingId: "" })); // a nota de origem é de uma loja só
  }

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
      const created = await api<SupplierReturn>("/supplier-returns", {
        method: "POST",
        body: { client_request_id: key.current, location_id: locationId, ...returnPayload(fields, product.id, qty) },
      });
      navigate(`/devolucoes/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Nova devolução ou troca</h2>
      <label>
        Local de estoque
        <select value={locationId} onChange={(e) => chooseLocation(e.target.value)}>
          {active.map((l) => <option key={l.id} value={l.id}>{l.storeName} · {l.name}</option>)}
        </select>
      </label>
      {product ? (
        <div className="row between">
          <strong>{product.name}</strong>
          <button type="button" className="link" onClick={() => setProduct(null)}>Trocar</button>
        </div>
      ) : <ProductPicker onChoose={setProduct} />}
      {product && <ReturnFieldsForm fields={fields} set={setFields} storeId={storeId} unitCode={unit?.code} />}
      <p className="muted small">Toda devolução aguarda aprovação do responsável. Nada é enviado ao ERP e nenhum documento fiscal é emitido nesta versão.</p>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={onCancel}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy || !product}>Registrar devolução</button>
      </div>
    </form>
  );
}

export function ReturnsPage() {
  const user = useUser();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("pending_approval");
  const list = useLoad(() => api<SupplierReturn[]>(`/supplier-returns${filter ? `?status=${filter}` : ""}`), [filter]);
  const locations = useLocations(user.id);

  return (
    <Shell title="MercadoPilot" subtitle="Devoluções e trocas">
      <MovementTabs />
      {user.role !== "auditor" && !creating && (
        <button className="primary block" onClick={() => setCreating(true)}>Nova devolução</button>
      )}
      {creating && (locations.data
        ? <NewReturnForm locations={locations.data} onCancel={() => setCreating(false)} />
        : <ErrorBox message={locations.error ?? "Carregando…"} />)}
      <label>
        Mostrar
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="pending_approval">Aguardando aprovação</option>
          <option value="needs_correction">Correção pedida</option>
          <option value="approved">Aguardando coleta</option>
          <option value="picked_up">Coletadas</option>
          <option value="completed">Concluídas</option>
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
                  <Link to={`/devolucoes/${c.id}`} className="card link-card">
                    <div className="row between">
                      <strong>{c.product_name}</strong>
                      <Badge status={c.status} label={RETURN_STATUS[c.status]} />
                    </div>
                    <span>{formatQty(c.quantity)} {c.unit_code} · {RETURN_MODALITY[c.modality]}</span>
                    <span className="muted small">{c.supplier_name} · {formatDate(c.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            {rows.length === 0 && <p className="empty">Nenhuma devolução nesta situação.</p>}
          </>
        )}
      </Loaded>
    </Shell>
  );
}
