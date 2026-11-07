import { useEffect, useState } from "react";

import { formatDate } from "../lib/format";
import {
  buscarVersao, lerVista, marcarVista, situacaoVersao, versaoCarregada, type Versao,
} from "../lib/versao";

const INTERVALO_MS = 5 * 60 * 1000;

/** Avisa quando o app foi atualizado no servidor e mostra o que mudou. */
export function AvisoVersao() {
  const [carregada, setCarregada] = useState<Versao | null>(null);
  const [noServidor, setNoServidor] = useState<Versao | null>(null);
  const [vista, setVista] = useState(lerVista);

  useEffect(() => {
    let ativo = true;
    void versaoCarregada().then((v) => { if (ativo) setCarregada(v); });
    const conferir = () => void buscarVersao().then((v) => { if (ativo && v) setNoServidor(v); });
    const aoVoltar = () => { if (document.visibilityState === "visible") conferir(); };
    const timer = window.setInterval(conferir, INTERVALO_MS);
    document.addEventListener("visibilitychange", aoVoltar);
    return () => {
      ativo = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", aoVoltar);
    };
  }, []);

  const situacao = situacaoVersao(carregada, noServidor, vista);
  if (situacao === "nova" && noServidor) {
    return (
      <div className="notice ok aviso-versao" role="status">
        <p><strong>Nova versão disponível ({noServidor.versao}).</strong> Termine o que está digitando e atualize.</p>
        <button type="button" className="primary" onClick={() => window.location.reload()}>Atualizar agora</button>
      </div>
    );
  }
  if (situacao === "atualizado" && carregada) {
    return (
      <div className="notice ok aviso-versao" role="status">
        <p>
          <strong>App atualizado para a versão {carregada.versao}</strong>
          {carregada.data && <>, de {formatDate(carregada.data)}</>}.
        </p>
        {carregada.mudancas.length > 0 && (
          <ul>{carregada.mudancas.map((m) => <li key={m}>{m}</li>)}</ul>
        )}
        <button type="button" onClick={() => { marcarVista(carregada.commit); setVista(carregada.commit); }}>
          Entendi
        </button>
      </div>
    );
  }
  return null;
}
