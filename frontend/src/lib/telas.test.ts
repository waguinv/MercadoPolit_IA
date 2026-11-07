import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { codigoCompleto, informacoesSuporte, novoCodigoErro, TELAS, telaDoEndereco } from "./telas";

const MAIN = readFileSync(new URL("../main.tsx", import.meta.url), "utf-8");
const rotasDoApp = [...MAIN.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1]!).filter((r) => r !== "*");

describe("catálogo de telas", () => {
  it("toda rota do app tem código", () => {
    expect(rotasDoApp.length).toBeGreaterThan(0);
    const semCodigo = rotasDoApp.filter((r) => !TELAS.some((t) => t.rota === r));
    expect(semCodigo).toEqual([]);
  });

  it("não há tela do catálogo apontando para rota que não existe", () => {
    const orfas = TELAS.filter((t) => t.rota !== null && !rotasDoApp.includes(t.rota));
    expect(orfas).toEqual([]);
  });

  it("códigos no formato MOD-NN e sem repetição", () => {
    for (const t of TELAS) expect(t.codigo).toMatch(/^[A-Z]{3}-\d{2}$/);
    expect(new Set(TELAS.map((t) => t.codigo)).size).toBe(TELAS.length);
  });

  it("código completo leva a sigla da aplicação", () => {
    expect(codigoCompleto(telaDoEndereco("/sessoes/abc")!)).toBe("MPI-INV-02");
  });
});

describe("tela do endereço", () => {
  it("reconhece rotas fixas, com parâmetro e com barra no fim", () => {
    expect(telaDoEndereco("/")?.codigo).toBe("INV-01");
    expect(telaDoEndereco("/sessoes/123")?.codigo).toBe("INV-02");
    expect(telaDoEndereco("/sessoes/123/itens/9")?.codigo).toBe("INV-03");
    expect(telaDoEndereco("/recebimentos/")?.codigo).toBe("REC-01");
    expect(telaDoEndereco("/recebimentos/77?aba=x")?.codigo).toBe("REC-02");
    expect(telaDoEndereco("/configuracao")?.codigo).toBe("CFG-01");
  });

  it("endereço desconhecido fica sem código", () => {
    expect(telaDoEndereco("/nao-existe")).toBeNull();
    expect(telaDoEndereco("/sessoes/")).toBeNull();
  });
});

describe("informações para o suporte", () => {
  const base = {
    versao: "1.0.0", commit: "63f762cabcdef", organizacao: "org-1", loja: null,
    perfil: "Operador", quando: new Date(2026, 10, 7, 9, 30),
  };

  it("leva o código completo e a rota modelo, nunca o id do registro", () => {
    const texto = informacoesSuporte({ ...base, tela: telaDoEndereco("/sessoes/5f1c-segredo")! });
    expect(texto).toContain("Tela: MPI-INV-02 · Sessão de contagem");
    expect(texto).toContain("Endereço: /sessoes/:sessionId");
    expect(texto).toContain("Versão: 1.0.0 (63f762c)");
    expect(texto).toContain("Loja: todas");
    expect(texto).not.toContain("5f1c-segredo");
  });

  it("inclui o código do erro quando houver", () => {
    const texto = informacoesSuporte({ ...base, tela: null, erro: "a1b2c3d4" });
    expect(texto).toContain("Tela: sem código");
    expect(texto).toContain("Código do erro: a1b2c3d4");
  });

  it("código do erro tem 8 caracteres hexadecimais", () => {
    expect(novoCodigoErro()).toMatch(/^[0-9a-f]{8}$/);
  });
});
