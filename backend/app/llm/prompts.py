"""Промпты — файлы backend/app/llm/prompts/*.md с переменными $name (string.Template)."""

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache
def _load(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))


def render(name: str, **variables: object) -> str:
    """Подставляет переменные; отсутствующая переменная — ошибка (KeyError), а не тихий пропуск."""
    return _load(name).substitute({k: str(v) for k, v in variables.items()})
