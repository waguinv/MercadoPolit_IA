// Fila de contagens no aparelho. A contagem é gravada aqui ANTES do envio: se a rede cair ou o login
// expirar, nada se perde, e o reenvio usa a mesma client_key (o servidor não duplica).
// A fila é por usuário: em aparelho compartilhado, uma contagem nunca sai com o login de outra pessoa.

export interface QueuedCount {
  client_key: string;
  session_id: string;
  product_id: string;
  product_name: string;
  unit_id: string;
  unit_code: string;
  quantity: string;
  note: string | null;
  queued_at: string;
  state: "pending" | "rejected";
  error?: string;
}

export type SendResult = "sent" | "retry_later" | "auth_expired" | { rejected: string };

type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

export function newClientKey(): string {
  // randomUUID só existe em HTTPS/localhost; getRandomValues também funciona em HTTP na rede local.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export class CountQueue {
  private flushing = false;
  private readonly key: string;

  constructor(userId: string, private readonly store: KeyValueStore = localStorage) {
    this.key = `mp.counts.${userId}`;
  }

  all(): QueuedCount[] {
    try {
      const parsed = JSON.parse(this.store.getItem(this.key) ?? "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  private save(items: QueuedCount[]): void {
    this.store.setItem(this.key, JSON.stringify(items));
  }

  /** Lança erro se o aparelho não conseguir gravar; quem chama deve avisar a pessoa. */
  add(item: Omit<QueuedCount, "state" | "queued_at">): void {
    this.save([...this.all(), { ...item, state: "pending", queued_at: new Date().toISOString() }]);
  }

  /** Só por decisão explícita da pessoa; o sistema nunca descarta uma contagem sozinho. */
  discard(clientKey: string): void {
    this.save(this.all().filter((c) => c.client_key !== clientKey));
  }

  private update(clientKey: string, change: (c: QueuedCount) => QueuedCount | null): void {
    const next: QueuedCount[] = [];
    for (const c of this.all()) {
      const changed = c.client_key === clientKey ? change(c) : c;
      if (changed) next.push(changed);
    }
    this.save(next);
  }

  /** Envia em ordem e para no primeiro problema temporário: as próximas falhariam igual. */
  async flush(send: (c: QueuedCount) => Promise<SendResult>): Promise<"done" | "retry_later" | "auth_expired"> {
    if (this.flushing) return "retry_later";
    this.flushing = true;
    try {
      for (const item of this.all().filter((c) => c.state === "pending")) {
        const result = await send(item);
        if (result === "retry_later" || result === "auth_expired") return result;
        if (result === "sent") this.update(item.client_key, () => null);
        else this.update(item.client_key, (c) => ({ ...c, state: "rejected", error: result.rejected }));
      }
      return "done";
    } finally {
      this.flushing = false;
    }
  }
}
