import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { ErrorBox } from "../../components/ui";
import { api, ApiError, cachedGet, errorMessage, NetworkError } from "../../lib/api";
import { useUser } from "../../lib/auth";
import { CountQueue, newClientKey, type QueuedCount, type SendResult } from "../../lib/countQueue";
import { formatDate, formatQty, isFractional, parseQty } from "../../lib/format";
import type { MyCount, PendingRecount, Product, SessionDetail, Unit } from "../../lib/types";

const RETRY_MS = 30_000;

function productCacheKey(userId: string) {
  return `mp.products.${userId}`;
}

function readProducts(userId: string): Product[] {
  try {
    return JSON.parse(localStorage.getItem(productCacheKey(userId)) ?? "[]") as Product[];
  } catch {
    return [];
  }
}

function rememberProducts(userId: string, found: Product[]): void {
  const merged = new Map([...found, ...readProducts(userId)].map((p) => [p.id, p]));
  try {
    localStorage.setItem(productCacheKey(userId), JSON.stringify([...merged.values()].slice(0, 500)));
  } catch {
    // Sem espaço: a busca sem rede só encontrará menos produtos.
  }
}

async function sendCount(c: QueuedCount): Promise<SendResult> {
  try {
    await api(`/inventory-sessions/${c.session_id}/counts`, {
      method: "POST",
      body: { product_id: c.product_id, unit_id: c.unit_id, quantity: c.quantity, client_key: c.client_key, note: c.note },
    });
    return "sent";
  } catch (error) {
    if (error instanceof NetworkError) return "retry_later";
    if (error instanceof ApiError) {
      if (error.status === 401) return "auth_expired";
      if (error.status === 408 || error.status === 429 || error.status >= 500) return "retry_later";
      return { rejected: error.message };
    }
    return "retry_later";
  }
}

