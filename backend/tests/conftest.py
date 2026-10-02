import io
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.core.db import get_db
from app.core.mailer import get_mailer
from app.core.storage import StoredObject, get_storage
from app.main import create_app
from app.template_engine.loader import ensure_default_template

BACKEND_DIR = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(config, items):
    skips = {
        "live": ("RUN_LIVE_LLM", "живой LLM-тест: запуск только при RUN_LIVE_LLM=1"),
        "integration": ("RUN_INTEGRATION", "нужно поднятое окружение: запуск при RUN_INTEGRATION=1"),
    }
    for marker, (env_var, reason) in skips.items():
        if os.getenv(env_var) == "1":
            continue
        for item in items:
            if marker in item.keywords:
                item.add_marker(pytest.mark.skip(reason=reason))


# --- фейки внешних сервисов ---


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data, content_type: str) -> None:
        assert key not in self.objects, "файлы неизменяемы: ключ не должен повторяться"
        self.objects[key] = (data.read(), content_type)

    def get(self, key: str) -> StoredObject:
        body, ctype = self.objects[key]
        return StoredObject(body=iter([body]), content_type=ctype, size=len(body))


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, text: str) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})

    def last_link(self, to: str) -> str:
        msg = next(m for m in reversed(self.sent) if m["to"] == to)
        return next(w for w in msg["text"].split() if w.startswith("http"))


# --- база данных ---


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан — тесты с БД запускаются в контейнере api (make test)")
    db_name = make_url(url).database
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    engine = create_engine(url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine: Engine) -> Iterator[Session]:
    """Сессия внутри внешней транзакции: всё, что тест закоммитил, откатывается после теста."""
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        ensure_default_template(session)
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def app(db, storage, mailer):
    """Приложение всегда работает через тестовую сессию с откатом — никогда через dev-БД."""
    app = create_app(register_default_template=False)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_mailer] = lambda: mailer
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def new_client(app) -> Callable[[], TestClient]:
    """Отдельный клиент со своими cookie — для сценариев с несколькими пользователями."""
    return lambda: TestClient(app)


# --- сценарные помощники ---


def unique_bin() -> str:
    return f"{uuid.uuid4().int % 10**12:012d}"


@pytest.fixture
def register(new_client) -> Callable[..., TestClient]:
    def _register(
        org_name: str = "ТОО Исполнитель", email: str | None = None, password: str = "secret-pass-1"
    ):
        c = new_client()
        email = email or f"{uuid.uuid4().hex[:8]}@example.kz"
        resp = c.post(
            "/api/v1/auth/register",
            json={
                "organization": {"name": org_name, "bin": unique_bin(), "address": "г. Астана"},
                "user": {"full_name": "Иванов Иван", "email": email, "password": password},
            },
        )
        assert resp.status_code == 201, resp.text
        c.email = email  # type: ignore[attr-defined]
        c.password = password  # type: ignore[attr-defined]
        return c

    return _register


def pdf_file(name: str = "cv.pdf") -> tuple[str, io.BytesIO, str]:
    return (name, io.BytesIO(b"%PDF-1.4 test cv"), "application/pdf")
