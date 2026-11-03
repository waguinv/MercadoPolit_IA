import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import simboloColorido from "../assets/brand/symbol_color.svg";
import simboloReverso from "../assets/brand/symbol_reverse.svg";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLE } from "../lib/format";

export function LogoSymbol({ size = 36, variant = "reversa" }: { size?: number; variant?: "reversa" | "escura" }) {
  // Regras da marca: sem distorcer (proporção original 510 × 470) e sem efeitos; versão reversa em fundo escuro.
  return (
    <img src={variant === "reversa" ? simboloReverso : simboloColorido} width={size}
      height={Math.round((size * 470) / 510)} alt="SysOrdem" className="logo-symbol" />
  );
}

export function Loading() {
  return (
    <div className="loading" role="status">
      <div className="loading__marca">
        <span className="loading__anel" aria-hidden />
        <LogoSymbol size={36} variant="escura" />
      </div>
      <p className="loading__titulo">Carregando…</p>
    </div>
  );
}

export function Shell({ title, subtitle, back, children }: {
  title: string; subtitle: string; back?: string; children: ReactNode;
}) {
  const { user, logout } = useAuth();
  return (
    <>
      <header className="header">
        {back && <Link to={back} className="header__back" aria-label="Voltar">←</Link>}
        {/* Abaixo de 180 px a marca usa só o símbolo (Manual da Marca) */}
        <LogoSymbol />
        <div className="header__text">
          <h1 className="header__loja">{title}</h1>
          <p className="header__sub">{subtitle}</p>
        </div>
        <button className="header__sair" onClick={logout} title={user ? `${user.full_name} (${ROLE[user.role]})` : undefined}>
          Sair
        </button>
      </header>
      <main className="content">{children}</main>
    </>
  );
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const stable = useCallback(load, deps);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await stable());
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [stable]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload };
}

/** Mostra carregando/erro; com dados na tela, um erro de recarga aparece sem esconder o conteúdo. */
export function Loaded<T>({ state, children }: {
  state: { data: T | null; error: string | null; reload: () => void | Promise<void> };
  children: (data: T) => ReactNode;
}) {
  if (state.data === null && state.error) return <ErrorBox message={state.error} retry={state.reload} />;
  if (state.data === null) return <Loading />;
  return (
    <>
      {state.error && <ErrorBox message={state.error} retry={state.reload} />}
      {children(state.data)}
    </>
  );
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void | Promise<void> }) {
  return (
    <div className="notice danger" role="alert">
      {message}
      {retry && <button className="link" onClick={() => void retry()}>Tentar de novo</button>}
    </div>
  );
}

const TONES: Record<string, string> = {
  open: "ok", counted: "ok", closed: "neutral", resolved: "ok",
  in_review: "warn", recount_requested: "warn", needs_review: "danger", cancelled: "neutral",
};

export function Badge({ status, label }: { status: string; label: string }) {
  return <span className={`badge ${TONES[status] ?? "neutral"}`}>{label}</span>;
}
