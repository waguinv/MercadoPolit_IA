import { describe, expect, it } from "vitest";

import { isPositiveQty, formatDay, parseQty, subtractQty } from "./format";
import { acceptedOf, isValidAccessKey, RequestKey, validateHeader, validateItem, type ItemForm } from "./receiving";

// Geradas por access_key_is_valid do backend: restos 0, 1, 2 e 7 da divisão por 11.
// Valores fixos, para o teste não repetir o cálculo que está conferindo.
const VALID_KEYS = [
  "00000000000000000000000000000000000000000000",
  "00000000000000000000000000000000000000000060",
  "00000000000000000000000000000000000000000019",
  "00000000000000000000000000000000000000000094",
];

const form = (over: Partial<ItemForm> = {}): ItemForm => ({
  expected: "", received: "10", rejected: "", reason: "", expiry: "", receivedOn: "2026-11-04",
  unitCode: "CX", allowsFraction: true, ...over,
});

const header = { supplierName: "Fornecedor", supplierDocument: "", documentNumber: "", documentSeries: "", accessKey: "", note: "" };

describe("quantidades sem ponto flutuante", () => {
  it("subtrai com exatidão onde o número comum erraria", () => {
    expect(subtractQty("0.3", "0.1")).toBe("0.200");
    expect(subtractQty("1000000.001", "0.001")).toBe("1000000.000");
    expect(subtractQty("10", "0")).toBe("10.000");
    expect(subtractQty("0.005", "0")).toBe("0.005");
  });

  it("não devolve resultado negativo", () => {
    expect(subtractQty("1", "1.001")).toBeNull();
  });

  it("limita as casas decimais pedidas", () => {
    expect(parseQty("1,234", 3)).toBe("1.234");
    expect(parseQty("1,2345", 3)).toBeNull();
    expect(parseQty("1,2345")).toBe("1.2345");
  });

  it("reconhece quantidade positiva e formata o dia sem mudar o fuso", () => {
    expect(isPositiveQty("0.000")).toBe(false);
    expect(isPositiveQty("0.001")).toBe(true);
    expect(isPositiveQty(null)).toBe(false);
    expect(formatDay("2026-11-04")).toBe("04/11/2026");
    expect(formatDay(null)).toBe("—");
  });
});

describe("chave de acesso", () => {
  const key = VALID_KEYS[2];

  it("concorda com o backend em todos os casos do dígito verificador", () => {
    for (const valid of VALID_KEYS) {
      expect(isValidAccessKey(valid)).toBe(true);
      const wrong = valid.slice(0, 43) + String((Number(valid[43]) + 1) % 10);
      expect(isValidAccessKey(wrong)).toBe(false);
    }
  });

  it("recusa tamanho errado e letras", () => {
    expect(isValidAccessKey(key.slice(0, 43))).toBe(false);
    expect(isValidAccessKey(`${key.slice(0, 43)}A`)).toBe(false);
  });
});

describe("cabeçalho", () => {
  it("exige fornecedor e limpa documento e chave", () => {
    expect(validateHeader({ ...header, supplierName: "  " })).toEqual({ error: "Informe o fornecedor." });
    const key = VALID_KEYS[1];
    const result = validateHeader({ ...header, supplierDocument: "12.345.678/0001-90", accessKey: `${key.slice(0, 4)} ${key.slice(4)}` });
    expect(result).toMatchObject({ payload: { supplier_document: "12345678000190", access_key: key } });
  });

  it("recusa documento de tamanho errado e chave inválida", () => {
    expect(validateHeader({ ...header, supplierDocument: "123" })).toHaveProperty("error");
    expect(validateHeader({ ...header, accessKey: "123" })).toHaveProperty("error");
  });

  it("campos em branco viram nulos, e não texto vazio", () => {
    expect(validateHeader(header)).toMatchObject({ payload: { supplier_document: null, document_number: null, access_key: null, note: null } });
  });
});

describe("item", () => {
  it("calcula o aceito a partir do recebido e do rejeitado", () => {
    expect(validateItem(form({ received: "10", rejected: "2,5", reason: "damaged" })))
      .toEqual({ expected: null, received: "10", rejected: "2.5", accepted: "7.500", reason: "damaged" });
    expect(acceptedOf("10", "")).toBe("10.000");
    expect(acceptedOf("1", "2")).toBeNull();
    expect(acceptedOf("", "")).toBeNull();
  });

  it("recusa rejeitado maior que o recebido", () => {
    expect(validateItem(form({ received: "1", rejected: "2", reason: "damaged" }))).toHaveProperty("error");
  });

  it("exige motivo quando há rejeição e descarta motivo sem rejeição", () => {
    expect(validateItem(form({ rejected: "1" }))).toEqual({ error: "Informe o motivo da rejeição." });
    expect(validateItem(form({ rejected: "0", reason: "damaged" }))).toMatchObject({ reason: null });
  });

  it("não aceita produto vencido, mas permite rejeitar tudo", () => {
    expect(validateItem(form({ expiry: "2026-11-03" }))).toHaveProperty("error");
    expect(validateItem(form({ expiry: "2026-11-03", rejected: "10", reason: "expired" }))).toMatchObject({ accepted: "0.000" });
  });

  it("aceita produto que vence no próprio dia", () => {
    expect(validateItem(form({ expiry: "2026-11-04" }))).toMatchObject({ accepted: "10.000" });
  });

  it("confere fração em todas as quantidades", () => {
    const rule = { allowsFraction: false };
    expect(validateItem(form({ ...rule, received: "1,5" }))).toEqual({ error: "A unidade CX não aceita fração." });
    expect(validateItem(form({ ...rule, received: "2", rejected: "0,5", reason: "other" }))).toHaveProperty("error");
    expect(validateItem(form({ ...rule, received: "2", expected: "2,5" }))).toHaveProperty("error");
    expect(validateItem(form({ ...rule, received: "2,000" }))).toMatchObject({ accepted: "2.000" });
  });

  it("recusa mais de 3 casas e permite recebido zero para registrar falta", () => {
    expect(validateItem(form({ received: "1,2345" }))).toHaveProperty("error");
    expect(validateItem(form({ received: "0", expected: "5" }))).toMatchObject({ expected: "5", accepted: "0.000" });
  });
});

describe("chave de idempotência", () => {
  const sequence = () => {
    let n = 0;
    return () => `chave-${(n += 1)}`;
  };

  it("repete a chave ao reenviar os mesmos dados", () => {
    const key = new RequestKey(sequence());
    expect(key.for({ a: 1 })).toBe("chave-1");
    expect(key.for({ a: 1 })).toBe("chave-1");
  });

  it("troca a chave quando os dados mudam", () => {
    const key = new RequestKey(sequence());
    key.for({ a: 1 });
    expect(key.for({ a: 2 })).toBe("chave-2");
  });

  it("depois do sucesso, dados iguais são outro lançamento", () => {
    const key = new RequestKey(sequence());
    key.for({ a: 1 });
    key.reset();
    expect(key.for({ a: 1 })).toBe("chave-2");
  });
});
