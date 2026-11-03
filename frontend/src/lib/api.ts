const TOKEN_KEY = "mp.token";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

/** Sem resposta do servidor: rede fora, servidor desligado ou proxy sem backend. */
export class NetworkError extends Error {
  constructor() {
    super("Sem conexão com o servidor.");
  }
}

let onAuthExpired: () => void = () => undefined;
export function setAuthExpiredHandler(handler: () => void): void {
  onAuthExpired = handler;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

const FALLBACK: Record<number, string> = {
  403: "Você não tem permissão para esta ação.",
  404: "Registro não encontrado.",
  409: "Esta ação não é permitida no estado atual.",
  422: "Dados inválidos.",
  429: "Muitas requisições ao ERP. Tente de novo em instantes.",
};

const DEFAULT_TIMEOUT_MS = 15_000;

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; anonymous?: boolean; timeoutMs?: number } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token && !options.anonymous) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      // Rede que trava sem responder não pode deixar a tela presa. Se a contagem chegou mesmo assim,
      // o reenvio com a mesma client_key não duplica.
      signal: AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch {
    throw new NetworkError();
  }
  // Proxy sem backend responde 5xx sem JSON do FastAPI; para quem usa, é o mesmo que estar fora do ar.
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (response.ok) return data as T;
  if (response.status === 401 && !options.anonymous) onAuthExpired();
  // Em 422 do FastAPI `detail` é uma lista técnica; só texto vai para a tela.
  const detail = typeof data?.detail === "string" ? data.detail : null;
  if (response.status >= 500 && !detail) throw new NetworkError();
  throw new ApiError(response.status, detail ?? FALLBACK[response.status] ?? "Não foi possível concluir a ação.");
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Erro inesperado.";
}

/** GET que guarda a última resposta deste usuário, para a tela de contagem abrir sem rede. */
export async function cachedGet<T>(path: string, userId: string): Promise<{ data: T; stale: boolean }> {
  const key = `mp.cache.${userId}.${path}`;
  try {
    const data = await api<T>(path);
    try {
      localStorage.setItem(key, JSON.stringify(data));
    } catch {
      // Sem espaço: a tela segue, só não terá cópia para usar sem rede.
    }
    return { data, stale: false };
  } catch (error) {
    if (error instanceof NetworkError) {
      const saved = localStorage.getItem(key);
      if (saved) return { data: JSON.parse(saved) as T, stale: true };
    }
    throw error;
  }
}
