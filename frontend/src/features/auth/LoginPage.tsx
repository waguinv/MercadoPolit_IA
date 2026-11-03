import { useState, type FormEvent } from "react";

import { errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export function LoginPage() {
  const { login, expired } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1 className="brand">MercadoPilot</h1>
      <p className="muted">Operação do supermercado, na palma da mão.</p>
      {expired && (
        <p className="notice warn" role="status">
          Sua sessão expirou. Entre de novo: as contagens guardadas neste aparelho continuam salvas e serão enviadas.
        </p>
      )}
      <form onSubmit={submit} className="stack">
        <label>
          E-mail
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Senha
          <input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="notice danger" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
      </form>
    </main>
  );
}
