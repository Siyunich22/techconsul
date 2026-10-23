import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.passport import LayoutSection, PassportOut, PassportUpdate, passport_layout
from app.services import passport as svc
from app.services.projects import get_project

router = APIRouter(tags=["passport"])

EnqueuerDep = Annotated[svc.PassportEnqueuer, Depends(svc.get_passport_enqueuer)]


@router.get("/passport/layout", response_model=list[LayoutSection])
def layout(_: CurrentPrincipal):
    """Разделы, поля и колонки таблиц паспорта с подписями — интерфейс строится по ним."""
    return passport_layout()


@router.get("/projects/{project_id}/passport", response_model=PassportOut)
def get_passport(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    return svc.to_out(svc.get_passport(db, project))


@router.post(
    "/projects/{project_id}/passport/extract",
    response_model=PassportOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def extract(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, enqueuer: EnqueuerDep):
    project = get_project(db, principal, project_id)
    return svc.to_out(svc.start_extraction(db, enqueuer, principal, project))


@router.put("/projects/{project_id}/passport", response_model=PassportOut)
def update(project_id: uuid.UUID, body: PassportUpdate, principal: CurrentPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    return svc.to_out(svc.update_passport(db, principal, project, body))


@router.put("/projects/{project_id}/passport/confirm", response_model=PassportOut)
def confirm(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    return svc.to_out(svc.confirm_passport(db, principal, project))
