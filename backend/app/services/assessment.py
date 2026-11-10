"""Оценка проекта (TZ §4.5 В): запуск прогона, пересборка пункта, результаты и прогресс."""

import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from types import SimpleNamespace
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.assessment.fingerprint import DocRef, fingerprints
from app.assessment.runner import _reuse_candidate, lock_key, model_role, plan_items, run_cost
from app.core.config import get_settings
from app.models import (
    AssessmentRun,
    Document,
    DocumentStatus,
    Expert,
    ItemStatus,
    Organization,
    Passport,
    PassportStatus,
    Project,
    ProjectMember,
    RunStatus,
    SectionResult,
    Source,
)
from app.schemas.assessment import (
    AssessmentOut,
    EstimateOut,
    ItemDetailOut,
    ItemOut,
    RunOut,
    SectionPayload,
    SourceOut,
)
from app.services.audit import audit
from app.template_engine.loader import parse_template

# Оценка стоимости пункта агентом (5–12 обращений к инструментам), USD по роли модели — уточняется по
# факту прогонов (llm_calls.run_id). Выводы — сильная модель (Opus), остальные — Sonnet (вдвое дешевле).
# Калибровка 2026-11-02 по первому прогону на стенде (Opus, 22 пункта): 0,23–1,34 $, медиана ~0,7 $.
COST_PER_ITEM = {
    "analysis": (Decimal("0.25"), Decimal("1.35")),
    "items": (Decimal("0.12"), Decimal("0.70")),
}
MINUTES_PER_ITEM = 3


class AssessmentEnqueuer(Protocol):
    def run(self, run_id: uuid.UUID, only: list[str] | None = None) -> None: ...


class CeleryAssessmentEnqueuer:
    def run(self, run_id: uuid.UUID, only: list[str] | None = None) -> None:
        from workers.tasks import enqueue_assessment

        enqueue_assessment(run_id, only)


@lru_cache
def get_assessment_enqueuer() -> AssessmentEnqueuer:
    return CeleryAssessmentEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


def _plan(project: Project):
    version = project.template_version
    return _plan_cached(version.id, tuple(project.enabled_optional_items), version.yaml_text)


@lru_cache(maxsize=64)
def _plan_cached(version_id: uuid.UUID, optional: tuple[str, ...], yaml_text: str):
    """План пунктов по версии шаблона: SSE опрашивает прогресс каждые 2 с, YAML не разбираем заново.
    Версия шаблона неизменяема; текст в ключе — страховка от правки в тестах."""
    return tuple(plan_items(parse_template(yaml_text), list(optional)))


def latest_run(db: Session, project: Project) -> AssessmentRun | None:
    return db.scalar(
        select(AssessmentRun)
        .where(AssessmentRun.project_id == project.id, AssessmentRun.org_id == project.org_id)
        .order_by(AssessmentRun.created_at.desc())
        .limit(1)
    )


def _passport(db: Session, project: Project) -> Passport | None:
    return db.scalar(
        select(Passport).where(Passport.project_id == project.id, Passport.org_id == project.org_id)
    )


def _web_allowed(db: Session, project: Project) -> bool:
    org = db.get(Organization, project.org_id)
    return bool((org.settings_json or {}).get("web_search_enabled", True)) if org else False


def _is_active(db: Session, run: AssessmentRun) -> bool:
    """Прогон жив, если им занят воркер (держит advisory-блокировку прогона) либо он недавно поставлен
    в очередь. «running» без блокировки — след упавшего воркера."""
    if run.status not in (RunStatus.queued, RunStatus.running):
        return False
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(run.id)}):
        return True  # блокировка у воркера; наша xact-блокировка снимется с концом транзакции
    started = run.started_at or run.created_at
    return _now() - started < timedelta(minutes=get_settings().analysis_stale_after_min)


def _docs_processing(db: Session, project: Project) -> int:
    return len(
        db.scalars(
            select(Document.id).where(
                Document.project_id == project.id,
                Document.org_id == project.org_id,
                Document.deleted_at.is_(None),
                Document.status.in_((DocumentStatus.uploaded, DocumentStatus.processing)),
            )
        ).all()
    )


def _passport_snapshot(passport: Passport) -> dict:
    return {
        "data": passport.data_json,
        "discrepancies": passport.discrepancies_json,
        "sources": passport.sources_json,
    }


