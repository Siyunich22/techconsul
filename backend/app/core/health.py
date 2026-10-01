"""Проверки доступности зависимостей для /health."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import boto3
import redis
from botocore.config import Config
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine


def check_database() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
        has_vector = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if not has_vector:
            raise RuntimeError("расширение pgvector не установлено (нужен make migrate)")


def check_redis() -> None:
    settings = get_settings()
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.health_check_timeout_s,
        socket_timeout=settings.health_check_timeout_s,
    )
    try:
        client.ping()
    finally:
        client.close()


def check_storage() -> None:
    settings = get_settings()
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=settings.health_check_timeout_s,
            read_timeout=settings.health_check_timeout_s,
            retries={"max_attempts": 1},
        ),
    )
    s3.head_bucket(Bucket=settings.s3_bucket)


CHECKS: dict[str, Callable[[], None]] = {
    "database": check_database,
    "redis": check_redis,
    "storage": check_storage,
}


_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")


def run_checks() -> dict[str, str]:
    """Проверки идут параллельно; каждая ограничена таймаутом, чтобы /health никогда не зависал."""
    timeout = get_settings().health_check_timeout_s
    deadline = time.monotonic() + timeout
    futures = {name: _executor.submit(check) for name, check in CHECKS.items()}
    results: dict[str, str] = {}
    for name, future in futures.items():
        try:
            future.result(timeout=max(0.0, deadline - time.monotonic()))
            results[name] = "ok"
        except FutureTimeout:
            results[name] = f"error: timeout > {timeout}s"
        except Exception as exc:  # любая ошибка = компонент недоступен
            results[name] = f"error: {exc.__class__.__name__}: {exc}"[:300]
    return results
