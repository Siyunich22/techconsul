"""Отчёт (TZ §4.5 Ж, §5.7): версии v0.N — черновики по последнему прогону оценки, файлы неизменяемы.

Выпуск v1.0 (без водяного знака, с хешем в колонтитуле) — только после утверждения всех пунктов
экспертами (правило 3, фаза 7); здесь его нет.
"""

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.storage import Storage, make_key, put_bytes
from app.models import AssessmentRun, Project, RenderStatus, ReportStatus, ReportVersion
from app.report.data import collect
from app.report.docx_render import render_docx
from app.report.pdf import docx_to_pdf, heading_pages
from app.services.assessment import _is_active, latest_run
from app.services.audit import audit
from app.services.passport import require_reviewer

log = logging.getLogger(__name__)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ReportEnqueuer(Protocol):
    def render(self, report_id: uuid.UUID) -> None: ...


class CeleryReportEnqueuer:
    def render(self, report_id: uuid.UUID) -> None:
        from workers.tasks import enqueue_report

        enqueue_report(report_id)


@lru_cache
def get_report_enqueuer() -> ReportEnqueuer:
    return CeleryReportEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


def report_filename(project_name: str, when: datetime) -> str:
    """Оценка_техпроработанности_<Проект>_<дата> (TZ §1)."""
    name = re.sub(r"[^\w\-]+", "_", project_name, flags=re.UNICODE).strip("_")[:80] or "проект"
    return f"Оценка_техпроработанности_{name}_{when:%Y-%m-%d}"


def list_reports(db: Session, project: Project) -> list[ReportVersion]:
    return list(
        db.scalars(
            select(ReportVersion)
            .where(ReportVersion.project_id == project.id, ReportVersion.org_id == project.org_id)
            .order_by(ReportVersion.number.desc())
        )
    )


def get_report(db: Session, project: Project, report_id: uuid.UUID) -> ReportVersion:
    report = db.scalar(
        select(ReportVersion).where(
            ReportVersion.id == report_id,
            ReportVersion.project_id == project.id,
            ReportVersion.org_id == project.org_id,
        )
    )
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия отчёта не найдена")
    return report


def create_draft(
    db: Session, enqueuer: ReportEnqueuer, principal: Principal, project: Project, *, release: bool = False
) -> ReportVersion:
    """Черновик v0.N — в любой момент; выпуск vN.0 — руководитель и только при чистом валидаторе
    (все пункты, риски и выводы утверждены экспертами, блокирующих ошибок нет — правило 3)."""
    require_reviewer(principal)
    if release and not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Выпускать отчёт может руководитель проекта")
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())  # номера по очереди
    run = latest_run(db, project)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка ещё не запускалась — отчёт не из чего собрать")
    if _is_active(db, run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания оценки")
    busy = db.scalar(
        select(func.count())
        .select_from(ReportVersion)
        .where(
            ReportVersion.project_id == project.id,
            ReportVersion.render_status.in_((RenderStatus.queued, RenderStatus.rendering)),
            ReportVersion.created_at > func.now() - func.make_interval(0, 0, 0, 0, 0, 30),
        )
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "Отчёт уже формируется")
    if release:
        from app.services.risks import coverage

        cov = coverage(db, project)
        if not cov.can_release:
            errors = [i.message for i in cov.issues if i.level == "error"]
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Выпуск невозможен — ошибок валидатора: {len(errors)}. Например: {'; '.join(errors[:3])}",
            )
    number = (
        db.scalar(select(func.max(ReportVersion.number)).where(ReportVersion.project_id == project.id)) or 0
    ) + 1
    released_before = db.scalar(
        select(func.count())
        .select_from(ReportVersion)
        .where(ReportVersion.project_id == project.id, ReportVersion.status == ReportStatus.released)
    )
    now = _now()
    report = ReportVersion(
        org_id=project.org_id,
        project_id=project.id,
        run_id=run.id,
        number=number,
        version=f"{released_before + 1}.0" if release else f"0.{number}",
        status=ReportStatus.released if release else ReportStatus.draft,
        render_status=RenderStatus.queued,
        filename=report_filename(project.name, now),
        created_by=principal.user_id,
    )
    db.add(report)
    db.flush()
    audit(
        db,
        "report.released" if release else "report.draft_created",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        report_id=report.id,
        version=report.version,
    )
    db.commit()
    enqueuer.render(report.id)
    db.refresh(report)
    return report


def render(db: Session, storage: Storage, report_id: uuid.UUID, *, with_pdf: bool = True) -> None:
    """Сборка версии: DOCX (+ PDF двумя проходами для номеров страниц в содержании)."""
    report = db.get(ReportVersion, report_id)
    if report is None or report.render_status not in (RenderStatus.queued, RenderStatus.rendering):
        return
    report.render_status = RenderStatus.rendering
    db.commit()
    try:
        project = db.scalar(
            select(Project).where(Project.id == report.project_id, Project.org_id == report.org_id)
        )
        run = db.get(AssessmentRun, report.run_id) if report.run_id else latest_run(db, project)
        data = collect(
            db,
            storage,
            project,
            run,
            version=report.version,
            released=report.status == ReportStatus.released,
            created=report.created_at.date(),
        )
        docx, toc = render_docx(data)
        pdf = None
        if with_pdf:
            first = docx_to_pdf(docx)
            pages = heading_pages(first, [e.match for e in toc])
            docx, _ = render_docx(data, pages)
            pdf = docx_to_pdf(docx)
        base = ("orgs", str(report.org_id), "projects", str(report.project_id), "reports")
        report.docx_key = make_key(*base, filename=f"{report.filename}.docx")
        put_bytes(storage, report.docx_key, docx, DOCX_TYPE)
        if pdf is not None:
            report.pdf_key = make_key(*base, filename=f"{report.filename}.pdf")
            put_bytes(storage, report.pdf_key, pdf, "application/pdf")
        report.sha256 = hashlib.sha256(docx).hexdigest()
        report.render_status, report.error, report.finished_at = RenderStatus.ready, None, _now()
        db.commit()
    except Exception as exc:  # сбой сборки — статус «ошибка» с причиной, а не вечное «формируется»
        log.exception("Отчёт %s не собран", report_id)
        db.rollback()
        report = db.get(ReportVersion, report_id)
        report.render_status, report.error = RenderStatus.failed, f"{type(exc).__name__}: {exc}"[:2000]
        report.finished_at = _now()
        db.commit()
