import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canCount, useUser } from "../../lib/auth";
import { formatDate } from "../../lib/format";
import { RECEIVING_STATUS, RequestKey, validateHeader } from "../../lib/receiving";
import type { Location, Receiving } from "../../lib/types";
import { NoLocations, useLocations } from "../inventory/SessionsPage";

function NewReceivingForm({ locations, onCancel }: { locations: (Location & { storeName: string })[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const active = locations.filter((l) => l.is_active);
  const requestKey = useRef(new RequestKey());
  const [locationId, setLocationId] = useState(active[0]?.id ?? "");
  const [supplierName, setSupplierName] = useState("");
  const [supplierDocument, setSupplierDocument] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [documentSeries, setDocumentSeries] = useState("");
  const [accessKey, setAccessKey] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (active.length === 0) return <NoLocations onCancel={onCancel} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const location = active.find((l) => l.id === locationId);
    const result = validateHeader({ supplierName, supplierDocument, documentNumber, documentSeries, accessKey, note });
    if ("error" in result) {
      setError(result.error);
      return;
    }
    if (!location) return;
    const body = { ...result.payload, store_id: location.store_id, location_id: location.id };
    setBusy(true);
    setError(null);
    try {
      const created = await api<Receiving>("/receivings", {
        method: "POST", body: { ...body, client_request_id: requestKey.current.for(body) },
      });
      navigate(`/recebimentos/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Novo recebimento</h2>
      <label>
        Local de estoque
        <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
          {active.map((l) => <option key={l.id} value={l.id}>{l.storeName} · {l.name}</option>)}
        </select>
      </label>
      <label>
        Fornecedor
        <input required maxLength={200} value={supplierName} onChange={(e) => setSupplierName(e.target.value)} />
      </label>
      <label>
        CPF ou CNPJ do fornecedor (opcional)
        <input inputMode="numeric" autoComplete="off" value={supplierDocument} onChange={(e) => setSupplierDocument(e.target.value)} />
      </label>
      <div className="fields">
        <label>
          Número da nota (opcional)
          <input maxLength={20} autoComplete="off" value={documentNumber} onChange={(e) => setDocumentNumber(e.target.value)} />
        </label>
        <label>
          Série (opcional)
          <input maxLength={10} autoComplete="off" value={documentSeries} onChange={(e) => setDocumentSeries(e.target.value)} />
        </label>
      </div>
      <label>
        Chave de acesso da NF-e (opcional)
        <input inputMode="numeric" autoComplete="off" placeholder="44 dígitos" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} />
      </label>
      <label>
        Observação (opcional)
        <input maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={onCancel}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy}>Criar recebimento</button>
      </div>
    </form>
  );
}

export function ReceivingsPage() {
  const user = useUser();
  const [showAll, setShowAll] = useState(false);
  const [creating, setCreating] = useState(false);
  const receivings = useLoad(() => api<Receiving[]>("/receivings"), []);
  const locations = useLocations(user.id);
  const locationName = new Map((locations.data ?? []).map((l) => [l.id, `${l.storeName} · ${l.name}`]));

  return (
    <Shell title="MercadoPilot" subtitle="Recebimentos">
      {canCount(user) && !creating && <button className="primary block" onClick={() => setCreating(true)}>Novo recebimento</button>}
      {creating && (locations.data
        ? <NewReceivingForm locations={locations.data} onCancel={() => setCreating(false)} />
        : <ErrorBox message={locations.error ?? "Carregando locais…"} />)}
      <Loaded state={receivings}>
        {(data) => {
          const visible = data.filter((r) => showAll || r.status === "draft");
          return (
            <>
              <ul className="list">
                {visible.map((r) => (
                  <li key={r.id}>
                    <Link to={`/recebimentos/${r.id}`} className="card link-card">
                      <div className="row between">
                        <strong>{r.supplier_name}</strong>
                        <Badge status={r.status} label={RECEIVING_STATUS[r.status]} />
                      </div>
                      <span className="muted">
                        {locationName.get(r.location_id) ?? "Local"}{r.document_number ? ` · Nota ${r.document_number}` : ""}
                      </span>
                      <span className="muted small">Recebido em {formatDate(r.received_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
              {visible.length === 0 && (
                <p className="empty">{showAll ? "Nenhum recebimento ainda." : "Nenhum recebimento em rascunho."}</p>
              )}
              <button className="link" onClick={() => setShowAll(!showAll)}>
                {showAll ? "Ver só os rascunhos" : "Ver finalizados e cancelados"}
              </button>
            </>
          );
        }}
      </Loaded>
    </Shell>
  );
}
