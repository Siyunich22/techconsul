"""Оркестратор оценки (TZ §5.3): пункты шаблона по зависимостям, параллельно, сбой пункта не валит прогон.

Пункт выполняется агентом с инструментами из YAML (`tools`) либо, для информационных пунктов
(`source:`/`render:` в YAML), собирается из данных проекта без ИИ. Результат проходит проверку
правила 2 и сверку ссылок на источники с реестром прогона.
"""

import logging
import re
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assessment.tools import SourceEntry, SourceRegistry, ToolContext, build_tools
from app.core.config import get_settings
from app.llm.client import LLM, CallContext, LLMError, get_llm
from app.llm.prompts import render
from app.models import (
    AssessmentRun,
    Document,
    DocumentStatus,
    ItemStatus,
    LlmCall,
    Organization,
    Project,
    ProjectMember,
    ProjectStatus,
    ResultAuthor,
    RunStatus,
    SectionResult,
    Source,
)
from app.schemas.assessment import SOURCE_REF, Finding, Gap, ResultTable, SectionPayload, SourceRef
from app.template_engine.loader import document_categories, parse_template
from app.template_engine.tree import TreeNode, build_tree

log = logging.getLogger(__name__)

DbFactory = Callable[[], Session]


# --- план: пункты шаблона с зависимостями ---


@dataclass(frozen=True)
class PlanItem:
    id: str
    title: str
    kind: str | None
    section: str  # «3.4 Оценка проектного оборудования»
    node: TreeNode
    depends_on: tuple[str, ...]

    @property
    def is_info(self) -> bool:
        return self.kind == "info"


def plan_items(template: dict, enabled_optional: list[str]) -> list[PlanItem]:
    """Пункты (узлы-items шаблона) включённых разделов. Разделы-целиком (риски 4, выводы 5, рендер 1/6)
    — не пункты анализа: их готовят фаза 6 и сборка отчёта."""
    result: list[PlanItem] = []

    def visit(node: TreeNode, section: str) -> None:
        if not node.enabled:
            return
        if node.type == "item":
            result.append(PlanItem(node.id, node.title, node.kind, section, node, tuple(node.depends_on)))
            return
        for child in node.children:
            visit(child, f"{node.id} {node.title}" if node.children and child.type == "item" else section)

    for top in build_tree(template, enabled_optional):
        visit(top, f"{top.id} {top.title}")
    ids = {i.id for i in result}
    # зависимости на выключенные/не-пункты (например, «3.*» включает выключенный 3.6) отбрасываем
    return [
        PlanItem(i.id, i.title, i.kind, i.section, i.node, tuple(d for d in i.depends_on if d in ids))
        for i in result
    ]


# --- выполнение одного пункта ---


@dataclass(frozen=True)
class RunInfo:
    """Снимок прогона и проекта: пункты выполняются в потоках, ORM-объекты между сессиями не передаём."""

    run_id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    web_search: bool
    project_name: str
    industry: str
    region: str


@dataclass
class ItemContext:
    info: RunInfo
    item: PlanItem
    instruction: str
    deps: dict[str, SectionPayload | None]  # None — зависимость не выполнена
    registry: SourceRegistry
    tools: ToolContext
    llm: LLM


@dataclass
class ItemOutcome:
    payload: SectionPayload | None
    author: ResultAuthor = ResultAuthor.ai
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _hints(node: TreeNode) -> str:
    hints = node.output_hints or {}
    parts = []
    for table in hints.get("tables", []):
        parts.append(f"Обязательная таблица: {table}.")
    if hints.get("min_alternatives"):
        parts.append(
            f"Не менее {hints['min_alternatives']} альтернатив для каждой единицы основного оборудования."
        )
    return "\nУказания к оформлению: " + " ".join(parts) + "\n" if parts else ""


