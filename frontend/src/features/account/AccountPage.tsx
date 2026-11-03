import { useState, type FormEvent } from "react";

import { ErrorBox, Shell } from "../../components/ui";
import { api, errorMessage, setAccessToken } from "../../lib/api";
import { useUser } from "../../lib/auth";
import { ROLE } from "../../lib/format";

export function AccountPage() {
  const user = useUser();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setDone(false);
    if (next !== repeat) {
      setError("A confirmação não é igual à nova senha.");
      return;
    }
    setBusy(true);
    try {
      const { access_token } = await api<{ access_token: string }>("/auth/change-password", {
        method: "POST", body: { current_password: current, new_password: next },
      });
      setAccessToken(access_token);
      setCurrent("");
      setNext("");
      setRepeat("");
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Minha conta" subtitle={ROLE[user.role]}>
      <section className="card">
        <strong>{user.full_name}</strong>
        <span className="muted">{user.email}</span>
      </section>
      <form className="card stack" onSubmit={submit}>
        <h2>Trocar senha</h2>
        <label>
          Senha atual
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          Nova senha (mínimo de 12 caracteres)
          <input type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          Confirme a nova senha
          <input type="password" autoComplete="new-password" required minLength={12} value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </label>
        {error && <ErrorBox message={error} />}
        {done && <p className="notice ok" role="status">Senha trocada. Os outros aparelhos precisarão entrar de novo.</p>}
        <button className="primary" disabled={busy}>{busy ? "Trocando…" : "Trocar senha"}</button>
      </form>
    </Shell>
  );
}
