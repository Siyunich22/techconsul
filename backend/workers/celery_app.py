import logging
import threading

from celery import Celery
from celery.signals import worker_ready

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "techocenka", broker=settings.redis_url, backend=settings.redis_url, include=["workers.tasks"]
)
celery_app.conf.update(
    task_acks_late=True,  # задачи идемпотентны; при падении воркера задача вернётся в очередь
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Almaty",
    enable_utc=True,
    task_default_queue="default",
    # Redis возвращает неподтверждённую задачу (acks_late) другому воркеру через visibility_timeout
    # (по умолчанию 1 ч) — меньше прогона оценки, задача выполнялась бы дважды. Берём с запасом больше
    # самого длинного лимита времени задачи.
    broker_transport_options={"visibility_timeout": 6 * 3600},
    result_backend_transport_options={"visibility_timeout": 6 * 3600},
    # Лимиты времени: зависшая задача не держит слот воркера вечно. Мягкий — исключение в задаче
    # (оркестратор помечает прогон прерванным), жёсткий — завершение процесса.
    task_soft_time_limit=2 * 3600,
    task_time_limit=2 * 3600 + 300,
    # ingest (разбор, OCR, LibreOffice) — параллельно; llm (паспорт, оценка — часы ожидания API) — своим
    # процессом, не занимая слоты загрузки; index (модель эмбеддингов в памяти) — отдельный воркер, где
    # query (поисковые запросы, ждут ответа до 30 с) обслуживается параллельно с индексацией пакета.
    task_routes={
        "documents.ingest": {"queue": "ingest"},
        "documents.index": {"queue": "index"},
        "references.ingest": {"queue": "index"},
        "index.embed_query": {"queue": "query"},
        "passport.extract": {"queue": "llm"},
        "assessment.run": {"queue": "llm"},
        "risks.run": {"queue": "llm"},
        "review.llm": {"queue": "llm"},
    },
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"


@worker_ready.connect
def _warm_up_embedder(sender, **_kwargs) -> None:
    """Воркер с очередью query загружает модель эмбеддингов сразу после старта (~25 с), в фоне:
    иначе первый поисковый запрос после деплоя ждёт загрузку и упирается в таймаут ожидания (30 с)."""
    queues = {q.name for q in sender.app.amqp.queues.consume_from.values()}  # sender — Consumer
    if "query" not in queues:
        return

    def load() -> None:
        from app.pipeline.embedder import get_embedder

        try:
            get_embedder().embed_query("прогрев")
        except Exception:
            logging.getLogger(__name__).exception("Прогрев модели эмбеддингов не удался")

    threading.Thread(target=load, name="embedder-warmup", daemon=True).start()
