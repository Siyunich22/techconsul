"""Автоклассификация документов по категориям шаблона ТЗ: правила → (при низкой уверенности) LLM."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.llm.client import CallContext, LLMError, get_llm
from app.llm.prompts import render

log = logging.getLogger(__name__)

FALLBACK = "прочее"
EXCERPT_CHARS = 6000


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float
    source: str  # rules | llm
    reason: str = ""


@lru_cache
def _rules() -> dict:
    return yaml.safe_load((Path(__file__).parent / "classification.yaml").read_text(encoding="utf-8"))


def classify_by_rules(
    categories: dict[str, str], filename: str, relative_path: str, text: str, sheets: list[str] | None = None
) -> Classification:
    name = f"{relative_path or ''} {filename}".lower().replace("_", " ")
    head = text[:EXCERPT_CHARS].lower()
    scores: dict[str, float] = {}
    for code, rule in _rules().items():
        if code not in categories:
            continue
        score = 0.0
        if any(re.search(p, name) for p in rule.get("filename", [])):
            score += 3
        score += min(4, sum(1 for phrase in rule.get("content", []) if phrase in head))
        if sheets and any(re.search(p, s.lower()) for p in rule.get("sheets", []) for s in sheets):
            score += 2
        if score:
            scores[code] = score
    if not scores:
        return Classification(FALLBACK if FALLBACK in categories else next(iter(categories)), 0.0, "rules")
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    # уверенность растёт с силой сигнала и отрывом от второй категории
    confidence = min(1.0, top / 5) * (1 - runner_up / (top + runner_up) * 0.6)
    return Classification(best, round(confidence, 3), "rules")


class _LLMAnswer(BaseModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    reason: str


def classify(
    categories: dict[str, str],
    filename: str,
    relative_path: str,
    text: str,
    sheets: list[str] | None = None,
    ctx: CallContext | None = None,
) -> Classification:
    by_rules = classify_by_rules(categories, filename, relative_path, text, sheets)
    if by_rules.confidence >= get_settings().classify_llm_threshold:
        return by_rules
    try:
        system = render(
            "classify_document", categories="\n".join(f"- {k} — {v}" for k, v in categories.items())
        )
        user = f"Имя файла: {relative_path or filename}\n\nНачало текста документа:\n{text[:EXCERPT_CHARS]}"
        answer = get_llm().structured(
            ctx=ctx or CallContext(purpose="classify_document"),
            role="cheap",
            system=system,
            user=user,
            schema=_LLMAnswer,
            max_tokens=300,
        )
    except LLMError as exc:
        log.info("LLM-классификация недоступна (%s) — категория по правилам", exc)
        return by_rules
    if answer.category not in categories:
        return by_rules
    return Classification(answer.category, round(answer.confidence, 3), "llm", answer.reason)
