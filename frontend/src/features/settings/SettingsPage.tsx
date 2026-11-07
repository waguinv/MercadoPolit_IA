import { useState, type FormEvent } from "react";

import { Badge, ErrorBox, Loaded, Shell, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { useUser } from "../../lib/auth";
import { formatDate, ROLE, STORE_TIMEZONES } from "../../lib/format";
import type { ErpCheck, ErpConnection, ErpConnector, ErpLocation, Location, Role, Store, SyncResult, User } from "../../lib/types";

import { ConsumptionSection } from "./ConsumptionSection";

/** Executa uma ação e guarda o erro para mostrar na tela. */
function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { error, busy, run };
}

export function SettingsPage() {
  const erpLocations = useLoad(() => api<ErpLocation[]>("/erp/stock-locations").catch(() => null), []);
  return (
    <Shell title="MercadoPilot" subtitle="Configuração">
      <p className="muted small">Para começar, siga a ordem: conecte o ERP, sincronize o catálogo, cadastre lojas e locais e crie os usuários.</p>
      <ErpSection onChanged={erpLocations.reload} />
      <CatalogSection />
      <StoresSection erpLocations={erpLocations.data ?? null} />
      <UsersSection />
      <ConsumptionSection />
    </Shell>
  );
}

// ---------------- ERP ----------------
/** O ERP pode fazer a resposta esperar pelo limite de requisições: cabe no tempo do proxy (130 s). */
const ERP_CHECK_TIMEOUT_MS = 125_000;

function ErpSection({ onChanged }: { onChanged: () => void | Promise<void> }) {
  const state = useLoad(async () => ({
    conn: await api<ErpConnection>("/erp/connection"),
    connectors: await api<ErpConnector[]>("/erp/connectors"),
  }), []);
  const action = useAction();
  const [kind, setKind] = useState("");
  const [token, setToken] = useState("");
  const [checks, setChecks] = useState<ErpCheck[] | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const refresh = async () => {
    await state.reload();
    await onChanged();
  };

  return (
    <section className="card stack">
      <h2>1. Conexão com o ERP</h2>
      <Loaded state={state}>
        {({ conn, connectors }) => {
          const simulated = conn.adapter === "mock";
          const selected = connectors.find((c) => c.kind === (kind || conn.kind)) ?? connectors[0];
          return (
            <>
              {simulated ? (
                <p className="notice warn">Este ambiente usa o <strong>ERP simulado</strong>, com dados fictícios. Nenhum dado real é lido.</p>
              ) : (
                <p className="notice">
                  {conn.configured
                    ? <>Conectado ao {connectors.find((c) => c.kind === conn.kind)?.name ?? conn.kind}. {conn.last_verified_at ? `Última verificação: ${formatDate(conn.last_verified_at)}.` : "Ainda não verificado."}</>
                    : "Cole o token abaixo. Ele é testado antes de ser salvo."}
                </p>
              )}
              {action.error && <ErrorBox message={action.error} />}
              {checks && !action.error && (
                <ul className="plain" role="status">
                  <li className="notice ok">Token aceito.</li>
                  {checks.map((c) => (
                    <li key={c.name} className={c.ok ? "notice ok" : "notice warn"}>{c.name}: {c.detail}</li>
                  ))}
                </ul>
              )}
              {!simulated && selected && (
                <form className="stack" onSubmit={(e: FormEvent) => {
                  e.preventDefault();
                  setChecks(null);
                  void action.run(async () => {
                    const saved = await api<ErpConnection>("/erp/connection", {
                      method: "PUT", body: { kind: selected.kind, token }, timeoutMs: ERP_CHECK_TIMEOUT_MS,
                    });
                    setToken("");
                    setChecks(saved.checks);
                    await refresh();
                  });
                }}>
                  {connectors.length > 1 && (
                    <label>
                      Sistema
                      <select value={selected.kind} onChange={(e) => setKind(e.target.value)}>
                        {connectors.map((c) => <option key={c.kind} value={c.kind}>{c.name}</option>)}
                      </select>
                    </label>
                  )}
                  <label>
                    {conn.configured ? `Substituir: ${selected.token_label}` : selected.token_label}
                    <input type="password" autoComplete="off" required minLength={8} maxLength={512} value={token} onChange={(e) => setToken(e.target.value)} />
                  </label>
                  <p className="muted small">{selected.token_help} O token é guardado cifrado e nunca é mostrado de novo.</p>
                  <button className="primary" disabled={action.busy}>{action.busy ? "Testando…" : "Testar e conectar"}</button>
                </form>
              )}
              {(simulated || conn.configured) && (
                <button disabled={action.busy} onClick={() => {
                  setChecks(null);
                  void action.run(async () => {
                    const tested = await api<ErpConnection>("/erp/connection/test", { method: "POST", timeoutMs: ERP_CHECK_TIMEOUT_MS });
                    setChecks(tested.checks);
                    await refresh();
                  });
                }}>Testar conexão</button>
              )}
              {!simulated && conn.configured && !confirmRemove && (
                <button className="link danger-text" onClick={() => setConfirmRemove(true)}>Remover conexão</button>
              )}
              {confirmRemove && (
                <div className="row">
                  <button onClick={() => setConfirmRemove(false)}>Voltar</button>
                  <button className="danger grow" disabled={action.busy} onClick={() => void action.run(async () => {
                    await api("/erp/connection", { method: "DELETE" });
                    setConfirmRemove(false);
                    setChecks(null);
                    await refresh();
                  })}>Confirmar remoção</button>
                </div>
              )}
            </>
          );
        }}
      </Loaded>
    </section>
  );
}

