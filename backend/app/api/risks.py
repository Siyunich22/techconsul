import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.risk import CoverageOut, ReviewIn, RisksOut
from app.services import risks as svc
from app.services.projects import get_project

router = APIRouter(prefix="/projects/{project_id}", tags=["risks"])

EnqueuerDep = Annotated[svc.RisksEnqueuer, Depends(svc.get_risks_enqueuer)]
ItemId = Annotated[str, Path(pattern=r"^[0-9]+(\.[0-9]+)*$", max_length=20)]
RiskCode = Annotated[str, Path(pattern=r"^[A-Z][A-Z0-9]{0,5}$")]


@router.get("/risks", response_model=RisksOut)
def get_risks(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.risks(db, get_project(db, principal, project_id))


@router.post("/risks/run", response_model=RisksOut, status_code=status.HTTP_202_ACCEPTED)
def run_risks(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, enqueuer: EnqueuerDep):
    """Оценка 15 обязательных рисков, дополнительные риски и выводы раздела 5 по результатам пунктов."""
    project = get_project(db, principal, project_id)
    svc.start(db, enqueuer, principal, project)
    return svc.risks(db, project)


@router.put("/risks/{code}/review", response_model=RisksOut)
def review_risk(
    project_id: uuid.UUID, code: RiskCode, body: ReviewIn, principal: CurrentPrincipal, db: DbSession
):
    project = get_project(db, principal, project_id)
    svc.review_risk(db, principal, project, code, body.status)
    return svc.risks(db, project)


@router.put("/conclusions/review", response_model=RisksOut)
def review_conclusions(project_id: uuid.UUID, body: ReviewIn, principal: CurrentPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    svc.review_conclusions(db, principal, project, body.status)
    return svc.risks(db, project)


@router.put("/assessment/items/{item_id}/review", response_model=CoverageOut)
def review_item(
    project_id: uuid.UUID, item_id: ItemId, body: ReviewIn, principal: CurrentPrincipal, db: DbSession
):
    project = get_project(db, principal, project_id)
    svc.review_item(db, principal, project, item_id, body.status)
    return svc.coverage(db, project)


@router.get("/coverage", response_model=CoverageOut)
def get_coverage(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    """Матрица покрытия: пункт/риск/выводы → статус → источники → эксперт → утверждён; валидатор выпуска."""
    return svc.coverage(db, get_project(db, principal, project_id))