def _doc_refs(db: Session, project: Project) -> list[DocRef]:
    rows = db.execute(
        select(Document.id, Document.sha256, Document.category).where(
            Document.project_id == project.id,
            Document.org_id == project.org_id,
            Document.deleted_at.is_(None),
            Document.status.in_((DocumentStatus.recognized, DocumentStatus.indexed)),
        )
    ).all()
    return [DocRef(*r) for r in rows]


def _reusable(db: Session, project: Project, plan, fps: dict[str, str]) -> set[str]:
    """Пункты, которые прогон возьмёт из прошлого: отпечаток совпал, и зависимости тоже берутся оттуда."""
    items = {i.id: i for i in plan}
    memo: dict[str, bool] = {}

    def ok(item_id: str, path: tuple[str, ...] = ()) -> bool:
        if item_id in memo:
            return memo[item_id]
        item = items[item_id]
        if item.is_info:
            return True
        probe = SimpleNamespace(
            org_id=project.org_id,
            project_id=project.id,
            item_id=item_id,
            fingerprint=fps.get(item_id),
            run_id=uuid.UUID(int=0),
        )
        result = (
            probe.fingerprint is not None
            and _reuse_candidate(db, probe) is not None  # type: ignore[arg-type]
            and all(d not in path and ok(d, (*path, item_id)) for d in item.depends_on if d in items)
        )
        memo[item_id] = result
        return result

    return {i.id for i in plan if not i.is_info and ok(i.id)}


def estimate(db: Session, project: Project) -> EstimateOut:
    plan = _plan(project)
    ai = sum(not i.is_info for i in plan)
    passport = _passport(db, project)
    web = _web_allowed(db, project)
    reused: set[str] = set()
    if passport is not None:
        fps = fingerprints(plan, _doc_refs(db, project), _passport_snapshot(passport), web)
        reused = _reusable(db, project, plan, fps)
    todo = [i for i in plan if not i.is_info and i.id not in reused]
    warnings = []
    if passport is None or passport.status != PassportStatus.confirmed:
        warnings.append("Паспорт проекта не подтверждён — подтвердите его во вкладке «Паспорт проекта».")
    if _docs_processing(db, project):
        warnings.append("Часть документов ещё обрабатывается.")
    if not web:
        warnings.append("Веб-поиск отключён в настройках организации — внешние источники не используются.")
    if reused:
        warnings.append(
            f"Пунктов без изменений с прошлого прогона: {len(reused)} — возьмутся из него без обращения к ИИ."
        )
    s = get_settings()
    concurrency = max(1, s.analysis_concurrency)
    roles = [model_role(i) for i in todo]
    last = latest_run(db, project)
    failed = _failed_items(db, last, plan) if last is not None and not _is_active(db, last) else []
    resume_roles = [model_role(i) for i in failed if not i.is_info]
    return EstimateOut(
        resume_items=len(failed),
        resume_cost_usd_min=sum((COST_PER_ITEM[r][0] for r in resume_roles), Decimal(0)),
        resume_cost_usd_max=sum((COST_PER_ITEM[r][1] for r in resume_roles), Decimal(0)),
        items_ai=ai,
        items_info=len(plan) - ai,
        items_reused=len(reused),
        items_strong=sum(r == "analysis" for r in roles),
        budget_usd=Decimal(str(s.analysis_run_budget_usd)) if s.analysis_run_budget_usd > 0 else None,
        cost_usd_min=sum((COST_PER_ITEM[r][0] for r in roles), Decimal(0)),
        cost_usd_max=sum((COST_PER_ITEM[r][1] for r in roles), Decimal(0)),
        minutes=math.ceil(len(todo) / concurrency) * MINUTES_PER_ITEM,
        passport_confirmed=passport is not None and passport.status == PassportStatus.confirmed,
        web_search=web,
        warnings=warnings,
    )