def _deps_text(ctx: ItemContext) -> str:
    if not ctx.deps:
        return ""
    lines = ["Результаты пунктов, на которые опирается этот пункт (ссылки [S..] в них можно использовать):"]
    for dep_id, p in ctx.deps.items():
        if p is None:
            lines.append(f"- Пункт {dep_id}: не выполнен (ошибка) — учти отсутствие результата.")
            continue
        facts = "; ".join(f"{f.text} {' '.join(f'[{s}]' for s in f.source_ids)}" for f in p.findings[:12])
        lines.append(
            f"- Пункт {dep_id} ({p.disclosure_status}): {p.summary}"
            + (f" Факты: {facts}." if facts else "")
            + (f" Замечания: {'; '.join(p.remarks[:8])}." if p.remarks else "")
            + (f" Допущения: {'; '.join(p.assumptions[:8])}." if p.assumptions else "")
            + (f" Недостаток данных: {p.gap.reason}" if p.gap else "")
        )
    return "\n".join(lines) + "\n"


def _user_prompt(ctx: ItemContext) -> str:
    node, item, info = ctx.item.node, ctx.item, ctx.info
    inputs = ""
    if node.inputs:
        names = [ctx.tools.categories.get(c, c) for c in node.inputs]
        inputs = "\nОсновные исходные документы для пункта: " + ", ".join(names) + ".\n"
    return render(
        "assess_item",
        project_name=info.project_name,
        industry=info.industry or "не указана",
        region=info.region or "не указан",
        section=item.section,
        item_id=item.id,
        item_title=item.title,
        requirement=node.requirement or item.title,
        hints=_hints(node),
        conditional=f"\nПункт условный: выполняется, если {node.conditional}.\n" if node.conditional else "",
        inputs_note=inputs,
        instruction=f"\nДополнительное указание эксперта: {ctx.instruction}\n" if ctx.instruction else "",
        dependencies=_deps_text(ctx),
    )


def server_tools(item: PlanItem, web_allowed: bool) -> list[dict]:
    if not web_allowed or "web" not in item.node.tools:
        return []
    s = get_settings()
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": s.analysis_web_search_max_uses},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": s.analysis_web_fetch_max_uses},
    ]


def run_agent(ctx: ItemContext) -> ItemOutcome:
    s = get_settings()
    tools = build_tools(list(ctx.item.node.tools) or ["project_docs"], ctx.tools)
    payload = ctx.llm.agent(
        ctx=CallContext(
            purpose=f"assess:{ctx.item.id}",
            org_id=ctx.info.org_id,
            project_id=ctx.info.project_id,
            run_id=ctx.info.run_id,
        ),
        role="analysis",
        system=render("assess_system"),
        user=_user_prompt(ctx),
        tools=tools,
        server_tools=server_tools(ctx.item, ctx.info.web_search),
        schema=SectionPayload,
        max_turns=s.analysis_max_tool_turns,
        max_tokens=s.analysis_max_tokens,
    )
    payload, warnings = finalize(payload, ctx.item, ctx.registry)
    return ItemOutcome(payload, ResultAuthor.ai, warnings)


