"""Configuração via variáveis de ambiente. Nenhum segredo real é versionado."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRET = "change-me-with-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = _PLACEHOLDER_SECRET
    access_token_expire_minutes: int = 30
    database_url: str = "postgresql+psycopg://app:change-me@localhost:5432/mercadopilot"
    redis_url: str = "redis://localhost:6379/0"
    erp_adapter: str = "mock"
    sysordem_base_url: str = "https://example.invalid"
    sysordem_client_id: str = "replace-me"
    sysordem_client_secret: str = "replace-me"

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.app_env not in ("development", "test") and (
            self.secret_key == _PLACEHOLDER_SECRET or len(self.secret_key) < 32
        ):
            raise ValueError("SECRET_KEY forte (>= 32 caracteres) é obrigatória fora de dev/test.")
        if self.erp_adapter not in ("mock", "sysordem"):
            raise ValueError("ERP_ADAPTER deve ser mock ou sysordem.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
