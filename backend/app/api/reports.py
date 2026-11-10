import uuid
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentPrincipal, DbSession
from app.core.storage import Storage, get_storage
from app.models import RenderStatus, ReportVersion
from app.schemas.report import ReportCreateIn, ReportOut
from app.services import reports as svc
from app.services.audit import audit
from app.services.projects import get_project

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])

EnqueuerDep = Annotated[svc.ReportEnqueuer, Depends(svc.get_report_enqueuer)]
StorageDep = Annotated[Storage, Depends(get_storage)]


def _out(r: ReportVersion) -> ReportOut:
    return ReportOut(
        id=r.id,
        number=r.number,
        version=r.version,
        status=r.status.value,
        render_status=r.render_status.value,
        error=r.error,
        filename=r.filename,
        has_docx=r.docx_key is not None,
        has_pdf=r.pdf_key is not None,
        sha256=r.sha256,
        created_at=r.created_at,
        finished_at=r.finished_at,
    )


@router.get("", response_model=list[ReportOut])
def list_reports(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return [_out(r) for r in svc.list_reports(db, get_project(db, principal, project_id))]


@router.post("", response_model=ReportOut, status_code=status.HTTP_202_ACCEPTED)
def create_draft(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    enqueuer: EnqueuerDep,
    body: ReportCreateIn | None = None,
):
    """Черновик отчёта по последнему прогону (водяной знак «Черновик») или выпуск (release=true)."""
    project = get_project(db, principal, project_id)
    return _out(svc.create_draft(db, enqueuer, principal, project, release=bool(body and body.release)))


@router.get("/{report_id}/download")
def download(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    fmt: Annotated[Literal["docx", "pdf"], Query()] = "docx",
):
    project = get_project(db, principal, project_id)
    report = svc.get_report(db, project, report_id)
    key = report.docx_key if fmt == "docx" else report.pdf_key
    if report.render_status != RenderStatus.ready or key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Файл версии ещё не готов")
    audit(
        db,
        "report.downloaded",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        report_id=report.id,
        fmt=fmt,
    )
    db.commit()
    obj = storage.get(key)
    name = f"{report.filename}.{fmt}"
    return StreamingResponse(
        obj.body,
        media_type=obj.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