def finalize(
    payload: SectionPayload, item: PlanItem, registry: SourceRegistry
) -> tuple[SectionPayload, list[str]]:
    """Веб-источники W1… → S-метки с датой обращения; ссылки на несуществующие метки удаляются
    с предупреждением; повторная проверка правила 2."""
    warnings: list[str] = []
    mapping: dict[str, str] = {}
    for s in payload.sources:
        if s.type == "web" or s.id.startswith("W"):
            if not s.url:
                warnings.append(f"Веб-источник {s.id} без URL — исключён")
                continue
            mapping[s.id] = registry.add(
                SourceEntry("", "web", s.title or s.url, url=s.url, accessed_at=_now())
            )

    def remap(sid: str) -> str:
        return mapping.get(sid, sid)

    text = re.sub(r"\[([WS]\d+)\]", lambda m: f"[{remap(m.group(1))}]", payload.analysis_md)
    findings = [Finding(text=f.text, source_ids=[remap(x) for x in f.source_ids]) for f in payload.findings]
    listed = {remap(s.id) for s in payload.sources if s.id in mapping or s.id.startswith("S")}
    cited = set(SOURCE_REF.findall(text)) | {x for f in findings for x in f.source_ids}
    unknown = sorted((cited | listed) - set(registry.entries))
    if unknown:
        warnings.append(f"Ссылки на несуществующие источники удалены: {', '.join(unknown)}")
        text = re.sub(r"\s?\[(S\d+)\]", lambda m: "" if m.group(1) in unknown else m.group(0), text)
        findings = [
            Finding(text=f.text, source_ids=[x for x in f.source_ids if x not in unknown]) for f in findings
        ]
    used = sorted(((cited | listed) - set(unknown)), key=lambda x: int(x[1:]))
    sources = [_source_ref(registry, sid) for sid in used]
    data = payload.model_dump()
    data.update(item_id=item.id, analysis_md=text, findings=[f.model_dump() for f in findings])
    data["sources"] = [s.model_dump() for s in sources]
    hints = item.node.output_hints or {}
    if hints.get("tables") and not payload.tables and payload.disclosure_status in ("full", "partial"):
        warnings.append("Пункт требует таблицу (output_hints), а таблиц в результате нет")
    try:
        result = SectionPayload.model_validate(data)
        result.check_citations()
        return result, warnings
    except (ValidationError, ValueError) as exc:
        raise LLMError(f"Результат не прошёл проверку после сверки источников: {exc}") from exc


def _source_ref(registry: SourceRegistry, sid: str) -> SourceRef:
    e = registry.entries[sid]
    return SourceRef(
        id=sid,
        type=e.type,  # type: ignore[arg-type]
        url=e.url,
        title=registry.caption(sid),
        accessed_at=e.accessed_at.date().isoformat() if e.accessed_at else None,
    )


# --- информационные пункты из данных проекта (ключи source/render в YAML) ---


def info_item(db: Session, info: RunInfo, item: PlanItem, registry: SourceRegistry) -> SectionPayload:
    project = db.scalar(select(Project).where(Project.id == info.project_id, Project.org_id == info.org_id))
    raw = _raw_node(project, item.id)
    kind = raw.get("render") or raw.get("source") or ""
    if kind == "documents_table":
        return _documents_table(db, project, item, registry)
    if "independence" in kind:
        return _customer_and_executor(db, project, item, registry)
    if "members" in kind:
        return _experts(db, project, item, registry)
    return SectionPayload(
        item_id=item.id,
        disclosure_status="not_disclosed",
        summary="Пункт формируется при сборке отчёта.",
        analysis_md="",
        gap=Gap(
            reason=f"Источник данных «{kind}» не поддерживается на этапе анализа",
            impact_on_conclusions="Не влияет на аналитические выводы",
            assumptions="Пункт заполняется при сборке отчёта",
            recommendation="Проверить пункт в готовом отчёте",
        ),
        confidence=1,
    )


def _raw_node(project: Project, item_id: str) -> dict:
    template = parse_template(project.template_version.yaml_text)

    def find(nodes: list[dict]) -> dict | None:
        for n in nodes:
            if str(n.get("id")) == item_id:
                return n
            found = find(n.get("subsections") or n.get("items") or [])
            if found:
                return found
        return None

    return find(template["sections"]) or {}


