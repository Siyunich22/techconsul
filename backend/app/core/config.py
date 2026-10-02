from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения. Значения берутся из переменных окружения (.env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_name: str = "ТехОценка"
    api_prefix: str = "/api/v1"
    public_url: str = "http://localhost:3000"  # для ссылок в письмах

    database_url: str = "postgresql+psycopg://techocenka:techocenka@localhost:5432/techocenka"
    test_database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "techocenka"
    s3_secret_key: str = "techocenka-secret"
    s3_bucket: str = "techocenka"
    s3_region: str = "us-east-1"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 14
    reset_token_ttl_hours: int = 2
    invite_token_ttl_days: int = 7
    cookie_secure: bool = False

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "ТехОценка <noreply@techocenka.local>"

    templates_dir: Path = Path(__file__).resolve().parents[3] / "templates"
    default_template_code: str = "tech_assessment_bank_v1"

    max_cv_size_mb: int = 20
    max_logo_size_mb: int = 5
    max_reference_docx_size_mb: int = 20

    anthropic_api_key: str = ""
    llm_model_analysis: str = "claude-opus-5-5"
    llm_model_extract: str = "claude-sonnet-5-5"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"

    db_connect_timeout_s: int = 5
    health_check_timeout_s: float = 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
