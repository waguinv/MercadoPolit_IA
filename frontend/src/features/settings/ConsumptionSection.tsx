import { useState, type FormEvent } from "react";

import { ErrorBox, Loaded, useLoad } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { DEPARTMENT_MODE, type ConsumptionConfig, type DepartmentMode } from "../../lib/consumption";

/** Seção 5 da Configuração: departamentos, finalidades e segregação do consumo interno. */
export function ConsumptionSection() {
  const state = useLoad(() => api<ConsumptionConfig>("/consumption-config"), []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [depName, setDepName] = useState("");
  const [depMode, setDepMode] = useState<DepartmentMode>("centralized");
  const [purName, setPurName] = useState("");

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await state.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h2>5. Consumo interno</h2>
      <Loaded state={state}>
        {(cfg) => (
          <>
            <label className="row">
              <input type="checkbox" checked={cfg.segregation} disabled={busy}
                onChange={(e) => void run(() => api("/consumption-config/settings", {
                  method: "PUT", body: { segregation: e.target.checked } }))} />
              <span>Segregação no consumo e nas perdas: quem registrou ou consumiu não pode aprovar (recomendado)</span>
            </label>
            {cfg.departments.length === 0 && cfg.purposes.length === 0 && (
              <button disabled={busy} onClick={() => void run(() => api("/consumption-config/defaults", { method: "POST" }))}>
                Usar a lista sugerida de departamentos e finalidades
              </button>
            )}

            <h3>Departamentos</h3>
            <ul className="plain">
              {cfg.departments.map((d) => (
                <li key={d.id} className="row between line">
                  <span className={d.is_active ? "" : "muted"}>{d.name}</span>
                  <span className="row">
                    <select value={d.mode} disabled={busy} aria-label={`Modo de ${d.name}`}
                      onChange={(e) => void run(() => api(`/consumption-config/departments/${d.id}`, {
                        method: "PATCH", body: { mode: e.target.value } }))}>
                      <option value="centralized">Centralizado</option>
                      <option value="self_service">Autoatendimento</option>
                    </select>
                    <button className="link" disabled={busy}
                      onClick={() => void run(() => api(`/consumption-config/departments/${d.id}`, {
                        method: "PATCH", body: { is_active: !d.is_active } }))}>
                      {d.is_active ? "Desativar" : "Ativar"}
                    </button>
                  </span>
                </li>
              ))}
            </ul>
            <form className="stack" onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void run(async () => {
                await api("/consumption-config/departments", { method: "POST", body: { name: depName.trim(), mode: depMode } });
                setDepName("");
              });
            }}>
              <div className="row">
                <label className="grow">
                  Novo departamento
                  <input required minLength={2} maxLength={100} value={depName} onChange={(e) => setDepName(e.target.value)} />
                </label>
                <label className="grow">
                  Modo
                  <select value={depMode} onChange={(e) => setDepMode(e.target.value as DepartmentMode)}>
                    <option value="centralized">Centralizado</option>
                    <option value="self_service">Autoatendimento</option>
                  </select>
                </label>
              </div>
              <p className="muted small">{DEPARTMENT_MODE[depMode]}.</p>
              <button disabled={busy}>Adicionar departamento</button>
            </form>

            <h3>Finalidades</h3>
            <ul className="plain">
              {cfg.purposes.map((p) => (
                <li key={p.id} className="row between line">
                  <span className={p.is_active ? "" : "muted"}>{p.name}</span>
                  <button className="link" disabled={busy}
                    onClick={() => void run(() => api(`/consumption-config/purposes/${p.id}`, {
                      method: "PATCH", body: { is_active: !p.is_active } }))}>
                    {p.is_active ? "Desativar" : "Ativar"}
                  </button>
                </li>
              ))}
            </ul>
            <form className="row" onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void run(async () => {
                await api("/consumption-config/purposes", { method: "POST", body: { name: purName.trim() } });
                setPurName("");
              });
            }}>
              <label className="grow">
                Nova finalidade
                <input required minLength={2} maxLength={100} value={purName} onChange={(e) => setPurName(e.target.value)} />
              </label>
              <button disabled={busy}>Adicionar</button>
            </form>
            {error && <ErrorBox message={error} />}
          </>
        )}
      </Loaded>
    </section>
  );
}