def _documents_table(
    db: Session, project: Project, item: PlanItem, registry: SourceRegistry
) -> SectionPayload:
    categories = document_categories(parse_template(project.template_version.yaml_text))
    docs = list(
        db.scalars(
            select(Document)
            .where(
                Document.project_id == project.id,
                Document.org_id == project.org_id,
                Document.deleted_at.is_(None),
                Document.status.in_((DocumentStatus.recognized, DocumentStatus.indexed)),
            )
            .order_by(Document.category, Document.filename)
        )
    )
    if not docs:
        return _no_data(item, "Документы проекта не загружены", "Загрузить исходные документы проекта")
    rows, sids = [], []
    for n, d in enumerate(docs, start=1):
        sid = registry.doc(d.id, d.filename, 1)
        sids.append(sid)
        rows.append(
            [
                str(n),
                d.filename,
                categories.get(d.category or "", d.category or "—"),
                str(d.pages or "—"),
                f"[{sid}]",
            ]
        )
    return SectionPayload(
        item_id=item.id,
        disclosure_status="full",
        summary=f"Оценка проведена на основании {len(docs)} документов, представленных Заказчиком.",
        analysis_md="Перечень исходных документов приведён в таблице.",
        tables=[
            ResultTable(
                title="Исходные документы",
                columns=["№", "Документ", "Категория", "Страниц", "Источник"],
                rows=rows,
            )
        ],
        sources=[_source_ref(registry, s) for s in sids],
        confidence=1,
    )


def _customer_and_executor(
    db: Session, project: Project, item: PlanItem, registry: SourceRegistry
) -> SectionPayload:
    org = db.get(Organization, project.org_id)
    ind = project.independence_json or {}
    sid = registry.add(
        SourceEntry("", "project", "Карточка проекта на платформе: сведения о Заказчике и независимости")
    )
    yes_no = {True: "да", False: "нет", None: "не указано"}
    lines = [
        f"Заказчик: {project.customer_name}"
        + (f", БИН {project.customer_bin}" if project.customer_bin else "")
        + f" [{sid}].",
        f"Исполнитель: {org.name if org else '—'}"
        + (f", БИН {org.bin}" if org and org.bin else "")
        + f" [{sid}].",
        f"Аффилированность Исполнителя с Заказчиком: {yes_no.get(ind.get('affiliated'))}; "
        f"участие Исполнителя в разработке документации: {yes_no.get(ind.get('participated_in_docs'))}; "
        f"Исполнитель — поставщик по проекту: {yes_no.get(ind.get('is_supplier'))} [{sid}].",
    ]
    if ind.get("justification"):
        lines.append(f"Пояснение: {ind['justification']} [{sid}].")
    lines.append(f"Опыт совместной деятельности: {ind.get('joint_experience') or 'не указан'} [{sid}].")
    complete = all(ind.get(k) is not None for k in ("affiliated", "participated_in_docs", "is_supplier"))
    return SectionPayload(
        item_id=item.id,
        disclosure_status="full" if complete else "partial",
        summary="Сведения о Заказчике, Исполнителе и их независимости приведены по данным карточки проекта."
        if complete
        else "Блок независимости заполнен не полностью.",
        analysis_md="\n\n".join(lines),
        remarks=[] if complete else ["Заполните блок «Независимость» в карточке проекта (п. 6.4 ТЗ банка)."],
        sources=[_source_ref(registry, sid)],
        confidence=1,
    )


def _experts(db: Session, project: Project, item: PlanItem, registry: SourceRegistry) -> SectionPayload:
    members = list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id)))
    if not members:
        return _no_data(
            item, "Команда экспертов проекта не сформирована", "Назначить экспертов во вкладке «Команда»"
        )
    sid = registry.add(SourceEntry("", "project", "Карточка проекта на платформе: команда экспертов"))
    rows = [
        [
            m.expert.full_name,
            ", ".join(m.expert.specialization) or "—",
            m.expert.education or "—",
            m.expert.research_experience or "—",
            ", ".join(x for x in (m.expert.email, m.expert.phone, m.expert.contacts) if x) or "—",
        ]
        for m in members
    ]
    return SectionPayload(
        item_id=item.id,
        disclosure_status="full",
        summary=f"В исследовании задействовано экспертов: {len(members)} [{sid}].",
        analysis_md=f"Состав экспертов приведён в таблице [{sid}].",
        tables=[
            ResultTable(
                title="Состав экспертов",
                columns=["ФИО", "Специализация", "Образование", "Исследовательский опыт", "Контакты"],
                rows=rows,
            )
        ],
        sources=[_source_ref(registry, sid)],
        confidence=1,
    )


