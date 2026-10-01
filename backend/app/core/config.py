from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения. Значения берутся из переменных окружения (.env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_name: str = "ТехОценка"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://techocenka:techocenka@localhost:5432/techocenka"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "techocenka"
    s3_secret_key: str = "techocenka-secret"
    s3_bucket: str = "techocenka"
    s3_region: str = "us-east-1"

    jwt_secret: str = "change-me"

    anthropic_api_key: str = ""
    llm_model_analysis: str = "claude-opus-5-5"
    llm_model_extract: str = "claude-sonnet-5-5"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"

    db_connect_timeout_s: int = 5
    health_check_timeout_s: float = 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
