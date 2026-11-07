import { describe, expect, it } from "vitest";

import { canApprove, canRegisterIn, type Consumption, type Department } from "./consumption";

const dep = (mode: Department["mode"], is_active = true): Department => ({ id: "d", name: "D", mode, is_active });
const item = (over: Partial<Consumption> = {}) =>
  ({ status: "pending_approval", registered_by: "r", consumer_user_id: "c", ...over }) as Consumption;

describe("consumo interno", () => {
  it("operador só registra no autoatendimento; auditor nunca", () => {
    expect(canRegisterIn("operator", dep("self_service"))).toBe(true);
    expect(canRegisterIn("operator", dep("centralized"))).toBe(false);
    expect(canRegisterIn("manager", dep("centralized"))).toBe(true);
    expect(canRegisterIn("auditor", dep("self_service"))).toBe(false);
    expect(canRegisterIn("admin", dep("self_service", false))).toBe(false);
  });

  it("com segregação, quem registrou ou consumiu não aprova", () => {
    expect(canApprove({ id: "m", role: "manager" }, item(), true)).toBe(true);
    expect(canApprove({ id: "r", role: "manager" }, item(), true)).toBe(false);
    expect(canApprove({ id: "c", role: "admin" }, item(), true)).toBe(false);
    expect(canApprove({ id: "r", role: "manager" }, item(), false)).toBe(true);
  });

  it("só gerente ou admin aprova, e só o que está pendente", () => {
    expect(canApprove({ id: "x", role: "operator" }, item(), true)).toBe(false);
    expect(canApprove({ id: "x", role: "manager" }, item({ status: "approved" }), true)).toBe(false);
  });
});
