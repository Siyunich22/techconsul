"""Извлечение паспорта проекта (TZ §5.2): map по пакетам страниц → reduce со сведением и расхождениями.

Страницам (листам) готовых документов раздаются метки `P1…Pn`; модель ссылается на них в `refs`,
код превращает метки в «документ + страница/лист». Поле или строка без подтверждённой метки
отбрасывается (CLAUDE.md, правило 2). Сбой одного пакета не валит извлечение — он попадает в предупреждения.
"""

import json
import logging
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.client import LLM, CallContext, LLMError, LLMUnavailable, get_llm
from app.llm.prompts import render
from app.models import Document, DocumentPage, DocumentStatus, Passport, PassportStatus, Project
from app.schemas.passport import Discrepancy, Fact, PassportData, ReducedPassport, Row
from app.template_engine.loader import document_categories, parse_template

log = logging.getLogger(__name__)

QUOTE_MAX = 200
READY = (DocumentStatus.recognized, DocumentStatus.indexed)


class PassportExtractionError(Exception):
    pass


# --- страницы и метки ---


@dataclass(frozen=True)
class SourcePage:
    label: str
    document_id: uuid.UUID
    filename: str
    category: str | None
    page: int
    sheet: str | None
    text: str

    @property
    def caption(self) -> str:
        where = f"лист «{self.sheet}»" if self.sheet else f"стр. {self.page}"
        return f"«{self.filename}», {where}"

    def source_json(self) -> dict:
        return {
            "document_id": str(self.document_id),
            "filename": self.filename,
            "category": self.category,
            "page": self.page,
            "sheet": self.sheet,
        }


def collect_pages(db: Session, project: Project) -> list[SourcePage]:
    """Страницы готовых документов проекта в порядке категорий шаблона; пустые страницы пропускаются."""
    order = {
        code: i
        for i, code in enumerate(document_categories(parse_template(project.template_version.yaml_text)))
    }
    docs = list(
        db.scalars(
            select(Document).where(
                Document.project_id == project.id,
                Document.org_id == project.org_id,
                Document.deleted_at.is_(None),
                Document.status.in_(READY),
            )
        )
    )
    docs.sort(key=lambda d: (order.get(d.category or "", len(order)), d.relative_path, d.filename))
    pages: list[SourcePage] = []
    for doc in docs:
        rows = db.execute(
            select(DocumentPage.page_no, DocumentPage.sheet, DocumentPage.text)
            .where(DocumentPage.document_id == doc.id)
            .order_by(DocumentPage.page_no)
        )
        for page_no, sheet, text in rows:
            if not (text or "").strip():
                continue
            pages.append(
                SourcePage(
                    label=f"P{len(pages) + 1}",
                    document_id=doc.id,
                    filename=doc.filename,
                    category=doc.category,
                    page=page_no,
                    sheet=sheet,
                    text=text.strip(),
                )
            )
    return pages


