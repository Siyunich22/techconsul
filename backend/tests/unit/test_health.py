import pytest
from fastapi.testclient import TestClient

from app.core import health
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """/health не обращается к get_db — тестовая БД не нужна."""
    return TestClient(create_app(register_default_template=False))


@pytest.fixture
def all_checks_ok(monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {name: (lambda: None) for name in health.CHECKS})


def test_health_ok(client, all_checks_ok):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis", "storage"}


def test_health_available_under_api_prefix(client, all_checks_ok):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_degraded_when_dependency_fails(client, monkeypatch):
    def broken():
        raise ConnectionError("redis недоступен")

    monkeypatch.setattr(health, "CHECKS", {"database": lambda: None, "redis": broken})
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"].startswith("error: ConnectionError")


def test_health_does_not_hang_on_stuck_dependency(client, monkeypatch):
    import threading
    import time

    from app.core.config import get_settings

    release = threading.Event()
    monkeypatch.setattr(get_settings(), "health_check_timeout_s", 0.2)
    monkeypatch.setattr(health, "CHECKS", {"database": lambda: release.wait(5)})
    started = time.monotonic()
    try:
        resp = client.get("/health")
    finally:
        release.set()
    assert time.monotonic() - started < 2
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"].startswith("error: timeout")
