/** Devoluções e trocas com fornecedor (Central de Movimentações, Etapa 5.3). Nada é enviado ao ERP. */
import type { Receiving } from "./types";

export type ReturnStatus =
  | "pending_approval" | "needs_correction" | "approved" | "rejected" | "cancelled" | "picked_up" | "completed";
export type ReturnReason = "damaged" | "expired" | "near_expiry" | "quality" | "incorrect_item" | "other";
export type ReturnModality = "exchange" | "credit" | "no_replacement";

export interface Replacement {
  id: string;
  receiving_id: string;
  document_number: string | null;
  quantity: string;
  registered_by_name: string;
  created_at: string;
}

export interface SupplierReturn {
  id: string;
  store_id: string;
  location_id: string;
  product_id: string;
  unit_id: string;
  quantity: string;
  reason: ReturnReason;
  modality: ReturnModality;
  supplier_name: string;
  supplier_document: string | null;
  receiving_id: string | null;
  origin_document: string | null;
  lot: string | null;
  expiry_date: string | null;
  note: string | null;
  expected_pickup_date: string | null;
  expected_replacement_date: string | null;
  registered_by: string;
  status: ReturnStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  picked_up_at: string | null;
  pickup_note: string | null;
  completed_at: string | null;
  completion_note: string | null;
  erp_status: string;
  created_at: string;
  updated_at: string;
  product_name: string;
  unit_code: string;
  store_name: string;
  location_name: string;
  registered_by_name: string;
  decided_by_name: string | null;
  picked_up_by_name: string | null;
  completed_by_name: string | null;
  replaced_quantity: string;
  replacements: Replacement[];
}

export const RETURN_STATUS: Record<ReturnStatus, string> = {
  pending_approval: "Aguardando aprovação",
  needs_correction: "Correção pedida",
  approved: "Aprovada, aguardando coleta",
  rejected: "Rejeitada",
  cancelled: "Cancelada",
  picked_up: "Coletada",
  completed: "Concluída",
};

export const RETURN_REASON: Record<ReturnReason, string> = {
  damaged: "Avaria",
  expired: "Vencido",
  near_expiry: "Próximo do vencimento",
  quality: "Qualidade",
  incorrect_item: "Produto errado",
  other: "Outros",
};

export const RETURN_MODALITY: Record<ReturnModality, string> = {
  exchange: "Troca",
  credit: "Crédito",
  no_replacement: "Devolução sem reposição",
};

/** Botão de aprovar: gerente/admin, devolução pendente e, com segregação, quem não registrou. */
export function canApproveReturn(user: { id: string; role: string }, item: SupplierReturn, segregation: boolean): boolean {
  if (user.role !== "admin" && user.role !== "manager") return false;
  if (item.status !== "pending_approval") return false;
  return !segregation || item.registered_by !== user.id;
}

/** Quanto ainda falta repor, em texto com três casas (mesma precisão do servidor). */
export function remainingToReplace(item: Pick<SupplierReturn, "quantity" | "replaced_quantity">): string {
  const thousandths = Math.round(Number(item.quantity) * 1000) - Math.round(Number(item.replaced_quantity) * 1000);
  return (Math.max(thousandths, 0) / 1000).toFixed(3);
}

/** Recebimentos que podem ser a reposição: finalizados, da mesma loja e fornecedor, e não a nota de origem. */
export function replacementCandidates(item: SupplierReturn, receivings: Receiving[]): Receiving[] {
  const name = item.supplier_name.trim().toLocaleLowerCase("pt-BR");
  return receivings.filter((r) => {
    if (r.status !== "finalized" || r.store_id !== item.store_id || r.id === item.receiving_id) return false;
    if (item.replacements.some((used) => used.receiving_id === r.id)) return false;
    if (item.supplier_document && r.supplier_document) return item.supplier_document === r.supplier_document;
    return r.supplier_name.trim().toLocaleLowerCase("pt-BR") === name;
  });
}

export const receivingLabel = (r: Receiving) =>
  `${r.supplier_name}${r.document_number ? ` · NF ${r.document_number}` : ""} · ${new Date(r.received_at).toLocaleDateString("pt-BR")}`;