// ---------------- Catálogo ----------------
function CatalogSection() {
  const action = useAction();
  const [result, setResult] = useState<SyncResult | null>(null);
  return (
    <section className="card stack">
      <h2>2. Catálogo</h2>
      <p className="muted small">Copia unidades e produtos do ERP. Só lê o ERP; o que sumiu de lá é desativado aqui, nunca apagado.</p>
      {action.error && <ErrorBox message={action.error} />}
      {result && (
        <div className="notice ok" role="status">
          Produtos: {result.products_created} novos, {result.products_updated} atualizados, {result.products_unchanged} sem mudança,
          {" "}{result.products_deactivated} desativados. Unidades: {result.units_created} novas, {result.units_updated} atualizadas.
        </div>
      )}
      {result && result.products_skipped > 0 && (
        <p className="notice warn">{result.products_skipped} produto(s) ignorado(s) por estarem sem unidade válida no ERP.</p>
      )}
      <button className="primary" disabled={action.busy} onClick={() => {
        setResult(null);
        // Catálogos grandes demoram por causa do limite de requisições do ERP.
        void action.run(async () => setResult(await api<SyncResult>("/catalog/sync", { method: "POST", timeoutMs: 600_000 })));
      }}>{action.busy ? "Sincronizando…" : "Sincronizar catálogo"}</button>
    </section>
  );
}

// ---------------- Lojas e locais ----------------
function StoresSection({ erpLocations }: { erpLocations: ErpLocation[] | null }) {
  const stores = useLoad(() => api<Store[]>("/stores"), []);
  const action = useAction();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE);

  return (
    <section className="card stack">
      <h2>3. Lojas e locais de estoque</h2>
      <Loaded state={stores}>
        {(rows) => (
          <>
            {rows.length === 0 && <p className="empty">Nenhuma loja ainda.</p>}
            {rows.map((s) => <StoreBlock key={s.id} store={s} erpLocations={erpLocations} onChanged={stores.reload} />)}
          </>
        )}
      </Loaded>
      <form className="stack" onSubmit={(e: FormEvent) => {
        e.preventDefault();
        void action.run(async () => {
          await api("/stores", { method: "POST", body: { code, name, timezone } });
          setCode("");
          setName("");
          setTimezone(DEFAULT_TIMEZONE);
          await stores.reload();
        });
      }}>
        <div className="row">
          <label>
            Código
            <input required maxLength={50} value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <label className="grow">
            Nome da loja
            <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        </div>
        <TimezoneField value={timezone} onChange={setTimezone} />
        {action.error && <ErrorBox message={action.error} />}
        <button disabled={action.busy}>Adicionar loja</button>
      </form>
    </section>
  );
}

const DEFAULT_TIMEZONE = "America/Sao_Paulo";

function TimezoneField({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label>
      Fuso da loja
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {Object.entries(STORE_TIMEZONES).map(([zone, label]) => <option key={zone} value={zone}>{label}</option>)}
      </select>
    </label>
  );
}

