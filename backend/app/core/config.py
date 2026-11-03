"""Configuração via variáveis de ambiente. Nenhum segredo real é versionado."""
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRET = "change-me-with-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = _PLACEHOLDER_SECRET
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    database_url: str = "postgresql+psycopg://app:change-me@localhost:5432/mercadopilot"
    redis_url: str = "redis://localhost:6379/0"
    # Chave Fernet para cifrar tokens de ERP no banco. Gerar com scripts/generate_secrets_key.py
    secrets_encryption_key: str | None = None
    # "mock" (padrão): todas as organizações usam o ERP simulado.
    # "sysordem": cada organização usa o PRÓPRIO token (tabela erp_connections).
    erp_adapter: str = "mock"
    # URL vem só do ambiente (nunca de dados do cliente), para evitar SSRF.
    sysordem_base_url: str = "https://api.meuerponline.com.br/publica"
    sysordem_timeout_seconds: float = 15.0
    sysordem_page_size: int = 100

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.app_env not in ("development", "test") and (
            self.secret_key == _PLACEHOLDER_SECRET or len(self.secret_key) < 32
        ):
            raise ValueError("SECRET_KEY forte (>= 32 caracteres) é obrigatória fora de dev/test.")
        if self.erp_adapter not in ("mock", "sysordem"):
            raise ValueError("ERP_ADAPTER deve ser mock ou sysordem.")
        if self.erp_adapter == "sysordem":
            try:
                Fernet((self.secrets_encryption_key or "").encode())
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "ERP_ADAPTER=sysordem exige SECRETS_ENCRYPTION_KEY válida (chave Fernet)."
                ) from exc
            if not self.sysordem_base_url.startswith("https://") and self.app_env != "development":
                raise ValueError("SYSORDEM_BASE_URL deve usar https fora de desenvolvimento.")
        if not 1 <= self.sysordem_page_size <= 500:
            raise ValueError("SYSORDEM_PAGE_SIZE deve estar entre 1 e 500.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