def start_run(
    db: Session, enqueuer: AssessmentEnqueuer, principal: Principal, project: Project
) -> AssessmentRun:
    if not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Запускать оценку может руководитель проекта")
    # блокировка строки проекта: два одновременных запуска не создают два прогона
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
    passport = _passport(db, project)
    if passport is None or passport.status != PassportStatus.confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Сначала подтвердите паспорт проекта")
    if _docs_processing(db, project):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания обработки документов")
    previous = latest_run(db, project)
    if previous is not None and _is_active(db, previous):
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка уже выполняется")
    if previous is not None and previous.status in (RunStatus.queued, RunStatus.running):
        previous.status, previous.error = RunStatus.failed, "Прогон прерван (воркер не отвечал)"

    plan = _plan(project)
    run = AssessmentRun(
        org_id=project.org_id,
        project_id=project.id,
        status=RunStatus.queued,
        started_by=principal.user_id,
        items_total=len(plan),
        web_search=_web_allowed(db, project),
        passport_json=_passport_snapshot(passport),
    )
    db.add(run)
    db.flush()
    fps = fingerprints(plan, _doc_refs(db, project), run.passport_json, run.web_search)
    for item in plan:
        db.add(
            SectionResult(
                org_id=project.org_id,
                project_id=project.id,
                run_id=run.id,
                item_id=item.id,
                fingerprint=fps.get(item.id),
            )
        )
    audit(
        db,
        "assessment.started",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
        items=len(plan),
        web_search=run.web_search,
    )
    db.commit()
    enqueuer.run(run.id)
    db.refresh(run)
    return run


def _failed_items(db: Session, run: AssessmentRun, plan) -> list:
    rows = _latest_rows(db, run)
    return [i for i in plan if (r := rows.get(i.id)) is not None and r.status == ItemStatus.failed]


