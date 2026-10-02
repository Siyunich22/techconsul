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
        self.multipart: dict[str, dict] = {}

    def put(self, key: str, data, content_type: str) -> None:
        assert key not in self.objects, "файлы неизменяемы: ключ не должен повторяться"
        self.objects[key] = (data.read(), content_type)

    def get(self, key: str) -> StoredObject:
        body, ctype = self.objects[key]
        return StoredObject(body=iter([body]), content_type=ctype, size=len(body))

    def download(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key][0])

    def create_multipart(self, key: str, content_type: str) -> str:
        upload_id = uuid.uuid4().hex
        self.multipart[upload_id] = {"key": key, "ctype": content_type, "parts": {}}
        return upload_id

    def upload_part(self, key: str, upload_id: str, part_no: int, data: bytes) -> str:
        self.multipart[upload_id]["parts"][part_no] = data
        return f'"etag-{part_no}"'

    def complete_multipart(self, key: str, upload_id: str, parts: dict[int, str]) -> None:
        mp = self.multipart.pop(upload_id)
        assert key not in self.objects
        self.objects[key] = (b"".join(mp["parts"][n] for n in sorted(parts)), mp["ctype"])

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.multipart.pop(upload_id, None)


class FakeLLM:
    """Мок LLM: по умолчанию «недоступен» (классификация по правилам); answers — очередь ответов."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.answers: list = []

    def structured(self, *, ctx, role, system, user, schema, max_tokens=4096):
        from app.llm.client import LLMUnavailable

        self.calls.append({"purpose": ctx.purpose, "role": role, "system": system, "user": user})
        if not self.answers:
            raise LLMUnavailable("FakeLLM: ответов нет")
        answer = self.answers.pop(0)
        return schema.model_validate(answer)


class SyncEnqueuer:
    """Выполняет конвейер сразу (вместо Celery) в той же тестовой сессии БД."""

    def __init__(self, db, storage) -> None:
        self.db, self.storage = db, storage
        self.ingested: list[uuid.UUID] = []

    def ingest(self, doc_id: uuid.UUID) -> None:
        from app.pipeline import process

        self.ingested.append(doc_id)
        process.ingest_document(
            self.db, self.storage, doc_id, self.ingest, lambda d: process.index_document(self.db, d)
        )


@pytest.fixture(autouse=True, scope="session")
def _test_settings():
    """Тесты не скачивают модель эмбеддингов: детерминированный HashEmbedder той же размерности."""
    from app.pipeline.embedder import get_embedder

    get_settings().embedder = "hash"
    get_embedder.cache_clear()
    yield


@pytest.fixture(autouse=True)
def llm():
    from app.llm.client import set_llm_override

    fake = FakeLLM()
    set_llm_override(fake)
    yield fake
    set_llm_override(None)


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
def enqueuer(db, storage) -> SyncEnqueuer:
    return SyncEnqueuer(db, storage)


@pytest.fixture
def app(db, storage, mailer, enqueuer):
    """Приложение всегда работает через тестовую сессию с откатом — никогда через dev-БД."""
    from app.api.admin import get_reference_enqueuer
    from app.pipeline import references
    from app.services.documents import get_enqueuer

    class SyncReferenceEnqueuer:
        def ingest(self, ref_id):
            references.ingest_reference(db, storage, ref_id)

    app = create_app(register_default_template=False)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_mailer] = lambda: mailer
    app.dependency_overrides[get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[get_reference_enqueuer] = lambda: SyncReferenceEnqueuer()
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


def upload_file(
    client: TestClient, project_id: str, filename: str, data: bytes, relative_path: str = ""
) -> dict:
    """Загрузка частями через API, как это делает фронтенд."""
    init = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        json={"filename": filename, "size": len(data), "relative_path": relative_path},
    )
    assert init.status_code == 201, init.text
    info = init.json()
    size = info["chunk_size"]
    for n in range(info["parts_total"]):
        part = client.put(
            f"/api/v1/projects/{project_id}/uploads/{info['upload_id']}/parts/{n + 1}",
            content=data[n * size : (n + 1) * size],
        )
        assert part.status_code == 204, part.text
    done = client.post(f"/api/v1/projects/{project_id}/uploads/{info['upload_id']}/complete")
    assert done.status_code == 200, done.text
    return done.json()


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory) -> Path:
    from tests.fixtures.sample_project.generate import generate

    return generate(tmp_path_factory.mktemp("sample_project"))


@pytest.fixture
def project(register) -> tuple[TestClient, dict]:
    c = register()
    p = c.post(
        "/api/v1/projects", json={"name": "Мукомольный завод 300 т/сут", "customer_name": "ТОО «Агро»"}
    )
    assert p.status_code == 201, p.text
    return c, p.json()
