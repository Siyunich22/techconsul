"""Отпечаток входных данных пункта оценки: совпал с прошлым прогоном — результат берётся оттуда без ИИ.

В отпечаток входят:
- формулировка пункта из шаблона: требование, инструменты, указания к оформлению, исходные документы;
- модель, промпты агента и схема результата;
- снимок паспорта;
- набор документов категорий из `inputs` пункта (все документы проекта, если `inputs` не задан);
- доступность веб-поиска;
- отпечатки пунктов, от которых зависит этот пункт.

Догрузили смету — меняются отпечатки пунктов, опирающихся на сметы, и выводов над ними; остальные
пункты берутся из прошлого прогона. Обзор документов в общем контексте прогона в отпечаток не входит:
он для навигации, источник вывода — фрагмент документа (D-091).
"""

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.assessment.runner import PlanItem, model_role
from app.llm.client import model_for
from app.schemas.assessment import SectionPayload

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
PROMPT_FILES = ("assess_system.md", "assess_item.md", "assess_tools.yaml", "assess_context.md")


@dataclass(frozen=True)
class DocRef:
    id: uuid.UUID
    sha256: str | None
    category: str | None


def _hash(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


@lru_cache
def _prompts_hash() -> str:
    parts = [(PROMPTS_DIR / f).read_text(encoding="utf-8") for f in PROMPT_FILES]
    return _hash([parts, SectionPayload.model_json_schema()])


def passport_hash(passport_json: dict | None) -> str:
    return _hash(passport_json or {})


def fingerprints(
    plan: Iterable[PlanItem], docs: list[DocRef], passport_json: dict | None, web_search: bool
) -> dict[str, str]:
    """Отпечатки пунктов плана (информационные пункты — без отпечатка: они бесплатны)."""
    items = {i.id: i for i in plan}
    passport = passport_hash(passport_json)
    result: dict[str, str] = {}

    def fp(item_id: str, path: tuple[str, ...] = ()) -> str:
        if item_id in result:
            return result[item_id]
        item = items[item_id]
        node = item.node
        inputs = set(node.inputs or [])
        doc_set = sorted(
            (str(d.id), d.sha256 or "") for d in docs if not inputs or (d.category or "") in inputs
        )
        deps = [fp(d, (*path, item_id)) if d not in path else "cycle" for d in item.depends_on if d in items]
        value = _hash(
            {
                "item": [item.id, item.title, item.kind, node.requirement, sorted(node.tools)],
                "hints": node.output_hints,
                "inputs": sorted(inputs),
                "conditional": node.conditional,
                "model": model_for(model_role(item)),
                "prompts": _prompts_hash(),
                "passport": passport,
                "docs": doc_set,
                "web": web_search and "web" in node.tools,
                "deps": deps,
            }
        )
        result[item_id] = value
        return value

    for item_id, item in items.items():
        if not item.is_info:
            fp(item_id)
    return {k: v for k, v in result.items() if not items[k].is_info}
