from app.core.config import get_settings
from app.integrations.erp_port import ErpAdapter, ErpNotAvailableError
from app.integrations.mock_erp import MockErpAdapter


def get_erp_adapter() -> ErpAdapter:
    """Dependência FastAPI. ``sysordem`` só será habilitado após homologação e autorização."""
    adapter = get_settings().erp_adapter
    if adapter == "mock":
        return MockErpAdapter()
    raise ErpNotAvailableError(
        "Adaptador SysOrdem ainda não implementado: requer confirmação de contrato "
        "(Swagger), homologação e autorização explícita."
    )
