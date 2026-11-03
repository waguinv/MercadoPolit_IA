import { describe, expect, it } from "vitest";

import { formatErpDate, formatQty, isFractional, parseQty } from "./format";

describe("quantidades", () => {
  it("mostra sem zeros à direita e com vírgula", () => {
    expect(formatQty("10.0000")).toBe("10");
    expect(formatQty("35.5000")).toBe("35,5");
    expect(formatQty("0.0000")).toBe("0");
    expect(formatQty("100")).toBe("100");
    expect(formatQty(null)).toBe("—");
  });

  it("aceita vírgula ou ponto e recusa o inválido", () => {
    expect(parseQty("1,5")).toBe("1.5");
    expect(parseQty(" 12 ")).toBe("12");
    expect(parseQty("0")).toBe("0");
    for (const bad of ["", "-1", "1,23456", "abc", "1,2,3", "1e3", "NaN"]) expect(parseQty(bad)).toBeNull();
  });

  it("identifica fração real", () => {
    expect(isFractional("1.5")).toBe(true);
    expect(isFractional("1.0")).toBe(false);
    expect(isFractional("10")).toBe(false);
  });
});

describe("horário do ERP", () => {
  it("não converte fuso e avisa", () => {
    expect(formatErpDate("2026-11-03T10:30:00")).toBe("03/11/2026 10:30 (fuso não informado)");
    expect(formatErpDate(null)).toBe("não informado");
  });
});
