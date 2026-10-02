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

    # загрузка документов проекта (TZ §4.5 А)
    max_document_size_mb: int = 500
    max_project_size_gb: int = 5
    upload_chunk_size: int = 8 * 1024**2  # байт; ≥5 МБ — минимум части S3 multipart (кроме последней)
    max_archive_entries: int = 5000
    max_archive_unpacked_gb: int = 5

    # ingest и индекс
    ocr_languages: str = "rus+kaz+eng"
    ocr_dpi: int = 300
    ocr_min_text_chars: int = 40  # меньше текста на странице PDF → считаем сканом и распознаём
    soffice_timeout_s: int = 180
    chunk_target_tokens: int = 1000  # TZ §5.1: 800–1200 токенов
    chunk_overlap_tokens: int = 150
    chars_per_token: float = 3.2  # оценка для русского текста без вызова токенизатора
    embedder: str = "fastembed"  # fastembed | hash (тесты)
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024  # размерность колонки chunks.embedding (миграция 0003)
    classify_llm_threshold: float = 0.6  # ниже — уточняем категорию через LLM

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
