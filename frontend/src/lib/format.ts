// Quantidades são strings decimais vindas da API: nunca passam por Number, para não perder precisão.

export function formatQty(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const [int, dec = ""] = value.split(".");
  const trimmed = dec.replace(/0+$/, "");
  return trimmed ? `${int},${trimmed}` : int;
}

/** Aceita "1,5" ou "1.5". Devolve null se não for uma quantidade válida (até 4 casas). */
export function parseQty(input: string): string | null {
  const text = input.trim().replace(",", ".");
  return /^\d{1,10}(\.\d{1,4})?$/.test(text) ? text : null;
}

export function isFractional(qty: string): boolean {
  return /\.\d*[1-9]/.test(qty);
}

const dateFormat = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

/** Horários gravados pelo nosso servidor são UTC, mesmo quando o SQLite devolve sem fuso. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? "—" : dateFormat.format(date);
}

/** Horário do ERP: o fuso não foi confirmado, então é mostrado exatamente como veio. */
export function formatErpDate(value: string | null | undefined): string {
  if (!value) return "não informado";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]} (fuso não informado)` : "não informado";
}

export const SESSION_STATUS: Record<string, string> = {
  open: "Aberta",
  in_review: "Em revisão",
  closed: "Encerrada",
  cancelled: "Cancelada",
};

export const ITEM_STATUS: Record<string, string> = {
  counted: "Contado",
  needs_review: "Precisa de revisão",
  recount_requested: "Reconferência pedida",
  resolved: "Decidido",
};

export const REVIEW_REASON: Record<string, string> = {
  count_conflict: "Contagens diferentes",
  duplicate_count: "Mesma pessoa contou mais de uma vez",
  unit_mismatch: "Unidade diferente da cadastrada",
  fraction_not_allowed: "Unidade não aceita fração",
  count_after_resolution: "Nova contagem depois da decisão",
  erp_difference: "Diferente do saldo no ERP",
  erp_movement_after_count: "ERP pode ter tido movimento depois da contagem",
  erp_movement_unverifiable: "ERP não informou o horário do último movimento",
};

export const ROLE: Record<string, string> = {
  admin: "Administrador",
  manager: "Gerente",
  operator: "Operador",
  auditor: "Auditor",
};
