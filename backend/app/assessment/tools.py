"""Инструменты агента анализа пункта и реестр источников прогона.

Источники раздаёт код: каждый фрагмент из инструмента получает метку S12 (документ+страница/лист,
справочник+страница), агент только ссылается на метки. Веб-источники агент перечисляет сам (W1, W2…),
после ответа они получают S-метки с датой обращения.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from string import Template

import yaml
from sqlalchemy.orm import Session

from app.assessment.calc import CalcError, evaluate
from app.llm.client import AgentTool
from app.pipeline.index import search_project
from app.pipeline.references import search_references
from app.schemas.passport import Fact, PassportData

FRAGMENT_CHARS = 1500
TOOL_SPECS = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "assess_tools.yaml"


@dataclass
class SourceEntry:
    sid: str
    type: str  # doc | reference | web | project
    title: str = ""
    document_id: uuid.UUID | None = None
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    reference_doc_id: uuid.UUID | None = None
    url: str | None = None
    accessed_at: datetime | None = None


@dataclass
class SourceRegistry:
    """Метки S1…Sn прогона; потокобезопасно — пункты выполняются параллельно."""

    entries: dict[str, SourceEntry] = field(default_factory=dict)
    _keys: dict[tuple, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_entries(cls, entries: list[SourceEntry]) -> "SourceRegistry":
        reg = cls()
        for e in entries:
            reg.entries[e.sid] = e
            reg._keys[reg._key(e)] = e.sid
        return reg

    @staticmethod
    def _key(e: SourceEntry) -> tuple:
        return (e.type, e.document_id, e.page, e.sheet, e.cell_range, e.reference_doc_id, e.url, e.title)

    def add(self, entry: SourceEntry) -> str:
        with self._lock:
            key = self._key(entry)
            if key in self._keys:
                return self._keys[key]
            entry.sid = f"S{len(self.entries) + 1}"
            self.entries[entry.sid] = entry
            self._keys[key] = entry.sid
            return entry.sid

    def doc(self, document_id, filename, page=None, sheet=None, cell_range=None) -> str:
        return self.add(
            SourceEntry(
                "", "doc", filename, document_id=document_id, page=page, sheet=sheet, cell_range=cell_range
            )
        )

    def caption(self, sid: str) -> str:
        e = self.entries[sid]
        if e.type == "doc":
            where = (
                f"лист «{e.sheet}»" + (f", ячейки {e.cell_range}" if e.cell_range else "")
                if e.sheet
                else f"стр. {e.page}"
            )
            return f"«{e.title}», {where}"
        if e.type == "reference":
            return f"справочник «{e.title}», стр. {e.page}"
        if e.type == "web":
            return f"{e.title} — {e.url}"
        return e.title


@lru_cache
def _specs() -> dict:
    return yaml.safe_load(TOOL_SPECS.read_text(encoding="utf-8"))


def _tool(code: str, run: Callable[[dict], str], categories: dict[str, str]) -> AgentTool:
    spec = _specs()[code]
    rendered = Template(yaml.safe_dump(spec, allow_unicode=True)).safe_substitute(
        categories=", ".join(categories)
    )
    spec = yaml.safe_load(rendered)
    return AgentTool(spec["name"], spec["description"], spec["input_schema"], run)


@dataclass
class ToolContext:
    db_factory: Callable[[], Session]  # своя сессия на вызов: пункты выполняются в потоках
    org_id: uuid.UUID
    project_id: uuid.UUID
    passport: dict  # снимок паспорта прогона: data, discrepancies, sources
    registry: SourceRegistry
    categories: dict[str, str]
    # метки, выданные инструментами этому пункту: ссылаться можно только на них (и на источники зависимостей)
    seen: set[str] = field(default_factory=set)

    def cite(self, sid: str) -> str:
        self.seen.add(sid)
        return sid


def build_tools(codes: list[str], ctx: ToolContext) -> list[AgentTool]:
    """Клиентские инструменты пункта по кодам из YAML; паспорт доступен всегда (TZ §5.3)."""
    makers = {
        "project_docs": lambda: _tool("project_docs", lambda a: search_docs(ctx, a), ctx.categories),
        "reference": lambda: _tool("reference", lambda a: search_refs(ctx, a), ctx.categories),
        "calc": lambda: _tool("calc", run_calc, ctx.categories),
    }
    tools = [makers[c]() for c in codes if c in makers]
    tools.append(_tool("passport", lambda a: passport_text(ctx, a.get("section")), ctx.categories))
    return tools


def search_docs(ctx: ToolContext, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if len(query) < 2:
        raise ValueError("Пустой запрос")
    k = max(1, min(int(args.get("k") or 8), 12))
    categories = [c for c in args.get("categories") or [] if c in ctx.categories] or None
    with ctx.db_factory() as db:
        hits = search_project(
            db, org_id=ctx.org_id, project_id=ctx.project_id, query=query, k=k, categories=categories
        )
    if not hits:
        return "Ничего не найдено. Попробуй другую формулировку или убери фильтр категорий."
    blocks = []
    for h in hits:
        sid = ctx.cite(ctx.registry.doc(h.document_id, h.filename, h.page, h.sheet, h.cell_range))
        cat = ctx.categories.get(h.category or "", h.category or "")
        blocks.append(f"[{sid}] {ctx.registry.caption(sid)} ({cat})\n{h.text[:FRAGMENT_CHARS]}")
    return "\n\n".join(blocks)


def search_refs(ctx: ToolContext, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if len(query) < 2:
        raise ValueError("Пустой запрос")
    kinds = [args["kind"]] if args.get("kind") else None
    with ctx.db_factory() as db:
        hits = search_references(db, query, k=6, kinds=kinds)
    if not hits:
        return "В справочной библиотеке ничего не найдено (библиотека может быть не заполнена)."
    blocks = []
    for h in hits:
        ref_id, page = h["reference_doc_id"], h["page"]
        entry = SourceEntry("", "reference", h["title"], reference_doc_id=ref_id, page=page)
        sid = ctx.cite(ctx.registry.add(entry))
        blocks.append(f"[{sid}] {ctx.registry.caption(sid)}\n{h['text'][:FRAGMENT_CHARS]}")
    return "\n\n".join(blocks)


def run_calc(args: dict) -> str:
    try:
        value = evaluate(str(args.get("expression", "")))
    except CalcError as exc:
        raise ValueError(str(exc)) from exc
    return f"{args.get('expression')} = {value}"


def passport_text(ctx: ToolContext, section: str | None) -> str:
    """Паспорт текстом с S-метками: метки паспорта P12 переводятся в источники прогона."""
    data = PassportData.model_validate(ctx.passport.get("data") or {})
    sources = ctx.passport.get("sources") or {}

    def refs(labels: list[str]) -> str:
        out = []
        for label in labels:
            s = sources.get(label)
            if s:
                doc_id = uuid.UUID(s["document_id"])
                sid = ctx.cite(ctx.registry.doc(doc_id, s["filename"], s.get("page"), s.get("sheet")))
                out.append(f"[{sid}]")
        return " ".join(out) or "[источник не указан — введено экспертом]"

    lines: list[str] = []
    for key, info in PassportData.model_fields.items():
        if section and section not in (key, "discrepancies"):
            continue
        if section == "discrepancies":
            break
        block = getattr(data, key)
        part: list[str] = []
        for fkey, finfo in type(block).model_fields.items():
            value = getattr(block, fkey)
            if isinstance(value, Fact):
                part.append(f"- {finfo.title}: {value.value} {value.unit}".rstrip() + f" {refs(value.refs)}")
            elif isinstance(value, list) and value:
                part.append(f"- {finfo.title}:")
                for row in value:
                    cells = "; ".join(
                        f"{type(row).model_fields[c].title}: {v}"
                        for c, v in row.model_dump(exclude={"refs", "quote", "confidence", "manual"}).items()
                        if v
                    )
                    part.append(f"  • {cells} {refs(row.refs)}")
        if part:
            lines.append(f"## {info.title}\n" + "\n".join(part))
    if section in (None, "discrepancies"):
        items = ctx.passport.get("discrepancies") or []
        if items:
            lines.append("## Расхождения между документами")
            for d in items:
                values = "; ".join(f"{v['value']} {refs(v.get('refs', []))}" for v in d.get("values", []))
                resolution = f" Решение эксперта: {d['resolution']}" if d.get("resolution") else ""
                lines.append(f"- {d['title']}: {d['description']} Значения: {values}.{resolution}")
    return "\n\n".join(lines) or "В паспорте нет данных по этому разделу."


def now() -> datetime:
    return datetime.now(UTC)
