/** Versão implantada, publicada pelo servidor em /versao.json (infra/deploy/implantar.sh). */
export interface Versao {
  versao: string;
  commit: string;
  data: string;
  mudancas: string[];
}

const VISTA_KEY = "mp.versaoVista";

export function parseVersao(value: unknown): Versao | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (typeof v.versao !== "string" || typeof v.commit !== "string" || !v.commit) return null;
  const mudancas = Array.isArray(v.mudancas) ? v.mudancas.filter((m): m is string => typeof m === "string") : [];
  return { versao: v.versao, commit: v.commit, data: typeof v.data === "string" ? v.data : "", mudancas };
}

export async function buscarVersao(): Promise<Versao | null> {
  try {
    const response = await fetch("/versao.json", { cache: "no-store" });
    if (!response.ok) return null;
    return parseVersao(await response.json());
  } catch {
    return null; // sem rede ou em desenvolvimento: sem aviso
  }
}

// A versão que estava no ar quando este código foi carregado: vale até a página ser recarregada.
let carregada: Promise<Versao | null> | null = null;
export function versaoCarregada(): Promise<Versao | null> {
  carregada ??= buscarVersao();
  return carregada;
}

export function lerVista(): string | null {
  try {
    return localStorage.getItem(VISTA_KEY);
  } catch {
    return null;
  }
}

export function marcarVista(commit: string): void {
  try {
    localStorage.setItem(VISTA_KEY, commit);
  } catch {
    // sem armazenamento: o aviso volta na próxima abertura, sem outro efeito
  }
}

/** "nova": há versão mais recente no servidor que a carregada; "atualizado": carregada e ainda não vista. */
export function situacaoVersao(
  carregadaAgora: Versao | null, noServidor: Versao | null, vista: string | null,
): "nova" | "atualizado" | null {
  if (carregadaAgora && noServidor && noServidor.commit !== carregadaAgora.commit) return "nova";
  if (carregadaAgora && vista !== carregadaAgora.commit) return "atualizado";
  return null;
}
