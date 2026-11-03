import { useState, type FormEvent } from "react";

import logoVertical from "../../assets/brand/vertical_reverse.svg";
import { errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export function LoginPage() {
  const { login, expired } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
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
    <div className="login">
      <div className="login__brand">
        {/* Versão vertical reversa: a partir de 180 px de largura (Manual da Marca) */}
        <img src={logoVertical} width={220} height={154} alt="SysOrdem Soluções Digitais" className="login__logo" />
        <h1 className="login__name">MercadoPilot</h1>
      </div>
      {expired && (
        <p className="notice warn login__notice" role="status">
          Sua sessão expirou. Entre de novo: as contagens guardadas neste aparelho continuam salvas e serão enviadas.
        </p>
      )}
      <form onSubmit={submit} className="login__card">
        <label>
          E-mail
          <input type="email" inputMode="email" autoComplete="username" required value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Senha
          <span className="pwd">
            <input type={show ? "text" : "password"} autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="link" aria-pressed={show} onClick={() => setShow(!show)}>
              {show ? "Ocultar" : "Mostrar"}
            </button>
          </span>
        </label>
        {error && <p className="notice danger" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
        <p className="login__slogan">MAIS CONTROLE. MAIS RESULTADOS.</p>
      </form>
    </div>
  );
}
