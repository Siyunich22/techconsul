import asyncio
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, DbSession
from app.core.db import get_sessionmaker
from app.models import Project, RunStatus
from app.schemas.assessment import AssessmentOut, EstimateOut, ItemDetailOut, ItemOut, RerunIn, RunOut
from app.services import assessment as svc
from app.services.projects import get_project

router = APIRouter(prefix="/projects/{project_id}", tags=["assessment"])

EnqueuerDep = Annotated[svc.AssessmentEnqueuer, Depends(svc.get_assessment_enqueuer)]
ItemId = Annotated[str, Path(pattern=r"^[0-9]+(\.[0-9]+)*$", max_length=20)]
SSE_INTERVAL_S = 2.0


def get_db_factory() -> Callable[[], Session]:
    """Фабрика сессий для SSE: поток живёт дольше запроса, у каждого опроса — своя сессия."""
    return get_sessionmaker()


@router.get("/assessment", response_model=AssessmentOut)
def assessment(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.assessment(db, get_project(db, principal, project_id))


@router.get("/assessment/estimate", response_model=EstimateOut)
def estimate(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.estimate(db, get_project(db, principal, project_id))


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def start_run(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, enqueuer: EnqueuerDep):
    project = get_project(db, principal, project_id)
    return svc.run_out(db, svc.start_run(db, enqueuer, principal, project))


@router.post("/assessment/resume", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def resume(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, enqueuer: EnqueuerDep):
    """Довыполнить пункты последнего прогона, завершившиеся ошибкой."""
    project = get_project(db, principal, project_id)
    return svc.run_out(db, svc.resume_run(db, enqueuer, principal, project))


@router.get("/runs/{run_id}/events")
async def run_events(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    factory: Annotated[Callable[[], Session], Depends(get_db_factory)],
):
    """SSE-прогресс прогона (TZ §4.5 В): событие с прогрессом и статусами пунктов при каждом изменении."""
    project = get_project(db, principal, project_id)
    svc.get_run(db, project, run_id)  # проверка доступа до начала потока
    project_id_, org_id = project.id, project.org_id
    # Сессия запроса закрывается только после ответа, а поток живёт весь прогон: завершаем её транзакцию,
    # соединение возвращается в пул. Дальше каждый опрос — своя короткая сессия в пуле потоков.
    db.commit()

    def poll() -> AssessmentOut | None:
        with factory() as s:
            p = s.get(Project, project_id_)
            if p is None or p.org_id != org_id:
                return None
            return svc.assessment(s, p)

    async def events():
        last = None
        while True:
            state = await run_in_threadpool(poll)
            if state is None:
                return
            payload = state.model_dump_json()
            if payload != last:
                last = payload
                yield f"event: progress\ndata: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
            if (
                state.run is None
                or state.run.id != run_id
                or state.run.status
                not in (
                    RunStatus.queued.value,
                    RunStatus.running.value,
                )
            ):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(SSE_INTERVAL_S)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/assessment/items/{item_id}", response_model=ItemDetailOut)
def item(project_id: uuid.UUID, item_id: ItemId, principal: CurrentPrincipal, db: DbSession):
    return svc.item_detail(db, get_project(db, principal, project_id), item_id)


@router.post(
    "/assessment/items/{item_id}/rerun", response_model=ItemOut, status_code=status.HTTP_202_ACCEPTED
)
def rerun(
    project_id: uuid.UUID,
    item_id: ItemId,
    body: RerunIn,
    principal: CurrentPrincipal,
    db: DbSession,
    enqueuer: EnqueuerDep,
):
    project = get_project(db, principal, project_id)
    svc.rerun_item(db, enqueuer, principal, project, item_id, body.instruction)
    return next(i for i in svc.assessment(db, project).items if i.item_id == item_id)
