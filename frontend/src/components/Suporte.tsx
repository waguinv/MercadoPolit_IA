import { Component, useEffect, useState, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { ROLE } from "../lib/format";
import { codigoCompleto, informacoesSuporte, novoCodigoErro, telaDoEndereco, type Tela } from "../lib/telas";
import type { User } from "../lib/types";
import { versaoCarregada, type Versao } from "../lib/versao";

function useVersao(): Versao | null {
  const [versao, setVersao] = useState<Versao | null>(null);
  useEffect(() => {
    let ativo = true;
    void versaoCarregada().then((v) => { if (ativo) setVersao(v); });
    return () => { ativo = false; };
  }, []);
  return versao;
}

function textoSuporte(tela: Tela | null, versao: Versao | null, user: User | null, erro?: string) {
  return informacoesSuporte({
    tela,
    versao: versao?.versao ?? null,
    commit: versao?.commit ?? null,
    organizacao: user?.organization_id ?? null,
    loja: user?.store_id ?? null,
    perfil: user ? (ROLE[user.role] ?? user.role) : "sem login",
    quando: new Date(),
    erro,
  });
}

async function copiar(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    // navegador sem permissão para a área de transferência (alguns celulares): cópia pela seleção de texto
    const campo = document.createElement("textarea");
    campo.value = texto;
    campo.setAttribute("readonly", "");
    campo.style.position = "fixed";
    campo.style.opacity = "0";
    document.body.appendChild(campo);
    campo.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      campo.remove();
    }
  }
}

type Copia = "copiado" | "falhou" | null;

function AvisoCopia({ estado, codigo }: { estado: Copia; codigo: string }) {
  if (!estado) return null;
  return (
    <span className="selo-tela__aviso" role="status">
      {estado === "copiado"
        ? "Informações copiadas. Cole na conversa com o suporte."
        : `Não foi possível copiar. Informe ao suporte o código ${codigo}.`}
    </span>
  );
}

/** Selo "Tela MPI-INV-02 · v1.0.0" no rodapé; o clique copia as informações para o suporte. */
export function SeloTela() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const versao = useVersao();
  const [copia, setCopia] = useState<Copia>(null);
  const tela = telaDoEndereco(pathname);
  const codigo = tela ? codigoCompleto(tela) : "—";

  useEffect(() => {
    if (!copia) return;
    const t = window.setTimeout(() => setCopia(null), 5000);
    return () => window.clearTimeout(t);
  }, [copia]);

  return (
    <footer className="selo-tela">
      <button
        type="button"
        className="selo-tela__botao"
        data-tela={codigo}
        title="Copiar informações para o suporte"
        onClick={async () => setCopia((await copiar(textoSuporte(tela, versao, user))) ? "copiado" : "falhou")}
      >
        Tela {codigo} · v{versao?.versao ?? "—"}
      </button>
      <AvisoCopia estado={copia} codigo={codigo} />
    </footer>
  );
}

function TelaDeErro({ codigoErro }: { codigoErro: string }) {
  const { user } = useAuth();
  const versao = useVersao();
  const [copia, setCopia] = useState<Copia>(null);
  // fora do roteador de propósito: a tela de erro não pode depender do que quebrou
  const tela = user ? telaDoEndereco(window.location.pathname) : null;
  const codigo = tela ? codigoCompleto(tela) : "sem código";
  return (
    <main className="content tela-erro" role="alert">
      <div className="card">
        <h2>Algo deu errado nesta tela</h2>
        <p>Os dados já salvos não foram perdidos. Tente de novo; se o problema continuar, chame o suporte com o código abaixo.</p>
        <p className="tela-erro__codigo">Tela {codigo} · código do erro {codigoErro}</p>
        <div className="tela-erro__acoes">
          <button type="button" className="primary" onClick={() => window.location.reload()}>Tentar de novo</button>
          <button type="button" onClick={() => { window.location.href = "/"; }}>Voltar ao início</button>
          <button
            type="button"
            onClick={async () => setCopia((await copiar(textoSuporte(tela, versao, user, codigoErro))) ? "copiado" : "falhou")}
          >
            Copiar informações para o suporte
          </button>
        </div>
        <AvisoCopia estado={copia} codigo={codigoErro} />
      </div>
    </main>
  );
}

/** Erro inesperado ao desenhar uma tela: mostra o código do erro em vez de uma tela em branco. */
export class ErroDaTela extends Component<{ children: ReactNode }, { codigo: string | null }> {
  state = { codigo: null as string | null };

  static getDerivedStateFromError(): { codigo: string } {
    return { codigo: novoCodigoErro() };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // só o nome do erro e onde aconteceu: a mensagem pode trazer dados digitados
    const nome = error instanceof Error ? error.name : typeof error;
    const tela = telaDoEndereco(window.location.pathname);
    console.error(`[suporte] erro ${this.state.codigo} na tela ${tela ? codigoCompleto(tela) : "sem código"}: ${nome}`,
      info.componentStack?.split("\n").slice(0, 4).join("\n"));
  }

  render() {
    return this.state.codigo ? <TelaDeErro codigoErro={this.state.codigo} /> : this.props.children;
  }
}
