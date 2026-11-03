export type Role = "admin" | "manager" | "operator" | "auditor";

export interface User {
  id: string;
  store_id: string | null;
  email: string;
  full_name: string;
  role: Role;
}

export interface Store {
  id: string;
  code: string;
  name: string;
}

export interface Location {
  id: string;
  store_id: string;
  code: string;
  name: string;
  external_code: string | null;
  is_active: boolean;
}

export interface Unit {
  id: string;
  code: string;
  name: string;
  allows_fraction: boolean;
}

export interface Product {
  id: string;
  sku: string | null;
  barcode: string | null;
  name: string;
  unit_id: string;
}

export type SessionStatus = "open" | "in_review" | "closed" | "cancelled";
export type ItemStatus = "counted" | "needs_review" | "recount_requested" | "resolved";

export interface Session {
  id: string;
  store_id: string;
  location_id: string;
  sector: string | null;
  name: string;
  status: SessionStatus;
  created_at: string;
  closed_at: string | null;
  cancel_reason: string | null;
}

export interface SessionDetail extends Session {
  items_total: number;
  items_by_status: Record<string, number>;
}

export interface MyCount {
  id: string;
  product_id: string;
  product_name: string;
  round: number;
  quantity: string;
  unit_id: string;
  unit_issue: string | null;
  counted_at: string;
}

export interface PendingRecount {
  item_id: string;
  product_id: string;
  product_name: string;
  unit_id: string;
  round: number;
  reason: string;
  assigned_user_id: string | null;
}

export interface Item {
  id: string;
  product_id: string;
  product_name: string;
  unit_id: string;
  status: ItemStatus;
  current_round: number;
  review_reasons: string[];
  accepted_count_id: string | null;
  accepted_quantity: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface ReviewCount {
  id: string;
  round: number;
  quantity: string;
  unit_id: string;
  unit_issue: string | null;
  counted_by_name: string;
  counted_at: string;
  note: string | null;
}

export interface Recount {
  id: string;
  round: number;
  reason: string;
  assigned_user_id: string | null;
  requested_at: string;
}

export interface ErpCheck {
  id: string;
  round: number;
  erp_quantity: string;
  erp_reserved_quantity: string | null;
  read_at: string;
  erp_last_changed_at: string | null;
}

export interface ItemDetail extends Item {
  counts: ReviewCount[];
  recounts: Recount[];
  erp_checks: ErpCheck[];
}

export interface ErpCheckResult {
  check: ErpCheck;
  difference: string | null;
  item_status: ItemStatus;
  review_reasons: string[];
}

export interface Assignment {
  user_id: string;
}

export interface AssignableUser {
  id: string;
  full_name: string;
  role: Role;
}
