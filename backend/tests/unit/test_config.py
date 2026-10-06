from app.core.config import Settings


def test_llm_models_default_to_spec():
    s = Settings(_env_file=None)
    assert s.llm_model_analysis == "claude-opus-5-5"
    assert s.llm_model_extract == "claude-sonnet-5-5"
    assert s.llm_model_cheap == "claude-haiku-4-5-20251001"


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("LLM_MODEL_ANALYSIS", "claude-test")
    s = Settings(_env_file=None)
    assert s.database_url.endswith("@db:5432/x")
    assert s.llm_model_analysis == "claude-test"


def test_postgres_url_normalized_for_psycopg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.railway.internal:5432/railway")
    assert (
        Settings(_env_file=None).database_url == "postgresql+psycopg://u:p@db.railway.internal:5432/railway"
    )
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert Settings(_env_file=None).database_url.startswith("postgresql+psycopg://")


def test_weak_jwt_secret_rejected_outside_dev(monkeypatch):
    import pytest

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("JWT_SECRET", "change-me")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(_env_file=None)
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    assert Settings(_env_file=None).app_env == "staging"