export function CountTab({ session, onChanged }: { session: SessionDetail; onChanged: () => void | Promise<void> }) {
  const user = useUser();
  const queue = useMemo(() => new CountQueue(user.id), [user.id]);
  const base = `/inventory-sessions/${session.id}`;
  const active = session.status === "open" || session.status === "in_review";

  const [units, setUnits] = useState<Unit[]>([]);
  const [myCounts, setMyCounts] = useState<MyCount[]>([]);
  const [recounts, setRecounts] = useState<PendingRecount[]>([]);
  const [queued, setQueued] = useState<QueuedCount[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Product[] | null>(null);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  const [product, setProduct] = useState<Product | null>(null);
  const [unitId, setUnitId] = useState("");
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);

  const refreshQueue = useCallback(() => {
    setQueued(queue.all().filter((c) => c.session_id === session.id));
  }, [queue, session.id]);

  const refreshServer = useCallback(async () => {
    try {
      const [counts, pending] = await Promise.all([
        cachedGet<MyCount[]>(`${base}/my-counts`, user.id),
        active ? cachedGet<PendingRecount[]>(`${base}/pending-recounts`, user.id) : Promise.resolve({ data: [] }),
      ]);
      setMyCounts(counts.data);
      setRecounts(pending.data);
      setLoadError(null);
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, [base, user.id, active]);

  const flush = useCallback(async () => {
    if (queue.all().every((c) => c.state !== "pending")) {
      refreshQueue();
      return;
    }
    setSending(true);
    const outcome = await queue.flush(sendCount);
    setSending(false);
    refreshQueue();
    if (outcome === "done") {
      await refreshServer();
      await onChanged();
    }
  }, [queue, refreshQueue, refreshServer, onChanged]);

  useEffect(() => {
    refreshQueue();
    void refreshServer();
    void flush();
    cachedGet<Unit[]>("/units", user.id).then((r) => setUnits(r.data)).catch((e) => setLoadError(errorMessage(e)));
    window.addEventListener("online", flush);
    const timer = window.setInterval(flush, RETRY_MS);
    return () => {
      window.removeEventListener("online", flush);
      window.clearInterval(timer);
    };
  }, [session.id, user.id, flush, refreshQueue, refreshServer]);

  useEffect(() => {
    const term = search.trim();
    setSearchNote(null);
    if (term.length < 2) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const looksLikeBarcode = /^\d{8,14}$/.test(term);
      try {
        const params = new URLSearchParams(looksLikeBarcode ? { barcode: term } : { q: term, limit: "20" });
        const found = await api<Product[]>(`/products?${params}`);
        if (cancelled) return;
        rememberProducts(user.id, found);
        setResults(found);
        // Código de barras identifica o produto: já vai direto para a quantidade.
        if (looksLikeBarcode && found.length === 1) choose(found[0]);
      } catch (error) {
        if (cancelled) return;
        if (!(error instanceof NetworkError)) {
          setSearchNote(errorMessage(error));
          setResults([]);
          return;
        }
        const lower = term.toLowerCase();
        setSearchNote("Sem conexão: buscando só nos produtos já consultados neste aparelho.");
        setResults(readProducts(user.id).filter((p) =>
          p.name.toLowerCase().includes(lower) || p.sku?.toLowerCase().includes(lower) || p.barcode === term));
      }
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search, user.id]);

  function choose(p: Product) {
    setProduct(p);
    setUnitId(p.unit_id);
    setQty("");
    setNote("");
    setFormError(null);
    setFeedback(null);
  }

  function clearForm() {
    setProduct(null);
    setSearch("");
    setResults(null);
    setQty("");
    setNote("");
    setFormError(null);
  }

  const expectedUnit = units.find((u) => u.id === product?.unit_id);
  const chosenUnit = units.find((u) => u.id === unitId);
  const recountForProduct = product && recounts.find((r) => r.product_id === product.id);
  const alreadyCounted = product && !recountForProduct && (
    myCounts.some((c) => c.product_id === product.id) || queued.some((c) => c.product_id === product.id));
  const reviewOnly = session.status === "in_review";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!product || !chosenUnit) return;
    const quantity = parseQty(qty);
    if (quantity === null) {
      setFormError("Informe uma quantidade válida, com até 4 casas decimais.");
      return;
    }
    if (!chosenUnit.allows_fraction && isFractional(quantity)) {
      setFormError(`A unidade ${chosenUnit.code} não aceita fração.`);
      return;
    }
    const clientKey = newClientKey();
    try {
      queue.add({
        client_key: clientKey, session_id: session.id, product_id: product.id, product_name: product.name,
        unit_id: chosenUnit.id, unit_code: chosenUnit.code, quantity, note: note.trim() || null,
      });
    } catch {
      setFormError("Não foi possível guardar neste aparelho. A contagem NÃO foi salva: libere espaço e tente de novo.");
      return;
    }
    const name = product.name;
    clearForm();
    refreshQueue();
    await flush();
    const left = queue.all().find((c) => c.client_key === clientKey);
    if (!left) setFeedback({ tone: "ok", text: `Salvo: ${name}, ${formatQty(quantity)} ${chosenUnit.code}.` });
    else if (left.state === "rejected") setFeedback({ tone: "warn", text: `Recusado: ${left.error}` });
    else setFeedback({ tone: "warn", text: `Guardado no aparelho: ${name}. Será enviado quando a conexão voltar.` });
  }

  function discard(c: QueuedCount) {
    queue.discard(c.client_key);
    refreshQueue();
  }

  const pending = queued.filter((c) => c.state === "pending");
  const rejected = queued.filter((c) => c.state === "rejected");
  const unitCode = (id: string) => units.find((u) => u.id === id)?.code ?? "";

  return (
    <div className="stack">
      {loadError && <ErrorBox message={loadError} retry={refreshServer} />}
      {feedback && <p className={`notice ${feedback.tone}`} role="status">{feedback.text}</p>}

      {pending.length > 0 && (
        <div className="notice warn row between">
          <span>{pending.length} contagem(ns) guardada(s) no aparelho, aguardando envio.</span>
          <button className="link" disabled={sending} onClick={() => void flush()}>{sending ? "Enviando…" : "Enviar agora"}</button>
        </div>
      )}

      {rejected.map((c) => (
        <div key={c.client_key} className="notice danger">
          <strong>{c.product_name}: {formatQty(c.quantity)} {c.unit_code}</strong> não foi aceita. {c.error}
          <button className="link" onClick={() => discard(c)}>Descartar</button>
        </div>
      ))}

      {recounts.length > 0 && (
        <section className="card stack">
          <h2>Reconferências para fazer</h2>
          <ul className="plain">
            {recounts.map((r) => (
              <li key={r.item_id}>
                <button className="row-button" onClick={() => choose(toProduct(r, readProducts(user.id)))}>
                  <strong>{r.product_name}</strong>
                  <span className="muted small">{r.reason}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!active && <p className="notice">Esta sessão não aceita mais contagens.</p>}
      {reviewOnly && !product && recounts.length === 0 && (
        <p className="notice">Sessão em revisão: só aceita as reconferências pedidas.</p>
      )}

      {active && !product && !reviewOnly && (
        <section className="stack">
          <label>
            Produto
            <input type="search" autoFocus value={search} placeholder="Nome, código ou código de barras"
              onChange={(e) => setSearch(e.target.value)} />
          </label>
          {searchNote && <p className="notice warn">{searchNote}</p>}
          {results?.length === 0 && <p className="empty">Nenhum produto encontrado.</p>}
          {results && results.length > 0 && (
            <ul className="plain card">
              {results.map((p) => (
                <li key={p.id}>
                  <button className="row-button" onClick={() => choose(p)}>
                    <strong>{p.name}</strong>
                    <span className="muted small">{[p.sku, p.barcode].filter(Boolean).join(" · ")}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {active && product && (
        <form className="card stack" onSubmit={submit}>
          <div>
            <h2>{product.name}</h2>
            <span className="muted small">{[product.sku, product.barcode].filter(Boolean).join(" · ")}</span>
          </div>
          {recountForProduct && <p className="notice">Reconferência: {recountForProduct.reason}</p>}
          {alreadyCounted && (
            <p className="notice warn">Você já contou este produto. Contar de novo envia o item para revisão do gerente.</p>
          )}
          <div className="row">
            <label className="grow">
              Quantidade
              <input autoFocus className="qty" inputMode="decimal" autoComplete="off" required value={qty}
                onChange={(e) => setQty(e.target.value)} />
            </label>
            <label>
              Unidade
              <select value={unitId} onChange={(e) => setUnitId(e.target.value)}>
                {units.length === 0 && <option value={unitId}>…</option>}
                {units.map((u) => <option key={u.id} value={u.id}>{u.code}</option>)}
              </select>
            </label>
          </div>
          {expectedUnit && unitId !== expectedUnit.id && (
            <p className="notice warn">O cadastro usa {expectedUnit.code}. A quantidade não é convertida, e o item irá para revisão.</p>
          )}
          <label>
            Observação (opcional)
            <input maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {formError && <p className="notice danger" role="alert">{formError}</p>}
          <div className="row">
            <button type="button" onClick={clearForm}>Trocar produto</button>
            <button className="primary grow" disabled={!chosenUnit}>Salvar contagem</button>
          </div>
        </form>
      )}

      <section className="stack">
        <h2>Minhas contagens</h2>
        {pending.length === 0 && myCounts.length === 0 ? (
          <p className="empty">Você ainda não contou nada nesta sessão.</p>
        ) : (
          <ul className="plain card">
            {pending.map((c) => (
              <li key={c.client_key} className="row between line">
                <span>{c.product_name}<br /><span className="muted small">Aguardando envio</span></span>
                <strong>{formatQty(c.quantity)} {c.unit_code}</strong>
              </li>
            ))}
            {[...myCounts].reverse().map((c) => (
              <li key={c.id} className="row between line">
                <span>
                  {c.product_name}
                  <br /><span className="muted small">{formatDate(c.counted_at)}{c.round > 1 ? ` · rodada ${c.round}` : ""}</span>
                </span>
                <strong>{formatQty(c.quantity)} {unitCode(c.unit_id)}</strong>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function toProduct(r: PendingRecount, known: Product[]): Product {
  return known.find((p) => p.id === r.product_id)
    ?? { id: r.product_id, name: r.product_name, sku: null, barcode: null, unit_id: r.unit_id };
}