/** Código do local no ERP: escolhe da lista quando o ERP responde; senão, digita. */
function ErpCodeField({ value, onChange, erpLocations }: {
  value: string; onChange: (v: string) => void; erpLocations: ErpLocation[] | null;
}) {
  if (!erpLocations) {
    return (
      <label>
        Código no ERP
        <input maxLength={100} value={value} onChange={(e) => onChange(e.target.value)} />
      </label>
    );
  }
  const known = erpLocations.some((l) => l.code === value);
  return (
    <label>
      Local no ERP
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Sem mapeamento</option>
        {value && !known && <option value={value}>{value} (não encontrado no ERP)</option>}
        {erpLocations.map((l) => <option key={l.code} value={l.code}>{l.name} ({l.code})</option>)}
      </select>
    </label>
  );
}

function StoreBlock({ store, erpLocations, onChanged }: {
  store: Store; erpLocations: ErpLocation[] | null; onChanged: () => void | Promise<void>;
}) {
  const base = `/stores/${store.id}/locations`;
  const locations = useLoad(() => api<Location[]>(base), [base]);
  const action = useAction();
  const zoneAction = useAction();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [external, setExternal] = useState("");
  const [adding, setAdding] = useState(false);

  return (
    <div className="stack subcard">
      <strong>{store.name} <span className="muted small">({store.code})</span></strong>
      <TimezoneField value={store.timezone} disabled={zoneAction.busy} onChange={(zone) => void zoneAction.run(async () => {
        await api(`/stores/${store.id}`, { method: "PATCH", body: { timezone: zone } });
        await onChanged();
      })} />
      <p className="muted small">Define o dia do recebimento, usado para conferir a validade dos produtos.</p>
      {zoneAction.error && <ErrorBox message={zoneAction.error} />}
      <Loaded state={locations}>
        {(rows) => (
          <ul className="plain">
            {rows.length === 0 && <li className="muted small">Nenhum local. Sem local não é possível criar inventário.</li>}
            {rows.map((l) => <LocationRow key={l.id} location={l} erpLocations={erpLocations} onChanged={locations.reload} />)}
          </ul>
        )}
      </Loaded>
      {action.error && <ErrorBox message={action.error} />}
      {adding ? (
        <form className="stack" onSubmit={(e: FormEvent) => {
          e.preventDefault();
          void action.run(async () => {
            await api(base, { method: "POST", body: { code, name, external_code: external || null } });
            setCode("");
            setName("");
            setExternal("");
            setAdding(false);
            await locations.reload();
          });
        }}>
          <div className="row">
            <label>
              Código
              <input required autoFocus maxLength={50} value={code} onChange={(e) => setCode(e.target.value)} />
            </label>
            <label className="grow">
              Nome do local
              <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          </div>
          <ErpCodeField value={external} onChange={setExternal} erpLocations={erpLocations} />
          <div className="row">
            <button type="button" onClick={() => setAdding(false)}>Voltar</button>
            <button className="primary grow" disabled={action.busy}>Salvar local</button>
          </div>
        </form>
      ) : <button onClick={() => setAdding(true)}>Adicionar local</button>}
    </div>
  );
}

function LocationRow({ location, erpLocations, onChanged }: {
  location: Location; erpLocations: ErpLocation[] | null; onChanged: () => void | Promise<void>;
}) {
  const action = useAction();
  const update = (body: object) => void action.run(async () => {
    await api(`/locations/${location.id}`, { method: "PATCH", body });
    await onChanged();
  });
  return (
    <li className="line stack">
      <div className="row between">
        <span>{location.name} <span className="muted small">({location.code})</span></span>
        <Badge status={location.is_active ? "open" : "cancelled"} label={location.is_active ? "Ativo" : "Inativo"} />
      </div>
      <ErpCodeField value={location.external_code ?? ""} erpLocations={erpLocations}
        onChange={(v) => update({ external_code: v || null })} />
      {action.error && <ErrorBox message={action.error} />}
      <button className="link" disabled={action.busy} onClick={() => update({ is_active: !location.is_active })}>
        {location.is_active ? "Desativar local" : "Reativar local"}
      </button>
    </li>
  );
}

// ---------------- Usuários ----------------
const ROLES: Role[] = ["operator", "manager", "auditor", "admin"];