def _no_data(item: PlanItem, reason: str, recommendation: str) -> SectionPayload:
    return SectionPayload(
        item_id=item.id,
        disclosure_status="not_disclosed",
        summary=reason + ".",
        analysis_md="",
        gap=Gap(
            reason=reason,
            impact_on_conclusions="Пункт отчёта не может быть заполнен",
            assumptions="Нет",
            recommendation=recommendation,
        ),
        confidence=1,
    )


# --- оркестратор ---


@contextmanager
def _session(factory: DbFactory):
    with factory() as db:
        yield db


def _registry(db: Session, run_id: uuid.UUID) -> SourceRegistry:
    rows = db.scalars(select(Source).where(Source.run_id == run_id)).all()
    entries = [
        SourceEntry(
            r.sid,
            r.type,
            r.title,
            r.document_id,
            r.page,
            r.sheet,
            r.cell_range,
            r.reference_doc_id,
            r.url,
            r.accessed_at,
        )
        for r in rows
    ]
    return SourceRegistry.from_entries(sorted(entries, key=lambda e: int(e.sid[1:])))


def _persist_sources(db: Session, run: AssessmentRun, registry: SourceRegistry) -> None:
    existing = set(db.scalars(select(Source.sid).where(Source.run_id == run.id)))
    for sid, e in list(registry.entries.items()):
        if sid in existing:
            continue
        db.add(
            Source(
                org_id=run.org_id,
                project_id=run.project_id,
                run_id=run.id,
                sid=sid,
                type=e.type,
                document_id=e.document_id,
                page=e.page,
                sheet=e.sheet,
                cell_range=e.cell_range,
                reference_doc_id=e.reference_doc_id,
                url=e.url,
                title=e.title,
                accessed_at=e.accessed_at,
            )
        )


def _latest(db: Session, run_id: uuid.UUID) -> dict[str, SectionResult]:
    rows = db.scalars(
        select(SectionResult).where(SectionResult.run_id == run_id).order_by(SectionResult.version)
    ).all()
    return {r.item_id: r for r in rows}  # последняя версия пункта


def execute(ctx: ItemContext, db_factory: DbFactory) -> ItemOutcome:
    try:
        if ctx.item.is_info:
            with _session(db_factory) as db:
                return ItemOutcome(info_item(db, ctx.info, ctx.item, ctx.registry), ResultAuthor.system)
        return run_agent(ctx)
    except LLMError as exc:
        log.warning("Пункт %s не выполнен: %s", ctx.item.id, exc)
        return ItemOutcome(None, error=str(exc)[:2000])
    except Exception as exc:  # сбой пункта не валит прогон (TZ §9)
        log.exception("Пункт %s: непредвиденная ошибка", ctx.item.id)
        return ItemOutcome(None, error=f"{type(exc).__name__}: {exc}"[:2000])


