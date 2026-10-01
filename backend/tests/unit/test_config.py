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
