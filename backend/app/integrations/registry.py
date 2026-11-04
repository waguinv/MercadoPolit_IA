"""Catálogo de sistemas que a plataforma sabe ler.

Para integrar um novo sistema: crie o adaptador em ``app/integrations/<sistema>/`` seguindo
``ErpAdapter`` (só leitura) e acrescente uma entrada em ``CONNECTORS``. A tela de Configuração passa
a oferecê-lo sozinha. Passo a passo em docs/INTEGRACOES.md.

A URL de cada sistema vem do ambiente, nunca do cliente, para evitar SSRF.
"""
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import get_settings
from app.integrations.erp_port import ErpAdapter
from app.integrations.sysordem.adapter import SysordemErpAdapter


@dataclass(frozen=True)
class Connector:
    kind: str  # gravado em erp_connections.kind; até 20 caracteres
    name: str
    token_label: str
    token_help: str
    build: Callable[[str], ErpAdapter]


def _build_sysordem(token: str) -> ErpAdapter:
    s = get_settings()
    return SysordemErpAdapter(s.sysordem_base_url, token, timeout=s.sysordem_timeout_seconds,
                              page_size=s.sysordem_page_size)


CONNECTORS: dict[str, Connector] = {
    c.kind: c for c in (
        Connector(
            kind="sysordem",
            name="SysOrdem",
            token_label="Token da API do SysOrdem",
            token_help="Use o token da empresa. O MercadoPilot só faz leituras no SysOrdem.",
            build=lambda token: _build_sysordem(token),
        ),
    )
}


def get_connector(kind: str) -> Connector | None:
    return CONNECTORS.get(kind)
