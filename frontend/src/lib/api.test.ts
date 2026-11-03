import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api, ApiError, NetworkError, setAccessToken, setAuthExpiredHandler } from "./api";

type Reply = { status: number; body?: unknown } | "network-error";

const json = (reply: { status: number; body?: unknown }) =>
  new Response(reply.status === 204 ? null : JSON.stringify(reply.body ?? {}), { status: reply.status });

/** Simula o servidor: `refresh` responde à renovação; `data` à rota comum, conforme o token recebido. */
function server(refresh: Reply[], data: (token: string | null) => Reply) {
  const calls: { path: string; token: string | null }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const path = url.replace("/api/v1", "");
    const header = (init.headers as Record<string, string>).Authorization;
    const token = header ? header.replace("Bearer ", "") : null;
    calls.push({ path, token });
    const reply = path === "/auth/refresh" ? refresh.shift() ?? { status: 401 } : data(token);
    if (reply === "network-error") throw new TypeError("rede fora");
    return json(reply);
  }));
  return calls;
}

const onlyWith = (valid: string) => (token: string | null): Reply =>
  token === valid ? { status: 200, body: { ok: true } } : { status: 401 };

describe("renovação do login", () => {
  let expired: Mock<() => void>;

  beforeEach(() => {
    setAccessToken(null);
    expired = vi.fn<() => void>();
    setAuthExpiredHandler(expired);
  });

  it("token vencido é renovado e o pedido é repetido sem a pessoa notar", async () => {
    setAccessToken("vencido");
    const calls = server([{ status: 200, body: { access_token: "novo" } }], onlyWith("novo"));
    expect(await api("/dados")).toEqual({ ok: true });
    expect(calls.map((c) => [c.path, c.token])).toEqual([
      ["/dados", "vencido"], ["/auth/refresh", null], ["/dados", "novo"],
    ]);
    expect(expired).not.toHaveBeenCalled();
  });

  it("ao abrir o app, sem token na memória, renova antes de pedir", async () => {
    const calls = server([{ status: 200, body: { access_token: "novo" } }], onlyWith("novo"));
    await api("/dados");
    expect(calls.map((c) => c.path)).toEqual(["/auth/refresh", "/dados"]);
  });

  it("vários pedidos ao mesmo tempo fazem uma renovação só", async () => {
    setAccessToken("vencido");
    const calls = server([{ status: 200, body: { access_token: "novo" } }], onlyWith("novo"));
    await Promise.all([api("/a"), api("/b"), api("/c")]);
    expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(1);
    expect(expired).not.toHaveBeenCalled();
  });

  it("renovação recusada encerra a sessão", async () => {
    setAccessToken("vencido");
    server([{ status: 401 }], onlyWith("novo"));
    await expect(api("/dados")).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("sem rede na renovação NÃO encerra a sessão", async () => {
    setAccessToken("vencido");
    server(["network-error"], onlyWith("novo"));
    await expect(api("/dados")).rejects.toBeInstanceOf(NetworkError);
    expect(expired).not.toHaveBeenCalled();
  });

  it("não entra em laço se o token novo também for recusado", async () => {
    setAccessToken("vencido");
    const calls = server([{ status: 200, body: { access_token: "novo" } }], () => ({ status: 401 }));
    await expect(api("/dados")).rejects.toBeInstanceOf(ApiError);
    expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(1);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("senha errada no login não tenta renovar nem encerra sessão", async () => {
    const calls = server([], () => ({ status: 401, body: { detail: "Credenciais inválidas." } }));
    await expect(api("/auth/login", { method: "POST", body: {}, anonymous: true })).rejects.toThrow("Credenciais inválidas.");
    expect(calls).toHaveLength(1);
    expect(expired).not.toHaveBeenCalled();
  });
});
