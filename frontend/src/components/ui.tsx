import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLE } from "../lib/format";

export function Shell({ title, back, children }: { title: string; back?: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <>
      <header className="topbar">
        {back ? <Link to={back} className="back" aria-label="Voltar">←</Link> : <span className="logo" aria-hidden>M</span>}
        <h1>{title}</h1>
        <button className="link on-dark" onClick={logout} title={user ? `${user.full_name} (${ROLE[user.role]})` : undefined}>
          Sair
        </button>
      </header>
      <main className="page">{children}</main>
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
  if (state.data === null) return <p className="muted" role="status">Carregando…</p>;
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
