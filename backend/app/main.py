from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    # /health — для docker healthcheck; /api/v1/health — для фронтенда через прокси.
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)
    return app


app = create_app()