def resume_run(
    db: Session, enqueuer: AssessmentEnqueuer, principal: Principal, project: Project
) -> AssessmentRun:
    """Довыполнение прогона: новые версии только для пунктов с ошибкой (нет средств, сбой сети, бюджет);
    готовые пункты и их источники остаются — за них не платим повторно."""
    if not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Довыполнять оценку может руководитель проекта")
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
    run = latest_run(db, project)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка ещё не запускалась")
    if _is_active(db, run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка уже выполняется")
    plan = _plan(project)
    failed = _failed_items(db, run, plan)
    if not failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Пунктов с ошибкой нет")
    rows = _latest_rows(db, run)
    fps = fingerprints(plan, _doc_refs(db, project), run.passport_json, run.web_search)
    for item in failed:
        db.add(
            SectionResult(
                org_id=project.org_id,
                project_id=project.id,
                run_id=run.id,
                item_id=item.id,
                version=rows[item.id].version + 1,
                fingerprint=fps.get(item.id),
            )
        )
    run.status, run.error, run.finished_at = RunStatus.running, None, None
    audit(
        db,
        "assessment.resumed",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
        items=len(failed),
    )
    db.commit()
    enqueuer.run(run.id)
    db.refresh(run)
    return run


def _latest_rows(db: Session, run: AssessmentRun) -> dict[str, SectionResult]:
    rows = db.scalars(
        select(SectionResult)
        .where(SectionResult.run_id == run.id, SectionResult.org_id == run.org_id)
        .order_by(SectionResult.version)
    ).all()
    return {r.item_id: r for r in rows}


def rerun_item(
    db: Session,
    enqueuer: AssessmentEnqueuer,
    principal: Principal,
    project: Project,
    item_id: str,
    instruction: str,
) -> SectionResult:
    if not principal.can_manage and principal.role.value != "expert":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    if not principal.can_manage and not _expert_assigned(db, principal, project, item_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Пункт не входит в разделы, назначенные эксперту")
    # версии пункта — по очереди
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
    run = latest_run(db, project)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка ещё не запускалась")
    current = _latest_rows(db, run).get(item_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пункт не найден в прогоне")
    if current.status in (ItemStatus.pending, ItemStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, "Пункт уже выполняется")
    row = SectionResult(
        org_id=project.org_id,
        project_id=project.id,
        run_id=run.id,
        item_id=item_id,
        version=current.version + 1,
        instruction=instruction.strip(),
    )
    db.add(row)
    if run.status not in (RunStatus.queued, RunStatus.running):
        run.status, run.finished_at = RunStatus.running, None
    audit(
        db,
        "assessment.item_rerun",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
        item_id=item_id,
        version=row.version,
        instruction=row.instruction,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Пункт уже пересобирается") from exc
    # Без `only`: если прогоном занят воркер, он сам подберёт новый pending-пункт после текущей партии.
    enqueuer.run(run.id)
    db.refresh(row)
    return row


def _expert_assigned(db: Session, principal: Principal, project: Project, item_id: str) -> bool:
    """Эксперт пересобирает пункты своих разделов (assigned_items); руководитель команды — любые."""
    member = db.scalar(
        select(ProjectMember)
        .join(Expert, Expert.id == ProjectMember.expert_id)
        .where(
            ProjectMember.project_id == project.id,
            Expert.org_id == project.org_id,
            Expert.user_id == principal.user_id,
        )
    )
    if member is None:
        return False
    if member.role.value == "lead":
        return True
    return any(item_id == a or item_id.startswith(a + ".") for a in member.assigned_items)


def run_out(db: Session, run: AssessmentRun) -> RunOut:
    rows = _latest_rows(db, run).values()
    return RunOut(
        id=run.id,
        status=run.status.value,
        items_total=run.items_total,
        items_done=sum(r.status == ItemStatus.done for r in rows),
        items_failed=sum(r.status == ItemStatus.failed for r in rows),
        cost_usd=Decimal(run_cost(db, run.id)),
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        web_search=run.web_search,
    )


def _item_out(row: SectionResult, titles: dict) -> ItemOut:
    payload = row.payload_json or {}
    title, section_id, kind, section_title = titles.get(row.item_id, (row.item_id, "", None, ""))
    return ItemOut(
        item_id=row.item_id,
        title=title,
        section_id=section_id,
        section_title=section_title,
        kind=kind,
        status=row.status.value,
        version=row.version,
        disclosure_status=payload.get("disclosure_status"),
        summary=payload.get("summary"),
        error=row.error,
        review_status=row.review_status.value,
        instruction=row.instruction,
        started_at=row.started_at,
        finished_at=row.finished_at,
        reused=row.reused_from_id is not None,
        author=row.author.value,
    )


def _titles(project: Project) -> dict:
    return {i.id: (i.title, i.section.split(" ", 1)[0], i.kind, i.section) for i in _plan(project)}


def assessment(db: Session, project: Project) -> AssessmentOut:
    run = latest_run(db, project)
    if run is None:
        return AssessmentOut(run=None, items=[])
    titles = _titles(project)
    rows = _latest_rows(db, run)
    order = {item_id: n for n, item_id in enumerate(titles)}
    items = sorted(rows.values(), key=lambda r: order.get(r.item_id, len(order)))
    return AssessmentOut(run=run_out(db, run), items=[_item_out(r, titles) for r in items])


def item_detail(db: Session, project: Project, item_id: str) -> ItemDetailOut:
    run = latest_run(db, project)
    row = _latest_rows(db, run).get(item_id) if run else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пункт не найден")
    base = _item_out(row, _titles(project))
    payload = SectionPayload.model_validate(row.payload_json) if row.payload_json else None
    sources: list[SourceOut] = []
    if payload is not None and payload.sources:
        sids = [s.id for s in payload.sources]
        found = {
            s.sid: s
            for s in db.scalars(
                select(Source).where(
                    Source.run_id == run.id, Source.org_id == run.org_id, Source.sid.in_(sids)
                )
            )
        }
        names = {
            d.id: d.filename
            for d in db.scalars(
                select(Document).where(
                    Document.org_id == run.org_id,
                    Document.id.in_([s.document_id for s in found.values() if s.document_id]),
                )
            )
        }
        sources = [
            SourceOut(
                sid=s.sid,
                type=s.type,  # type: ignore[arg-type]
                document_id=s.document_id,
                filename=names.get(s.document_id) if s.document_id else None,
                page=s.page,
                sheet=s.sheet,
                cell_range=s.cell_range,
                reference_doc_id=s.reference_doc_id,
                url=s.url,
                title=s.title,
                accessed_at=s.accessed_at,
            )
            for sid in sids
            if (s := found.get(sid))
        ]
    return ItemDetailOut(
        **base.model_dump(),
        payload=payload,
        sources=sources,
        warnings=list(row.validator_warnings_json or []),
    )


def get_run(db: Session, project: Project, run_id: uuid.UUID) -> AssessmentRun:
    run = db.scalar(
        select(AssessmentRun).where(
            AssessmentRun.id == run_id,
            AssessmentRun.project_id == project.id,
            AssessmentRun.org_id == project.org_id,
        )
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Прогон не найден")
    return run
