"""Confere se as leituras básicas de um sistema funcionam, para mostrar na tela de Configuração.

Não lê o catálogo de produtos aqui: ele pode ser grande e gastaria o limite de requisições do ERP.
"""
from dataclasses import dataclass

from app.integrations.erp_port import ErpAdapter, ErpError


@dataclass(frozen=True)
class ReadCheck:
    name: str
    ok: bool
    detail: str


def run_read_checks(erp: ErpAdapter) -> list[ReadCheck]:
    checks = []
    for name, read in (("Unidades", erp.list_units), ("Locais de estoque", erp.list_stock_locations)):
        try:
            checks.append(ReadCheck(name, True, f"{len(read())} encontrado(s)"))
        except ErpError as exc:  # As mensagens são nossas e nunca trazem o token.
            checks.append(ReadCheck(name, False, str(exc)))
    return checks
