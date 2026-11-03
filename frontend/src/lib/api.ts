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

// O token de acesso fica só na memória. O de renovação fica em cookie HttpOnly, que script da página
// não lê: uma falha de XSS não leva o login embora. Ao abrir o app, a memória é refeita pelo cookie.
let accessToken: string | null = null;
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

let onAuthExpired: () => void = () => undefined;
export function setAuthExpiredHandler(handler: () => void): void {
  onAuthExpired = handler;
}

const FALLBACK: Record<number, string> = {
  403: "Você não tem permissão para esta ação.",
  404: "Registro não encontrado.",
  409: "Esta ação não é permitida no estado atual.",
  422: "Dados inválidos.",
  429: "Muitas requisições ao ERP. Tente de novo em instantes.",
};

const DEFAULT_TIMEOUT_MS = 15_000;

interface Options {
  method?: string;
  body?: unknown;
  anonymous?: boolean;
  timeoutMs?: number;
}

async function send(path: string, options: Options): Promise<Response> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken && !options.anonymous) headers.Authorization = `Bearer ${accessToken}`;
  try {
    return await fetch(`/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      // Rede que trava sem responder não pode deixar a tela presa. Se a contagem chegou mesmo assim,
      // o reenvio com a mesma client_key não duplica.
      signal: AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch {
    throw new NetworkError();
  }
}

let refreshing: Promise<boolean> | null = null;

/** Uma renovação por vez: vários pedidos com token vencido esperam a mesma resposta. */
function refreshAccess(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await send("/auth/refresh", { method: "POST", anonymous: true });
      if (response.status === 401) {
        accessToken = null;
        return false;
      }
      if (!response.ok) throw new NetworkError();
      accessToken = ((await response.json()) as { access_token: string }).access_token;
      return true;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  let response: Response | null = null;
  if (options.anonymous || accessToken) response = await send(path, options);
  if (!options.anonymous && (response === null || response.status === 401)) {
    // Sem rede a renovação lança NetworkError e o login é mantido: a contagem segue guardada no aparelho.
    if (!(await refreshAccess())) {
      onAuthExpired();
      throw new ApiError(401, "Sua sessão expirou.");
    }
    response = await send(path, options);
  }
  response = response as Response;
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (response.ok) return data as T;
  if (response.status === 401 && !options.anonymous) onAuthExpired();
  // Em 422 do FastAPI `detail` é uma lista técnica; só texto vai para a tela.
  const detail = typeof data?.detail === "string" ? data.detail : null;
  // Proxy sem backend responde 5xx sem JSON do FastAPI; para quem usa, é o mesmo que estar fora do ar.
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
