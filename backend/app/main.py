import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, auth, catalog, documents, health, org, projects
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.template_engine.loader import ensure_default_template

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.register_default_template:
        try:
            with get_sessionmaker()() as db:
                ensure_default_template(db)
        except Exception:  # БД может быть ещё недоступна; /health это покажет
            log.exception("Не удалось зарегистрировать шаблон ТЗ по умолчанию")
    yield


def create_app(*, register_default_template: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.register_default_template = register_default_template
    # /health — для docker healthcheck; /api/v1/health — для фронтенда через прокси.
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)
    for router in (
        auth.router,
        auth.me_router,
        org.router,
        projects.router,
        documents.router,
        catalog.router,
        admin.router,
    ):
        app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