def make_batches(pages: list[SourcePage], limit: int) -> list[str]:
    """Пакеты текста для map: страницы подряд, пока помещаются в лимит символов."""
    batches: list[str] = []
    current: list[str] = []
    size = 0
    for p in pages:
        block = f"[{p.label}] {p.caption}\n{p.text[:limit]}\n"
        if current and size + len(block) > limit:
            batches.append("\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        batches.append("\n".join(current))
    return batches


# --- очистка ответа модели: только подтверждённые источники ---


def _clean_refs(refs: Iterable[str], valid: set[str]) -> list[str]:
    seen: list[str] = []
    for r in refs:
        r = r.strip().strip("[]")
        if r in valid and r not in seen:
            seen.append(r)
    return seen


def _clean_item[T: Fact | Row](item: T, valid: set[str]) -> T | None:
    refs = _clean_refs(item.refs, valid)
    if not refs:
        return None
    return item.model_copy(update={"refs": refs, "quote": item.quote[:QUOTE_MAX], "manual": False})


def clean_passport(data: PassportData, valid: set[str]) -> PassportData:
    """Поля и строки без существующей метки-источника отбрасываются (правило 2)."""
    sections = {}
    for key in PassportData.model_fields:
        section: BaseModel = getattr(data, key)
        updates = {}
        for fkey in type(section).model_fields:
            value = getattr(section, fkey)
            if isinstance(value, Fact):
                updates[fkey] = _clean_item(value, valid)
            elif isinstance(value, list):
                updates[fkey] = [r for r in (_clean_item(row, valid) for row in value) if r is not None]
        sections[key] = section.model_copy(update=updates)
    return PassportData(**sections)


def clean_discrepancies(items: list[Discrepancy], valid: set[str]) -> list[Discrepancy]:
    result = []
    for d in items:
        values = [v.model_copy(update={"refs": _clean_refs(v.refs, valid)}) for v in d.values]
        values = [v for v in values if v.refs]
        if len(values) < 2:  # противоречие подтверждается минимум двумя источниками
            continue
        result.append(d.model_copy(update={"values": values, "resolution": ""}))
    return result


def is_empty(data: PassportData) -> bool:
    for key in PassportData.model_fields:
        section = getattr(data, key)
        for fkey in type(section).model_fields:
            if getattr(section, fkey):
                return False
    return True


def merge_partials(partials: list[PassportData]) -> PassportData:
    """Запасное сведение без LLM: поле — с наибольшей уверенностью, таблицы — объединение без дублей."""
    merged = PassportData()
    for key in PassportData.model_fields:
        target: BaseModel = getattr(merged, key)
        for fkey in type(target).model_fields:
            values = [getattr(getattr(p, key), fkey) for p in partials]
            facts = [v for v in values if isinstance(v, Fact)]
            if facts:
                setattr(target, fkey, max(facts, key=lambda f: f.confidence))
                continue
            rows: list[Row] = []
            keys: set[str] = set()
            for row_list in values:
                for row in row_list or []:
                    ident = json.dumps(
                        row.model_dump(exclude=set(Row.model_fields)), ensure_ascii=False
                    ).lower()
                    if ident not in keys:
                        keys.add(ident)
                        rows.append(row)
            if rows:
                setattr(target, fkey, rows)
    return merged


# --- извлечение ---


@dataclass
class ExtractionResult:
    data: PassportData
    discrepancies: list[Discrepancy]
    sources: dict[str, dict]
    warnings: list[str] = field(default_factory=list)


ProgressFn = Callable[[str, int, int], None]


def extract(
    pages: list[SourcePage],
    ctx: CallContext,
    llm: LLM | None = None,
    progress: ProgressFn | None = None,
) -> ExtractionResult:
    if not pages:
        raise PassportExtractionError("Нет обработанных документов с текстом — загрузите документы проекта")
    s = get_settings()
    llm = llm or get_llm()
    progress = progress or (lambda stage, done, total: None)
    valid = {p.label for p in pages}
    batches = make_batches(pages, s.passport_batch_chars)
    system = render("passport_extract")
    warnings: list[str] = []
    partials: list[PassportData] = []
    unavailable: LLMUnavailable | None = None

    progress("map", 0, len(batches))
    with ThreadPoolExecutor(max_workers=max(1, s.passport_concurrency)) as pool:
        futures = {
            pool.submit(
                llm.structured,
                ctx=ctx,
                role="extract",
                system=system,
                user=f"Фрагмент пакета документов (часть {i + 1} из {len(batches)}):\n\n{text}",
                schema=PassportData,
                max_tokens=s.passport_map_max_tokens,
            ): i
            for i, text in enumerate(batches)
        }
        for done, future in enumerate(as_completed(futures), start=1):
            i = futures[future]
            try:
                partials.append(clean_passport(future.result(), valid))
            except LLMUnavailable as exc:
                unavailable = exc
                warnings.append(f"Часть {i + 1}: ИИ недоступен ({exc})")
            except LLMError as exc:
                log.warning("Паспорт: часть %d не извлечена: %s", i + 1, exc)
                warnings.append(f"Часть {i + 1} из {len(batches)} не извлечена: {exc}")
            progress("map", done, len(batches))

    partials = [p for p in partials if not is_empty(p)]
    if not partials:
        if unavailable is not None:
            raise PassportExtractionError(f"ИИ недоступен: {unavailable}")
        if len(warnings) == len(batches):
            raise PassportExtractionError("Не удалось извлечь паспорт ни из одной части пакета")
        return ExtractionResult(PassportData(), [], {}, warnings)

    used = {p.label: p for p in pages}
    if len(partials) == 1:
        data, discrepancies = partials[0], []
    else:
        progress("reduce", 0, 1)
        try:
            reduced = llm.structured(
                ctx=ctx,
                role="extract",
                system=render(
                    "passport_reduce", sources="\n".join(f"{p.label} — {p.caption}" for p in pages)
                ),
                user="Частичные паспорта:\n\n"
                + "\n\n".join(
                    f"--- часть {i + 1} ---\n{p.model_dump_json(exclude_none=True, exclude_defaults=True)}"
                    for i, p in enumerate(partials)
                ),
                schema=ReducedPassport,
                max_tokens=s.passport_reduce_max_tokens,
            )
            data = clean_passport(reduced.passport, valid)
            discrepancies = clean_discrepancies(reduced.discrepancies, valid)
        except LLMError as exc:
            log.warning("Паспорт: сведение через ИИ не удалось: %s", exc)
            warnings.append(f"Сведение частей выполнено без ИИ, расхождения не выявлены: {exc}")
            data, discrepancies = merge_partials(partials), []
        progress("reduce", 1, 1)

    return ExtractionResult(
        data=data,
        discrepancies=discrepancies,
        sources={
            label: page.source_json()
            for label, page in used.items()
            if label in _all_refs(data, discrepancies)
        },
        warnings=warnings,
    )


def _all_refs(data: PassportData, discrepancies: list[Discrepancy]) -> set[str]:
    refs: set[str] = set()
    for key in PassportData.model_fields:
        section = getattr(data, key)
        for fkey in type(section).model_fields:
            value = getattr(section, fkey)
            if isinstance(value, Fact):
                refs.update(value.refs)
            elif isinstance(value, list):
                for row in value:
                    refs.update(row.refs)
    for d in discrepancies:
        for v in d.values:
            refs.update(v.refs)
    return refs


# --- задача: идемпотентный прогон с прогрессом ---


def _now() -> datetime:
    return datetime.now(UTC)


def run_extraction(db: Session, passport_id: uuid.UUID, run_id: uuid.UUID, llm: LLM | None = None) -> None:
    passport = db.get(Passport, passport_id)
    if passport is None or passport.run_id != run_id or passport.status != PassportStatus.extracting:
        log.info("Паспорт %s: прогон %s уже не актуален — пропуск", passport_id, run_id)
        return
    project = db.scalar(
        select(Project).where(Project.id == passport.project_id, Project.org_id == passport.org_id)
    )
    if project is None:
        return

    def progress(stage: str, done: int, total: int) -> None:
        passport.progress_json = {"stage": stage, "done": done, "total": total}
        db.commit()

    try:
        result = extract(
            collect_pages(db, project),
            CallContext(purpose="passport_extract", org_id=project.org_id, project_id=project.id),
            llm=llm,
            progress=progress,
        )
    except PassportExtractionError as exc:
        db.refresh(passport)
        if passport.run_id == run_id:
            passport.status, passport.error = PassportStatus.failed, str(exc)
            db.commit()
        return

    db.refresh(passport)
    if passport.run_id != run_id:  # пока шло извлечение, запустили новое
        return
    payload = result.data.model_dump(mode="json")
    passport.data_json = payload
    passport.ai_data_json = payload
    passport.discrepancies_json = [d.model_dump(mode="json") for d in result.discrepancies]
    passport.sources_json = result.sources
    passport.warnings_json = result.warnings
    passport.status, passport.error = PassportStatus.draft, None
    passport.extracted_at = _now()
    passport.confirmed_by = passport.confirmed_at = None
    db.commit()
