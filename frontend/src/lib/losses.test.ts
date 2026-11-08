import { describe, expect, it } from "vitest";

import { canApproveLoss, type Loss } from "./losses";

const item = (over: Partial<Loss> = {}) => ({ status: "pending_approval", registered_by: "r", ...over }) as Loss;

describe("perdas e avarias", () => {
  it("com segregação, quem registrou não aprova", () => {
    expect(canApproveLoss({ id: "m", role: "manager" }, item(), true)).toBe(true);
    expect(canApproveLoss({ id: "r", role: "manager" }, item(), true)).toBe(false);
    expect(canApproveLoss({ id: "r", role: "admin" }, item(), false)).toBe(true);
  });

  it("só gerente ou admin aprova, e só o que está pendente", () => {
    expect(canApproveLoss({ id: "x", role: "operator" }, item(), true)).toBe(false);
    expect(canApproveLoss({ id: "x", role: "auditor" }, item(), true)).toBe(false);
    expect(canApproveLoss({ id: "x", role: "admin" }, item({ status: "rejected" }), true)).toBe(false);
  });
});
