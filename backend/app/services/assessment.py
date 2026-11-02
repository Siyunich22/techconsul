"""Оценка проекта (TZ §4.5 В): запуск прогона, пересборка пункта, результаты и прогресс."""

import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.assessment.runner import plan_items, run_cost
from app.core.config import get_settings
from app.models import (
    AssessmentRun,
    Document,
    DocumentStatus,
    ItemStatus,
    Organization,
    Passport,
    PassportStatus,
    Project,
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

# Оценка стоимости пункта агентом (Opus, 5–12 обращений к инструментам), USD — уточняется по факту прогонов
COST_PER_ITEM_MIN = Decimal("0.15")
COST_PER_ITEM_MAX = Decimal("0.80")
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
    return plan_items(
        parse_template(project.template_version.yaml_text), list(project.enabled_optional_items)
    )


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


def _is_active(run: AssessmentRun) -> bool:
    if run.status not in (RunStatus.queued, RunStatus.running):
        return False
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


def estimate(db: Session, project: Project) -> EstimateOut:
    plan = _plan(project)
    ai = sum(not i.is_info for i in plan)
    passport = _passport(db, project)
    web = _web_allowed(db, project)
    warnings = []
    if passport is None or passport.status != PassportStatus.confirmed:
        warnings.append("Паспорт проекта не подтверждён — подтвердите его во вкладке «Паспорт проекта».")
    if _docs_processing(db, project):
        warnings.append("Часть документов ещё обрабатывается.")
    if not web:
        warnings.append("Веб-поиск отключён в настройках организации — внешние источники не используются.")
    concurrency = max(1, get_settings().analysis_concurrency)
    return EstimateOut(
        items_ai=ai,
        items_info=len(plan) - ai,
        cost_usd_min=COST_PER_ITEM_MIN * ai,
        cost_usd_max=COST_PER_ITEM_MAX * ai,
        minutes=math.ceil(ai / concurrency) * MINUTES_PER_ITEM,
        passport_confirmed=passport is not None and passport.status == PassportStatus.confirmed,
        web_search=web,
        warnings=warnings,
    )


def start_run(
    db: Session, enqueuer: AssessmentEnqueuer, principal: Principal, project: Project
) -> AssessmentRun:
    if not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Запускать оценку может руководитель проекта")
    passport = _passport(db, project)
    if passport is None or passport.status != PassportStatus.confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Сначала подтвердите паспорт проекта")
    if _docs_processing(db, project):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания обработки документов")
    previous = latest_run(db, project)
    if previous is not None and _is_active(previous):
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
        passport_json={
            "data": passport.data_json,
            "discrepancies": passport.discrepancies_json,
            "sources": passport.sources_json,
        },
    )
    db.add(run)
    db.flush()
    for item in plan:
        db.add(SectionResult(org_id=project.org_id, project_id=project.id, run_id=run.id, item_id=item.id))
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
    db.commit()
    enqueuer.run(run.id, [item_id])
    db.refresh(row)
    return row


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
                    Document.id.in_([s.document_id for s in found.values() if s.document_id])
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
