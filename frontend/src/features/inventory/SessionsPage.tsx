import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, cachedGet, errorMessage } from "../../lib/api";
import { canManage, useUser } from "../../lib/auth";
import { formatDate, SESSION_STATUS } from "../../lib/format";
import type { Location, Session, Store } from "../../lib/types";

export function useLocations(userId: string) {
  return useLoad(async () => {
    const { data: stores } = await cachedGet<Store[]>("/stores", userId);
    const lists = await Promise.all(
      stores.map((s) => cachedGet<Location[]>(`/stores/${s.id}/locations`, userId)),
    );
    const storeName = new Map(stores.map((s) => [s.id, s.name]));
    return lists.flatMap((l) => l.data).map((l) => ({ ...l, storeName: storeName.get(l.store_id) ?? "" }));
  }, [userId]);
}

/** Sem local ativo não há o que escolher: explica o caminho e sempre deixa voltar. */
export function NoLocations({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const admin = useUser().role === "admin";
  return (
    <div className="card stack">
      <p className="notice warn">
        {admin
          ? "Nenhum local de estoque ativo. Cadastre a loja e o local em Configuração, na seção 3."
          : "Nenhum local de estoque ativo. Peça a um administrador para cadastrar."}
      </p>
      <div className="row">
        <button className="voltar" type="button" onClick={onCancel}><span aria-hidden>←</span> Voltar</button>
        {admin && (
          <button type="button" className="primary grow" onClick={() => navigate("/configuracao")}>
            Ir para Configuração
          </button>
        )}
      </div>
    </div>
  );
}

function NewSessionForm({ locations, onCancel }: { locations: (Location & { storeName: string })[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const active = locations.filter((l) => l.is_active);
  const [locationId, setLocationId] = useState(active[0]?.id ?? "");
  const [name, setName] = useState("");
  const [sector, setSector] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (active.length === 0) return <NoLocations onCancel={onCancel} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await api<Session>("/inventory-sessions", {
        method: "POST", body: { location_id: locationId, name, sector: sector || null },
      });
      navigate(`/sessoes/${session.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Nova sessão</h2>
      <label>
        Nome
        <input required maxLength={200} value={name} placeholder="Ex.: Inventário de novembro" onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Local de estoque
        <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
          {active.map((l) => <option key={l.id} value={l.id}>{l.storeName} · {l.name}</option>)}
        </select>
      </label>
      <label>
        Setor (opcional)
        <input maxLength={100} value={sector} placeholder="Em branco = o local inteiro" onChange={(e) => setSector(e.target.value)} />
      </label>
      {error && <ErrorBox message={error} />}
      <div className="row">
        <button className="voltar" type="button" onClick={onCancel}><span aria-hidden>←</span> Voltar</button>
        <button className="primary grow" disabled={busy}>Criar sessão</button>
      </div>
    </form>
  );
}

export function SessionsPage() {
  const user = useUser();
  const [showAll, setShowAll] = useState(false);
  const [creating, setCreating] = useState(false);
  const sessions = useLoad(() => cachedGet<Session[]>("/inventory-sessions?limit=100", user.id), [user.id]);
  const locations = useLocations(user.id);
  const locationName = new Map((locations.data ?? []).map((l) => [l.id, `${l.storeName} · ${l.name}`]));

  return (
    <Shell title="MercadoPilot" subtitle="Inventários">
      {canManage(user) && !creating && <button className="primary block" onClick={() => setCreating(true)}>Nova sessão</button>}
      {creating && (locations.data
        ? <NewSessionForm locations={locations.data} onCancel={() => setCreating(false)} />
        : <ErrorBox message={locations.error ?? "Carregando locais…"} />)}
      <Loaded state={sessions}>
        {({ data, stale }) => {
          const visible = data.filter((s) => showAll || s.status === "open" || s.status === "in_review");
          return (
            <>
              {stale && <p className="notice warn">Sem conexão: mostrando a última lista salva.</p>}
              <ul className="list">
                {visible.map((s) => (
                  <li key={s.id}>
                    <Link to={`/sessoes/${s.id}`} className="card link-card">
                      <div className="row between">
                        <strong>{s.name}</strong>
                        <Badge status={s.status} label={SESSION_STATUS[s.status]} />
                      </div>
                      <span className="muted">
                        {locationName.get(s.location_id) ?? "Local"}{s.sector ? ` · ${s.sector}` : ""}
                      </span>
                      <span className="muted small">Criada em {formatDate(s.created_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
              {visible.length === 0 && (
                <p className="empty">{showAll ? "Nenhuma sessão ainda." : "Nenhuma sessão em andamento."}</p>
              )}
              <button className="link" onClick={() => setShowAll(!showAll)}>
                {showAll ? "Ver só as em andamento" : "Ver encerradas e canceladas"}
              </button>
            </>
          );
        }}
      </Loaded>
    </Shell>
  );
}
