/** Consumo interno (Central de Movimentações, Etapa 5.1). Registro e aprovação; nada é enviado ao ERP. */
export type ConsumptionStatus = "pending_approval" | "needs_correction" | "approved" | "rejected" | "cancelled";
export type DepartmentMode = "self_service" | "centralized";

export interface Consumption {
  id: string;
  store_id: string;
  location_id: string;
  department_id: string;
  purpose_id: string;
  product_id: string;
  unit_id: string;
  quantity: string;
  consumer_user_id: string | null;
  consumer_name: string;
  registered_by: string;
  status: ConsumptionStatus;
  note: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  erp_status: string;
  created_at: string;
  updated_at: string;
  product_name: string;
  unit_code: string;
  department_name: string;
  purpose_name: string;
  store_name: string;
  location_name: string;
  registered_by_name: string;
  decided_by_name: string | null;
}

export interface Department {
  id: string;
  name: string;
  mode: DepartmentMode;
  is_active: boolean;
}

export interface Purpose {
  id: string;
  name: string;
  is_active: boolean;
}

export interface ConsumptionConfig {
  segregation: boolean;
  departments: Department[];
  purposes: Purpose[];
}

export interface Consumer {
  id: string;
  name: string;
}

export const CONSUMPTION_STATUS: Record<ConsumptionStatus, string> = {
  pending_approval: "Aguardando aprovação",
  needs_correction: "Correção pedida",
  approved: "Aprovado",
  rejected: "Rejeitado",
  cancelled: "Cancelado",
};

export const DEPARTMENT_MODE: Record<DepartmentMode, string> = {
  self_service: "Autoatendimento: o próprio funcionário registra",
  centralized: "Centralizado: só o responsável registra",
};

/** Quem pode registrar neste departamento: operador só no autoatendimento; gerente e admin sempre. */
export function canRegisterIn(role: string, dep: Department): boolean {
  if (role === "auditor" || !dep.is_active) return false;
  return role !== "operator" || dep.mode === "self_service";
}

/** Botão de aprovar: gerente/admin, consumo pendente e, com segregação, quem não registrou nem consumiu. */
export function canApprove(
  user: { id: string; role: string }, item: Consumption, segregation: boolean,
): boolean {
  if (user.role !== "admin" && user.role !== "manager") return false;
  if (item.status !== "pending_approval") return false;
  return !segregation || (item.registered_by !== user.id && item.consumer_user_id !== user.id);
}
