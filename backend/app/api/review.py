import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.assessment import ItemDetailOut
from app.schemas.review import CommentIn, CommentOut, ItemEdit, RiskEdit, VersionOut
from app.schemas.risk import CoverageOut, RisksOut
from app.services import assessment as assessment_svc
from app.services import review as svc
from app.services import risks as risks_svc
from app.services.projects import get_project

router = APIRouter(prefix="/projects/{project_id}", tags=["review"])

EnqueuerDep = Annotated[svc.ReviewEnqueuer, Depends(svc.get_review_enqueuer)]
ItemId = Annotated[str, Path(pattern=r"^[0-9]+(\.[0-9]+)*$", max_length=20)]
RiskCode = Annotated[str, Path(pattern=r"^[A-Z][A-Z0-9]{0,5}$")]


@router.put("/assessment/items/{item_id}/edit", response_model=ItemDetailOut)
def edit_item(
    project_id: uuid.UUID, item_id: ItemId, body: ItemEdit, principal: CurrentPrincipal, db: DbSession
):
    """Правка пункта экспертом — новая версия (ИИ-версия сохраняется), статус «на проверке»."""
    project = get_project(db, principal, project_id)
    svc.edit_item(db, principal, project, item_id, body)
    return assessment_svc.item_detail(db, project, item_id)


@router.get("/assessment/items/{item_id}/versions", response_model=list[VersionOut])
def item_versions(project_id: uuid.UUID, item_id: ItemId, principal: CurrentPrincipal, db: DbSession):
    return svc.versions(db, get_project(db, principal, project_id), item_id)


@router.put("/risks/{code}/edit", response_model=RisksOut)
def edit_risk(
    project_id: uuid.UUID, code: RiskCode, body: RiskEdit, principal: CurrentPrincipal, db: DbSession
):
    project = get_project(db, principal, project_id)
    svc.edit_risk(db, principal, project, code, body)
    return risks_svc.risks(db, project)


@router.put("/conclusions/edit", response_model=RisksOut)
def edit_conclusions(project_id: uuid.UUID, body: ItemEdit, principal: CurrentPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    svc.edit_conclusions(db, principal, project, body)
    return risks_svc.risks(db, project)


@router.get("/comments", response_model=list[CommentOut])
def comments(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    ref: Annotated[str | None, Query(max_length=20)] = None,
):
    return svc.list_comments(db, get_project(db, principal, project_id), ref)


@router.post("/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(project_id: uuid.UUID, body: CommentIn, principal: CurrentPrincipal, db: DbSession):
    return svc.add_comment(db, principal, get_project(db, principal, project_id), body)


@router.post("/comments/{comment_id}/resolve", response_model=CommentOut)
def resolve_comment(project_id: uuid.UUID, comment_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.resolve_comment(db, principal, get_project(db, principal, project_id), comment_id)


@router.post("/coverage/llm-review", response_model=CoverageOut, status_code=status.HTTP_202_ACCEPTED)
def llm_review(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, enqueuer: EnqueuerDep):
    """LLM-ревью отчёта (неблокирующее): противоречия, выводы без аргументов, двусмысленности."""
    project = get_project(db, principal, project_id)
    svc.start_llm_review(db, enqueuer, principal, project)
    return risks_svc.coverage(db, project)
