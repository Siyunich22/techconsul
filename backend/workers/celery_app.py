from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("techocenka", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,  # задачи идемпотентны; при падении воркера задача вернётся в очередь
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Almaty",
    enable_utc=True,
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
