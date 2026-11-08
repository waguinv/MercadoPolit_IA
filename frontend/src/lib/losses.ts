/** Perdas e avarias (Central de Movimentações, Etapa 5.2). Registro e aprovação; nada é enviado ao ERP. */
import type { ConsumptionStatus } from "./consumption";

export type LossReason =
  | "damaged_package" | "spoilage" | "expired" | "breakage" | "contamination" | "operational" | "other";

export interface Loss {
  id: string;
  store_id: string;
  location_id: string;
  product_id: string;
  unit_id: string;
  quantity: string;
  reason: LossReason;
  lot: string | null;
  expiry_date: string | null;
  note: string | null;
  registered_by: string;
  status: ConsumptionStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  erp_status: string;
  created_at: string;
  updated_at: string;
  product_name: string;
  unit_code: string;
  store_name: string;
  location_name: string;
  registered_by_name: string;
  decided_by_name: string | null;
}

export const LOSS_STATUS: Record<ConsumptionStatus, string> = {
  pending_approval: "Aguardando aprovação",
  needs_correction: "Correção pedida",
  approved: "Aprovada",
  rejected: "Rejeitada",
  cancelled: "Cancelada",
};

export const LOSS_REASON: Record<LossReason, string> = {
  damaged_package: "Embalagem danificada",
  spoilage: "Deterioração",
  expired: "Vencimento",
  breakage: "Quebra/acidente",
  contamination: "Contaminação",
  operational: "Perda operacional",
  other: "Outros",
};

/** Botão de aprovar: gerente/admin, perda pendente e, com segregação, quem não registrou. */
export function canApproveLoss(user: { id: string; role: string }, item: Loss, segregation: boolean): boolean {
  if (user.role !== "admin" && user.role !== "manager") return false;
  if (item.status !== "pending_approval") return false;
  return !segregation || item.registered_by !== user.id;
}
