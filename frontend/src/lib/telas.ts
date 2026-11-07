/**
 * Código de cada tela do MercadoPilot, no padrão de suporte SysOrdem (`APP-MOD-NN`).
 * O catálogo guarda só `MOD-NN`; a sigla da aplicação (MPI) entra na hora de mostrar e de enviar.
 * Toda rota nova de `main.tsx` precisa entrar aqui: o teste `telas.test.ts` acusa rota sem código.
 * Códigos nunca mudam nem são reaproveitados, mesmo quando a tela sai do sistema.
 */

export const SIGLA_APP = "MPI";
export const NOME_APP = "MercadoPilot AI";

export interface Tela {
  /** "INV-02": 3 letras do módulo + 2 dígitos */
  codigo: string;
  /** rota no formato do React Router ("/sessoes/:sessionId"); null = tela mostrada sem rota própria */
  rota: string | null;
  nome: string;
  modulo: string;
}

const T = (codigo: string, rota: string | null, nome: string, modulo: string): Tela => ({ codigo, rota, nome, modulo });

export const TELAS: readonly Tela[] = [
  T("ACE-01", null, "Entrar", "Acesso"),
  T("INV-01", "/", "Sessões de contagem", "Inventário"),
  T("INV-02", "/sessoes/:sessionId", "Sessão de contagem", "Inventário"),
  T("INV-03", "/sessoes/:sessionId/itens/:itemId", "Item da contagem", "Inventário"),
  T("REC-01", "/recebimentos", "Recebimentos", "Recebimento"),
  T("REC-02", "/recebimentos/:receivingId", "Recebimento", "Recebimento"),
  T("CON-01", "/consumo", "Consumos internos", "Consumo interno"),
  T("CON-02", "/consumo/:consumptionId", "Consumo interno", "Consumo interno"),
  T("CTA-01", "/conta", "Minha conta", "Conta"),
  T("CFG-01", "/configuracao", "Configuração", "Configurações"),
];

export const TELA_ENTRAR = TELAS[0]!;

/** Código completo, como o usuário vê e o suporte procura: "MPI-INV-02". */
export const codigoCompleto = (t: Tela) => `${SIGLA_APP}-${t.codigo}`;

function casa(rota: string, caminho: string): boolean {
  const r = rota.split("/");
  const c = caminho.split("/");
  return r.length === c.length && r.every((seg, i) => (seg.startsWith(":") ? c[i] !== "" : seg === c[i]));
}

const parametros = (rota: string) => rota.split("/").filter((s) => s.startsWith(":")).length;

/** Tela do endereço atual; rota fixa ganha da rota com parâmetro. */
export function telaDoEndereco(pathname: string): Tela | null {
  const caminho = pathname.split(/[?#]/)[0]!.replace(/\/+$/, "") || "/";
  const achadas = TELAS.filter((t) => t.rota !== null && casa(t.rota, caminho));
  return achadas.sort((a, b) => parametros(a.rota!) - parametros(b.rota!))[0] ?? null;
}

/**
 * Texto copiado pelo selo para o suporte. Só identificadores técnicos: o endereço vai como rota modelo
 * (sem o número da sessão, do item ou do recebimento) e nunca leva token, senha ou dado de produto.
 */
export function informacoesSuporte(i: {
  tela: Tela | null;
  versao: string | null;
  commit: string | null;
  organizacao: string | null;
  loja: string | null;
  perfil: string;
  quando: Date;
  erro?: string | null;
}): string {
  const linhas = [
    `${NOME_APP} · informações para o suporte`,
    `Tela: ${i.tela ? `${codigoCompleto(i.tela)} · ${i.tela.nome}` : "sem código"}`,
    `Endereço: ${i.tela?.rota ?? "—"}`,
    `Versão: ${i.versao ?? "desconhecida"}${i.commit ? ` (${i.commit.slice(0, 7)})` : ""}`,
    `Organização: ${i.organizacao ?? "—"}`,
    `Loja: ${i.loja ?? "todas"}`,
    `Perfil: ${i.perfil}`,
    `Data: ${i.quando.toLocaleString("pt-BR")}`,
  ];
  if (i.erro) linhas.splice(2, 0, `Código do erro: ${i.erro}`);
  return linhas.join("\n");
}

/** Código curto do erro (8 caracteres hexadecimais) que o usuário repassa ao suporte. */
export function novoCodigoErro(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
