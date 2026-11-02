from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.catalog import erp_http_error
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.integrations.erp_port import ErpError


def create_app() -> FastAPI:
    settings = get_settings()  # valida configuração na inicialização
    app = FastAPI(
        title="MercadoPilot AI",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(ErpError)
    async def _erp_error(_: Request, exc: ErpError) -> JSONResponse:
        # Cobre erros levantados fora das rotas (ex.: fábrica do adaptador).
        http = erp_http_error(exc)
        return JSONResponse({"detail": http.detail}, status_code=http.status_code)

    app.include_router(api_router)
    return app


app = create_app()
