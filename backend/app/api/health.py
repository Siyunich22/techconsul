from fastapi import APIRouter, Response, status

from app.core import health

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(response: Response) -> dict:
    """Состояние API и зависимостей. 200 + status=ok, если всё доступно, иначе 503."""
    checks = health.run_checks()
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}