function UsersSection() {
  const state = useLoad(async () => ({
    users: await api<User[]>("/users"),
    stores: await api<Store[]>("/stores"),
  }), []);
  const action = useAction();
  const [form, setForm] = useState({ full_name: "", email: "", password: "", role: "operator" as Role, store_id: "" });
  const [adding, setAdding] = useState(false);
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [field]: e.target.value });

  return (
    <section className="card stack">
      <h2>4. Usuários</h2>
      <Loaded state={state}>
        {({ users, stores }) => (
          <>
            <ul className="plain">
              {users.map((u) => <UserRow key={u.id} target={u} stores={stores} onChanged={state.reload} />)}
            </ul>
            {action.error && <ErrorBox message={action.error} />}
            {adding ? (
              <form className="stack" onSubmit={(e: FormEvent) => {
                e.preventDefault();
                void action.run(async () => {
                  await api("/users", { method: "POST", body: { ...form, store_id: form.store_id || null } });
                  setForm({ full_name: "", email: "", password: "", role: "operator", store_id: "" });
                  setAdding(false);
                  await state.reload();
                });
              }}>
                <label>
                  Nome completo
                  <input required autoFocus maxLength={200} value={form.full_name} onChange={set("full_name")} />
                </label>
                <label>
                  E-mail
                  <input type="email" required autoComplete="off" value={form.email} onChange={set("email")} />
                </label>
                <label>
                  Senha inicial (mínimo de 12 caracteres)
                  <input type="password" required minLength={12} autoComplete="new-password" value={form.password} onChange={set("password")} />
                </label>
                <p className="muted small">Passe a senha à pessoa e peça que troque em "Minha conta".</p>
                <div className="row">
                  <label className="grow">
                    Papel
                    <select value={form.role} onChange={set("role")}>
                      {ROLES.map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}
                    </select>
                  </label>
                  <label className="grow">
                    Loja
                    <select value={form.store_id} onChange={set("store_id")}>
                      <option value="">Todas as lojas</option>
                      {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  </label>
                </div>
                <div className="row">
                  <button type="button" onClick={() => setAdding(false)}>Voltar</button>
                  <button className="primary grow" disabled={action.busy}>Criar usuário</button>
                </div>
              </form>
            ) : <button onClick={() => setAdding(true)}>Adicionar usuário</button>}
          </>
        )}
      </Loaded>
    </section>
  );
}

function UserRow({ target, stores, onChanged }: { target: User; stores: Store[]; onChanged: () => void | Promise<void> }) {
  const me = useUser();
  const action = useAction();
  const [resetting, setResetting] = useState(false);
  const [password, setPassword] = useState("");
  const [saved, setSaved] = useState(false);
  const store = stores.find((s) => s.id === target.store_id)?.name ?? "Todas as lojas";

  return (
    <li className="line stack">
      <div className="row between">
        <span>{target.full_name}<br /><span className="muted small">{target.email}</span></span>
        <Badge status={target.is_active ? "open" : "cancelled"} label={target.is_active ? "Ativo" : "Inativo"} />
      </div>
      <span className="muted small">{ROLE[target.role]} · {store}</span>
      {action.error && <ErrorBox message={action.error} />}
      {saved && <p className="notice ok" role="status">Senha redefinida. Os aparelhos dessa pessoa foram desconectados.</p>}
      {resetting ? (
        <form className="stack" onSubmit={(e: FormEvent) => {
          e.preventDefault();
          void action.run(async () => {
            await api(`/users/${target.id}`, { method: "PATCH", body: { password } });
            setPassword("");
            setResetting(false);
            setSaved(true);
          });
        }}>
          <label>
            Nova senha (mínimo de 12 caracteres)
            <input type="password" required autoFocus minLength={12} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <div className="row">
            <button type="button" onClick={() => setResetting(false)}>Voltar</button>
            <button className="primary grow" disabled={action.busy}>Salvar senha</button>
          </div>
        </form>
      ) : target.id !== me.id && (
        <div className="row between">
          <button className="link" onClick={() => {
            setSaved(false);
            setResetting(true);
          }}>Redefinir senha</button>
          <button className={target.is_active ? "link danger-text" : "link"} disabled={action.busy} onClick={() => void action.run(async () => {
            await api(`/users/${target.id}`, { method: "PATCH", body: { is_active: !target.is_active } });
            await onChanged();
          })}>{target.is_active ? "Desativar" : "Reativar"}</button>
        </div>
      )}
    </li>
  );
}
