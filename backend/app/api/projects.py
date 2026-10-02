import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentPrincipal, DbSession, ManagerPrincipal
from app.models import ProjectStatus
from app.schemas.common import Page
from app.schemas.project import (
    MemberIn,
    MemberOut,
    ProjectCreate,
    ProjectListItem,
    ProjectOut,
    ProjectPatch,
    SortField,
)
from app.services import projects as svc

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectListItem])
def list_projects(
    principal: CurrentPrincipal,
    db: DbSession,
    status_: Annotated[list[ProjectStatus] | None, Query(alias="status")] = None,
    include_archived: bool = False,
    industry: str | None = None,
    region: str | None = None,
    owner_id: uuid.UUID | None = None,
    integral_risk: str | None = None,
    updated_from: date | None = None,
    updated_to: date | None = None,
    q: str | None = None,
    sort: SortField = "updated_at",
    desc: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
):
    items, total = svc.list_projects(
        db,
        principal,
        statuses=status_,
        include_archived=include_archived,
        industry=industry,
        region=region,
        owner_id=owner_id,
        integral_risk=integral_risk,
        updated_from=updated_from,
        updated_to=updated_to,
        q=q,
        sort=sort,
        desc=desc,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(data: ProjectCreate, principal: ManagerPrincipal, db: DbSession):
    project = svc.create_project(db, principal, data)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.get_project(db, principal, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: uuid.UUID, data: ProjectPatch, principal: ManagerPrincipal, db: DbSession):
    project = svc.update_project(db, principal, svc.get_project(db, principal, project_id), data)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, principal: ManagerPrincipal, db: DbSession):
    svc.delete_project(db, principal, svc.get_project(db, principal, project_id))
    db.commit()


@router.post("/{project_id}/archive", response_model=ProjectOut)
def archive_project(project_id: uuid.UUID, principal: ManagerPrincipal, db: DbSession):
    project = svc.archive(db, principal, svc.get_project(db, principal, project_id))
    db.commit()
    return project


@router.post("/{project_id}/unarchive", response_model=ProjectOut)
def unarchive_project(project_id: uuid.UUID, principal: ManagerPrincipal, db: DbSession):
    project = svc.unarchive(db, principal, svc.get_project(db, principal, project_id))
    db.commit()
    return project


@router.get("/{project_id}/members", response_model=list[MemberOut])
def get_members(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.get_project(db, principal, project_id).members


@router.put("/{project_id}/members", response_model=list[MemberOut])
def put_members(project_id: uuid.UUID, data: list[MemberIn], principal: ManagerPrincipal, db: DbSession):
    project = svc.set_members(db, principal, svc.get_project(db, principal, project_id), data)
    db.commit()
    db.refresh(project)
    return project.members
