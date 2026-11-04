// Regras do recebimento conferidas ainda no aparelho, para dar uma mensagem clara em português.
// O servidor continua sendo quem decide: ele aplica as mesmas regras e pode recusar.
import { newClientKey } from "./countQueue";
import { isFractional, isPositiveQty, parseQty, subtractQty } from "./format";
import type { RejectionReason } from "./types";

export const RECEIVING_STATUS: Record<string, string> = {
  draft: "Rascunho",
  finalized: "Finalizado",
  cancelled: "Cancelado",
};

export const REJECTION_REASON: Record<RejectionReason, string> = {
  damaged: "Avaria",
  incorrect_item: "Item incorreto",
  expired: "Vencido",
  quality: "Qualidade",
  other: "Outro",
};

/** Chave de acesso da NF-e: 44 dígitos, com o último sendo o verificador (módulo 11). */
export function isValidAccessKey(key: string): boolean {
  if (!/^\d{44}$/.test(key)) return false;
  let total = 0;
  let weight = 2;
  for (let i = 42; i >= 0; i -= 1) {
    total += Number(key[i]) * weight;
    weight = weight === 9 ? 2 : weight + 1;
  }
  const rest = total % 11;
  return Number(key[43]) === (rest < 2 ? 0 : 11 - rest);
}

export interface HeaderForm {
  supplierName: string;
  supplierDocument: string;
  documentNumber: string;
  documentSeries: string;
  accessKey: string;
  note: string;
}

export interface HeaderPayload {
  supplier_name: string;
  supplier_document: string | null;
  document_number: string | null;
  document_series: string;
  access_key: string | null;
  note: string | null;
}

export function validateHeader(form: HeaderForm): { error: string } | { payload: HeaderPayload } {
  const supplier = form.supplierName.trim();
  if (!supplier) return { error: "Informe o fornecedor." };
  const document = form.supplierDocument.replace(/[.\-/\s]/g, "");
  if (document && !/^(\d{11}|\d{14})$/.test(document)) {
    return { error: "O documento do fornecedor deve ter 11 (CPF) ou 14 (CNPJ) dígitos." };
  }
  const key = form.accessKey.replace(/\s/g, "");
  if (key && !isValidAccessKey(key)) {
    return { error: "Chave de acesso inválida: confira os 44 dígitos." };
  }
  return {
    payload: {
      supplier_name: supplier,
      supplier_document: document || null,
      document_number: form.documentNumber.trim() || null,
      document_series: form.documentSeries.trim(),
      access_key: key || null,
      note: form.note.trim() || null,
    },
  };
}

export interface ItemForm {
  expected: string;
  received: string;
  rejected: string;
  reason: RejectionReason | "";
  expiry: string;
  /** Dia do recebimento, para a regra de validade. */
  receivedOn: string;
  unitCode: string;
  allowsFraction: boolean;
}

export interface ItemQuantities {
  expected: string | null;
  received: string;
  rejected: string;
  accepted: string;
  reason: RejectionReason | null;
}

/** Aceito é sempre calculado: recebido - rejeitado. Devolve null enquanto os números não formam um resultado. */
export function acceptedOf(received: string, rejected: string): string | null {
  const got = parseQty(received, 3);
  const bad = rejected.trim() === "" ? "0" : parseQty(rejected, 3);
  return got === null || bad === null ? null : subtractQty(got, bad);
}

export function validateItem(form: ItemForm): { error: string } | ItemQuantities {
  const received = parseQty(form.received, 3);
  if (received === null) return { error: "Informe a quantidade recebida, com até 3 casas decimais." };
  const rejected = form.rejected.trim() === "" ? "0" : parseQty(form.rejected, 3);
  if (rejected === null) return { error: "Quantidade rejeitada inválida: use até 3 casas decimais." };
  const expected = form.expected.trim() === "" ? null : parseQty(form.expected, 3);
  if (form.expected.trim() !== "" && expected === null) {
    return { error: "Quantidade esperada inválida: use até 3 casas decimais." };
  }
  if (!form.allowsFraction) {
    for (const value of [received, rejected, expected]) {
      if (value !== null && isFractional(value)) return { error: `A unidade ${form.unitCode} não aceita fração.` };
    }
  }
  const accepted = subtractQty(received, rejected);
  if (accepted === null) return { error: "O rejeitado não pode ser maior que o recebido." };
  const hasRejection = isPositiveQty(rejected);
  if (hasRejection && !form.reason) return { error: "Informe o motivo da rejeição." };
  if (form.expiry && form.expiry < form.receivedOn && isPositiveQty(accepted)) {
    return { error: "Produto vencido não pode ser aceito: rejeite toda a quantidade com o motivo Vencido." };
  }
  return { expected, received, rejected, accepted, reason: hasRejection ? (form.reason as RejectionReason) : null };
}

/**
 * Chave de idempotência de um formulário. Reenviar os mesmos dados (a resposta pode ter se perdido) usa a
 * mesma chave, e o servidor não duplica. Dados alterados são outro pedido, com outra chave.
 */
export class RequestKey {
  private payload: string | null = null;
  private key = "";

  constructor(private readonly generate: () => string = newClientKey) {}

  for(payload: unknown): string {
    const text = JSON.stringify(payload);
    if (text !== this.payload) {
      this.payload = text;
      this.key = this.generate();
    }
    return this.key;
  }

  /** Depois do sucesso: dados iguais, a partir daqui, são um novo lançamento. */
  reset(): void {
    this.payload = null;
  }
}
