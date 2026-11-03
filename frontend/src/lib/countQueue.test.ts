import { beforeEach, describe, expect, it, vi } from "vitest";

import { CountQueue, newClientKey, type QueuedCount, type SendResult } from "./countQueue";

function memoryStore() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

const item = (key: string) => ({
  client_key: key, session_id: "s", product_id: "p", product_name: "Arroz", unit_id: "u", unit_code: "UN",
  quantity: "10", note: null,
});

describe("CountQueue", () => {
  let store: ReturnType<typeof memoryStore>;
  let queue: CountQueue;

  beforeEach(() => {
    store = memoryStore();
    queue = new CountQueue("user-1", store);
  });

  it("mantém a contagem até o servidor confirmar", async () => {
    queue.add(item("a"));
    expect(await queue.flush(async () => "retry_later")).toBe("retry_later");
    expect(queue.all()).toHaveLength(1);
    expect(await queue.flush(async () => "sent")).toBe("done");
    expect(queue.all()).toHaveLength(0);
  });

  it("reenvia com a mesma chave", async () => {
    queue.add(item("a"));
    const keys: string[] = [];
    const send = async (c: QueuedCount): Promise<SendResult> => {
      keys.push(c.client_key);
      return keys.length === 1 ? "retry_later" : "sent";
    };
    await queue.flush(send);
    await queue.flush(send);
    expect(keys).toEqual(["a", "a"]);
  });

  it("para no primeiro problema temporário, preservando a ordem", async () => {
    queue.add(item("a"));
    queue.add(item("b"));
    const send = vi.fn(async () => "retry_later" as const);
    await queue.flush(send);
    expect(send).toHaveBeenCalledTimes(1);
    expect(queue.all().map((c) => c.client_key)).toEqual(["a", "b"]);
  });

  it("login expirado não perde nada", async () => {
    queue.add(item("a"));
    expect(await queue.flush(async () => "auth_expired")).toBe("auth_expired");
    expect(queue.all()[0].state).toBe("pending");
  });

  it("recusa definitiva fica visível, não é reenviada e não trava as próximas", async () => {
    queue.add(item("a"));
    queue.add(item("b"));
    const send = vi.fn(async (c: QueuedCount): Promise<SendResult> =>
      c.client_key === "a" ? { rejected: "Sessão encerrada." } : "sent");
    expect(await queue.flush(send)).toBe("done");
    expect(queue.all()).toMatchObject([{ client_key: "a", state: "rejected", error: "Sessão encerrada." }]);
    send.mockClear();
    await queue.flush(send);
    expect(send).not.toHaveBeenCalled();
    queue.discard("a");
    expect(queue.all()).toEqual([]);
  });

  it("separa as filas por usuário em aparelho compartilhado", () => {
    queue.add(item("a"));
    expect(new CountQueue("user-2", store).all()).toEqual([]);
    expect(new CountQueue("user-1", store).all()).toHaveLength(1);
  });

  it("não roda dois envios ao mesmo tempo", async () => {
    queue.add(item("a"));
    let release!: (r: SendResult) => void;
    const send = vi.fn(() => new Promise<SendResult>((r) => (release = r)));
    const first = queue.flush(send);
    expect(await queue.flush(send)).toBe("retry_later");
    release("sent");
    await first;
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("dados corrompidos não derrubam a tela", () => {
    store.setItem("mp.counts.user-1", "{quebrado");
    expect(queue.all()).toEqual([]);
  });

  it("gera chave aceita pelo servidor", () => {
    expect(newClientKey()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(newClientKey()).not.toBe(newClientKey());
  });
});
