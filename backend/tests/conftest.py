import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


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


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
