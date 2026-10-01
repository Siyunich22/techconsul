"""Проверка реального окружения (make up). Запускается внутри контейнера api: make test-integration."""

import pytest

from app.core import health

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", sorted(health.CHECKS))
def test_dependency_reachable(name):
    health.CHECKS[name]()


def test_celery_ping_roundtrip():
    from workers.celery_app import ping

    assert ping.delay().get(timeout=15) == "pong"
