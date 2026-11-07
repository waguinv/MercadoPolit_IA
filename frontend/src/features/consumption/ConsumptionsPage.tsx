import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import {
  canRegisterIn, CONSUMPTION_STATUS, DEPARTMENT_MODE,
  type Consumer, type Consumption, type ConsumptionConfig,
} from "../../lib/consumption";
import { newClientKey } from "../../lib/countQueue";
import { formatDate, formatQty, parseQty } from "../../lib/format";
import type { Location, Product, Unit } from "../../lib/types";
import { NoLocations, useLocations } from "../inventory/SessionsPage";
import { ProductPicker } from "../receiving/ReceivingPage";

type Loc = Location & { storeName: string };

function NewConsumptionForm({ config, locations, onCancel }: {
  config: ConsumptionConfig; locations: Loc[]; onCancel: () => void;
}) {
  const user = useUser();
  const navigate = useNavigate();
  const manager = canManage(user);
  const active = locations.filter((l) => l.is_active);
  const departments = config.departments.filter((d) => canRegisterIn(user.role, d));
  const purposes = config.purposes.filter((p) => p.is_active);
  const units = useLoad(() => api<Unit[]>("/units"), []);
  const consumers = useLoad(() => (manager ? api<Consumer[]>("/consumption-config/consumers") : Promise.resolve([])), [manager]);
  const key = useRef(newClientKey());
  const [locationId, setLocationId] = useState(active[0]?.id ?? "");
  const [departmentId, setDepartmentId] = useState(departments[0]?.id ?? "");
  const [purposeId, setPurposeId] = useState(purposes[0]?.id ?? "");
  const [product, setProduct] = useState<Product | null>(null);
  const [quantity, setQuantity] = useState("");
  const [consumerId, setConsumerId] = useState("");
  const [consumerName, setConsumerName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (active.length === 0) return <NoLocations onCancel={onCancel} />;
  if (departments.length === 0 || purposes.length === 0) {
    return (
      <div className="card stack">
        <p className="notice warn">
          {user.role === "admin"
            ? "Cadastre departamentos e finalidades em Configuração, na seção 5. Consumo interno."
            : manager
              ? "Nenhum departamento ou finalidade ativa. Peça a um administrador para cadastrar."
              : "Nenhum departamento aceita registro pelo próprio funcionário. Peça ao responsável para registrar."}
        </p>
        <div className="row">
          <button type="button" onClick={onCancel}>Voltar</button>
          {user.role === "admin" && (
            <button type="button" className="primary grow" onClick={() => navigate("/configuracao")}>Ir para Configuração</button>
          )}
        </div>
      </div>
    );
  }

  const unit = product ? units.data?.find((u) => u.id === product.unit_id) : undefined;
  const department = departments.find((d) => d.id === departmentId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!product) return;
    const qty = parseQty(quantity, 3);
    if (!qty || Number(qty) <= 0) {
      setError("Informe uma quantidade maior que zero.");
      return;
    }
    if (unit && !unit.allows_fraction && !Number.isInteger(Number(qty))) {
      setError(`A unidade ${unit.code} não aceita quantidade fracionada.`);
      return;
    }
    if (manager && !consumerId && !consumerName.trim()) {
      setError("Informe quem consumiu.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api<Consumption>("/internal-consumptions", {
        method: "POST",
        body: {
          client_request_id: key.current, location_id: locationId, department_id: departmentId,
          purpose_id: purposeId, product_id: product.id, quantity: qty, note: note.trim() || null,
          ...(manager ? (consumerId ? { consumer_user_id: consumerId } : { consumer_name: consumerName.trim() }) : {}),
        },
      });
      navigate(`/consumo/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Novo consumo interno</h2>
      <label>
        Local de estoque
        <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
          {active.map((l) => <option key={l.id} value={l.id}>{l.storeName} · {l.name}</option>)}
        </select>
      </label>
      <label>
        Departamento
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
          {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      {department && <p className="muted small">{DEPARTMENT_MODE[department.mode]}.</p>}
      <label>
        Finalidade
        <select value={purposeId} onChange={(e) => setPurposeId(e.target.value)}>
          {purposes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      {product ? (
        <div className="row between">
          <span><strong>{product.name}</strong>{unit && <span className="muted small"> · {unit.code}</span>}</span>
          <button type="button" className="link" onClick={() => setProduct(null)}>Trocar</button>
        </div>
      ) : <ProductPicker onChoose={setProduct} />}
      {product && (
        <label>
          Quantidade{unit ? ` (${unit.code})` : ""}
          <input inputMode="decimal" required value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </label>
      )}
      {manager ? (
        <>
          <label>
            Quem consumiu
            <select value={consumerId} onChange={(e) => setConsumerId(e.target.value)}>
              <option value="">Outra pessoa (digitar o nome)</option>
              {(consumers.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          {!consumerId && (
            <label>
              Nome de quem consumiu
              <input maxLength={200} value={consumerName} onChange={(e) => setConsumerName(e.target.value)} />
            </label>
          )}
        </>
      ) : <p className="muted small">Consumidor: você ({user.full_name}).</p>}
      <label>
        Observação (opcional)
        <input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <p className="muted small">O consumo aguarda aprovação do responsável. Nada é enviado ao ERP nesta versão.</p>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button type="button" onClick={onCancel}>Voltar</button>
        <button className="primary grow" disabled={busy || !product}>Registrar consumo</button>
      </div>
    </form>
  );
}

export function ConsumptionsPage() {
  const user = useUser();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState<string>("pending_approval");
  const config = useLoad(() => api<ConsumptionConfig>("/consumption-config"), []);
  const list = useLoad(() => api<Consumption[]>(`/internal-consumptions${filter ? `?status=${filter}` : ""}`), [filter]);
  const locations = useLocations(user.id);

  return (
    <Shell title="MercadoPilot" subtitle="Consumo interno">
      {user.role !== "auditor" && !creating && (
        <button className="primary block" onClick={() => setCreating(true)}>Novo consumo</button>
      )}
      {creating && (config.data && locations.data
        ? <NewConsumptionForm config={config.data} locations={locations.data} onCancel={() => setCreating(false)} />
        : <ErrorBox message={config.error ?? locations.error ?? "Carregando…"} />)}
      <label>
        Mostrar
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="pending_approval">Aguardando aprovação</option>
          <option value="needs_correction">Correção pedida</option>
          <option value="approved">Aprovados</option>
          <option value="rejected">Rejeitados</option>
          <option value="cancelled">Cancelados</option>
          <option value="">Todos</option>
        </select>
      </label>
      <Loaded state={list}>
        {(rows) => (
          <>
            <ul className="list">
              {rows.map((c) => (
                <li key={c.id}>
                  <Link to={`/consumo/${c.id}`} className="card link-card">
                    <div className="row between">
                      <strong>{c.product_name}</strong>
                      <Badge status={c.status} label={CONSUMPTION_STATUS[c.status]} />
                    </div>
                    <span>{formatQty(c.quantity)} {c.unit_code} · {c.department_name} · {c.purpose_name}</span>
                    <span className="muted small">{c.consumer_name} · {formatDate(c.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            {rows.length === 0 && <p className="empty">Nenhum consumo nesta situação.</p>}
          </>
        )}
      </Loaded>
    </Shell>
  );
}
