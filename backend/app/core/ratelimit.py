"""Ограничение частоты для входа, сброса пароля и регистрации (перебор паролей, рассылка писем).

Счётчики — в Redis (общие для всех процессов API), окно фиксированное. Redis недоступен — запрос
пропускается: ограничение защитное и не должно отключать вход.
"""

import logging
import time
from collections import defaultdict
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status

from app.core.config import get_settings

log = logging.getLogger(__name__)


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_s: int) -> bool:
        """Учитывает попытку; False — лимит окна исчерпан."""
        ...

    def reset(self, key: str) -> None: ...


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)

    def hit(self, key: str, limit: int, window_s: int) -> bool:
        try:
            k = f"rl:{key}"
            pipe = self._redis.pipeline()
            pipe.incr(k)
            pipe.expire(k, window_s, nx=True)
            count, _ = pipe.execute()
            return int(count) <= limit
        except Exception:
            log.warning("Ограничение частоты недоступно (Redis)", exc_info=True)
            return True

    def reset(self, key: str) -> None:
        try:
            self._redis.delete(f"rl:{key}")
        except Exception:
            log.warning("Ограничение частоты недоступно (Redis)", exc_info=True)


class MemoryRateLimiter:
    """Для тестов: тот же контракт в памяти процесса."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def hit(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits[key] if now - t < window_s]
        hits.append(now)
        self._hits[key] = hits
        return len(hits) <= limit

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter(get_settings().redis_url)


def enforce(limiter: RateLimiter, key: str, limit: int, window_s: int) -> None:
    if not limiter.hit(key, limit, window_s):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток. Повторите позже.",
            headers={"Retry-After": str(window_s)},
        )
