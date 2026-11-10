import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI

from app.api import (
    admin,
    assessment,
    auth,
    catalog,
    documents,
    health,
    org,
    passport,
    projects,
    reports,
    risks,
)
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.storage import _s3
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
        if get_settings().s3_auto_create_bucket:
            try:
                _s3().ensure_bucket()
            except Exception:  # хранилище может быть ещё недоступно; /health это покажет
                log.exception("Не удалось проверить/создать бакет хранилища")
    yield


# Активное содержимое, которое браузер исполнил бы на нашем origin (SVG-логотип со скриптом и т.п.)
_ACTIVE_TYPES = (b"image/svg", b"text/html", b"application/xhtml", b"text/xml", b"application/xml")


class SecurityHeaders:
    """nosniff для всех ответов; для SVG/HTML — CSP sandbox: открытый по прямой ссылке файл не выполнит
    скрипт с cookie пользователя. Чистый ASGI — не мешает потоковым ответам (SSE, файлы)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"").lower()
                headers.append((b"x-content-type-options", b"nosniff"))
                if ctype.startswith(_ACTIVE_TYPES):
                    headers.append(
                        (
                            b"content-security-policy",
                            b"default-src 'none'; style-src 'unsafe-inline'; sandbox",
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class OriginCheck:
    """CSRF: изменяющий запрос из браузера с чужого сайта отклоняется по заголовку Origin (его страница
    подделать не может). Cookie SameSite=Lax не закрывают «соседние» поддомены и multipart-формы.
    Свой origin — Host/X-Forwarded-Host (запрос через прокси Next.js) или PUBLIC_URL.
    Запросы без Origin (не браузер, curl, тесты) пропускаются — у них нет чужих cookie."""

    def __init__(self, app) -> None:
        self.app = app
        self.public = urlsplit(get_settings().public_url).netloc.lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in _UNSAFE:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            origin = headers.get("origin")
            if origin is not None:
                allowed = {self.public, headers.get("host", "").lower()}
                allowed |= {h.strip().lower() for h in headers.get("x-forwarded-host", "").split(",") if h}
                if urlsplit(origin).netloc.lower() not in allowed - {""}:
                    body = json.dumps({"detail": "Запрос с чужого сайта отклонён"}, ensure_ascii=False)
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [(b"content-type", b"application/json; charset=utf-8")],
                        }
                    )
                    await send({"type": "http.response.body", "body": body.encode()})
                    return
        await self.app(scope, receive, send)


def create_app(*, register_default_template: bool = True) -> FastAPI:
    settings = get_settings()
    if settings.app_env != "dev" and not settings.cookie_secure:
        # cookie сессии без Secure уходят по http — вне dev только HTTPS (проверка в API: воркерам не нужна)
        raise RuntimeError("COOKIE_SECURE=true обязателен при APP_ENV ≠ dev")
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.register_default_template = register_default_template
    app.add_middleware(SecurityHeaders)
    app.add_middleware(OriginCheck)
    # /health — для docker healthcheck; /api/v1/health — для фронтенда через прокси.
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)
    for router in (
        auth.router,
        auth.me_router,
        org.router,
        projects.router,
        documents.router,
        passport.router,
        assessment.router,
        reports.router,
        risks.router,
        catalog.router,
        admin.router,
    ):
        app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
