import { describe, expect, it } from "vitest";

import { parseVersao, situacaoVersao, type Versao } from "./versao";

const v = (commit: string): Versao => ({ versao: commit.slice(0, 7), commit, data: "", mudancas: [] });

describe("versão do app", () => {
  it("aceita só o formato publicado pelo servidor", () => {
    expect(parseVersao({ versao: "abc1234", commit: "abc1234ff", data: "2026-11-07T10:00:00-03:00",
      mudancas: ["Corrige X", 3] })).toEqual({ versao: "abc1234", commit: "abc1234ff",
      data: "2026-11-07T10:00:00-03:00", mudancas: ["Corrige X"] });
    expect(parseVersao(null)).toBeNull();
    expect(parseVersao({ versao: "x", commit: "" })).toBeNull();
    expect(parseVersao("<html>")).toBeNull();
  });

  it("avisa a versão nova no servidor antes de tudo", () => {
    expect(situacaoVersao(v("a1"), v("b2"), "a1")).toBe("nova");
    expect(situacaoVersao(v("a1"), v("b2"), null)).toBe("nova");
  });

  it("avisa uma vez a versão carregada que a pessoa ainda não viu", () => {
    expect(situacaoVersao(v("b2"), v("b2"), "a1")).toBe("atualizado");
    expect(situacaoVersao(v("b2"), null, null)).toBe("atualizado");
    expect(situacaoVersao(v("b2"), v("b2"), "b2")).toBeNull();
  });

  it("sem versão publicada não mostra nada", () => {
    expect(situacaoVersao(null, null, null)).toBeNull();
    expect(situacaoVersao(null, v("b2"), null)).toBeNull();
  });
});
