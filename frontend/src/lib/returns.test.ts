import { describe, expect, it } from "vitest";

import { canApproveReturn, remainingToReplace, replacementCandidates, type SupplierReturn } from "./returns";
import type { Receiving } from "./types";

const item = (over: Partial<SupplierReturn> = {}) => ({
  status: "pending_approval", registered_by: "r", store_id: "s1", supplier_name: "Boa Distribuidora",
  supplier_document: null, receiving_id: "origem", replacements: [], ...over,
}) as SupplierReturn;
const rec = (over: Partial<Receiving> = {}) => ({
  id: "n1", status: "finalized", store_id: "s1", supplier_name: "boa distribuidora ", supplier_document: null, ...over,
}) as Receiving;

describe("devoluções e trocas", () => {
  it("com segregação, quem registrou não aprova", () => {
    expect(canApproveReturn({ id: "m", role: "manager" }, item(), true)).toBe(true);
    expect(canApproveReturn({ id: "r", role: "manager" }, item(), true)).toBe(false);
    expect(canApproveReturn({ id: "r", role: "admin" }, item(), false)).toBe(true);
    expect(canApproveReturn({ id: "x", role: "operator" }, item(), true)).toBe(false);
    expect(canApproveReturn({ id: "x", role: "admin" }, item({ status: "approved" }), true)).toBe(false);
  });

  it("calcula o que falta repor sem erro de arredondamento", () => {
    expect(remainingToReplace({ quantity: "0.300", replaced_quantity: "0.100" })).toBe("0.200");
    expect(remainingToReplace({ quantity: "2.000", replaced_quantity: "2.000" })).toBe("0.000");
  });

  it("só oferece recebimento finalizado, da loja e do fornecedor, e nunca a nota de origem", () => {
    const ok = rec();
    const fora = [
      rec({ id: "a", status: "draft" }), rec({ id: "b", store_id: "s2" }), rec({ id: "origem" }),
      rec({ id: "c", supplier_name: "Outro" }),
    ];
    expect(replacementCandidates(item(), [ok, ...fora])).toEqual([ok]);
  });

  it("não oferece de novo a nota já usada nesta devolução", () => {
    const usada = item({ replacements: [{ receiving_id: "n1" } as SupplierReturn["replacements"][number]] });
    expect(replacementCandidates(usada, [rec()])).toEqual([]);
  });

  it("havendo documento nos dois lados, o documento decide o fornecedor", () => {
    const doc = item({ supplier_document: "11111111111111" });
    expect(replacementCandidates(doc, [rec({ supplier_document: "22222222222222" })])).toEqual([]);
    const mesmo = rec({ supplier_document: "11111111111111", supplier_name: "Razão diferente" });
    expect(replacementCandidates(doc, [mesmo])).toEqual([mesmo]);
  });
});