def run_items(
    db_factory: DbFactory,
    run_id: uuid.UUID,
    only: list[str] | None = None,
    llm: LLM | None = None,
    concurrency: int | None = None,
) -> None:
    """Выполняет пункты прогона в статусе pending (или только `only`) по зависимостям.

    concurrency=1 — последовательно в текущем потоке (тесты используют одну сессию БД)."""
    s = get_settings()
    llm = llm or get_llm()
    concurrency = concurrency or s.analysis_concurrency
    with _session(db_factory) as db:
        run = db.get(AssessmentRun, run_id)
        if run is None or run.status in (RunStatus.cancelled,):
            return
        project = db.scalar(select(Project).where(Project.id == run.project_id, Project.org_id == run.org_id))
        if project is None:
            return
        template = parse_template(project.template_version.yaml_text)
        plan = {i.id: i for i in plan_items(template, list(project.enabled_optional_items))}
        latest = _latest(db, run.id)
        todo = [
            i
            for i in plan
            if latest.get(i) and latest[i].status == ItemStatus.pending and (not only or i in only)
        ]
        if not todo:
            return
        if run.status == RunStatus.queued:
            run.status, run.started_at = RunStatus.running, _now()
            project.status = ProjectStatus.analysis
        db.commit()
        registry = _registry(db, run.id)
        done: dict[str, SectionPayload | None] = {
            i: (
                SectionPayload.model_validate(r.payload_json)
                if r.status == ItemStatus.done and r.payload_json
                else None
            )
            for i, r in latest.items()
            if r.status in (ItemStatus.done, ItemStatus.failed)
        }
        tool_ctx = ToolContext(
            db_factory=db_factory,
            org_id=project.org_id,
            project_id=project.id,
            passport=run.passport_json or {},
            registry=registry,
            categories=document_categories(template),
        )
        info = RunInfo(
            run.id, run.org_id, project.id, run.web_search, project.name, project.industry, project.region
        )

    remaining = list(todo)
    in_flight: set[str] = set()

    def ready(item_id: str) -> bool:
        return all(d in done or (d not in remaining and d not in in_flight) for d in plan[item_id].depends_on)

    def start(item_id: str) -> ItemContext:
        with _session(db_factory) as db:
            row = _latest(db, run_id)[item_id]
            row.status, row.started_at = ItemStatus.running, _now()
            instruction = row.instruction
            db.commit()
        item = plan[item_id]
        return ItemContext(
            info=info,
            item=item,
            instruction=instruction,
            deps={d: done.get(d) for d in item.depends_on},
            registry=registry,
            tools=tool_ctx,
            llm=llm,
        )

    def save(item_id: str, outcome: ItemOutcome) -> None:
        with _session(db_factory) as db:
            row = _latest(db, run_id)[item_id]
            row.finished_at = _now()
            if outcome.payload is not None:
                row.status, row.payload_json = ItemStatus.done, outcome.payload.model_dump(mode="json")
                row.author, row.error = outcome.author, None
            else:
                row.status, row.error = ItemStatus.failed, outcome.error
            row.validator_warnings_json = outcome.warnings
            _persist_sources(db, db.get(AssessmentRun, run_id), registry)
            db.commit()
        done[item_id] = outcome.payload

    if concurrency <= 1:
        while remaining:
            item_id = next((i for i in remaining if ready(i)), remaining[0])  # цикл зависимостей — по порядку
            remaining.remove(item_id)
            save(item_id, execute(start(item_id), db_factory))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            running: dict[Future, str] = {}
            while remaining or running:
                for item_id in [i for i in remaining if ready(i)][: concurrency - len(running)]:
                    remaining.remove(item_id)
                    in_flight.add(item_id)
                    running[pool.submit(execute, start(item_id), db_factory)] = item_id
                if not running and remaining:  # цикл зависимостей — запускаем по порядку
                    item_id = remaining.pop(0)
                    in_flight.add(item_id)
                    running[pool.submit(execute, start(item_id), db_factory)] = item_id
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    item_id = running.pop(future)
                    save(item_id, future.result())
                    in_flight.discard(item_id)

    with _session(db_factory) as db:
        run = db.get(AssessmentRun, run_id)
        pending = db.scalar(
            select(func.count())
            .select_from(SectionResult)
            .where(
                SectionResult.run_id == run_id,
                SectionResult.status.in_((ItemStatus.pending, ItemStatus.running)),
            )
        )
        if not pending and run.status == RunStatus.running:
            run.status, run.finished_at = RunStatus.done, _now()
            project = db.get(Project, run.project_id)
            if project and project.status == ProjectStatus.analysis:
                project.status = ProjectStatus.review
        db.commit()


def run_cost(db: Session, run_id: uuid.UUID):
    return db.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(LlmCall.run_id == run_id))
