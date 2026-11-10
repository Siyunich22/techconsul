"""Celery-задачи конвейера. Логика — в app.pipeline (тестируется без Celery)."""

import threading
import uuid
from contextlib import contextmanager

from app.assessment.runner import run_items
from app.core.db import get_sessionmaker
from app.core.storage import get_storage
from app.pipeline import passport, process, references
from workers.celery_app import celery_app


def enqueue_ingest(doc_id: uuid.UUID) -> None:
    ingest_document_task.delay(str(doc_id))


def enqueue_index(doc_id: uuid.UUID) -> None:
    index_document_task.delay(str(doc_id))


def enqueue_assessment(run_id: uuid.UUID, only: list[str] | None = None) -> None:
    run_assessment_task.delay(str(run_id), only)


def enqueue_llm_review(run_id: uuid.UUID) -> None:
    llm_review_task.delay(str(run_id))


def enqueue_risks(run_id: uuid.UUID) -> None:
    run_risks_task.delay(str(run_id))


def enqueue_report(report_id: uuid.UUID) -> None:
    render_report_task.delay(str(report_id))


def enqueue_passport(passport_id: uuid.UUID, run_id: uuid.UUID) -> None:
    extract_passport_task.delay(str(passport_id), str(run_id))


@celery_app.task(name="documents.ingest")
def ingest_document_task(doc_id: str) -> None:
    with get_sessionmaker()() as db:
        process.ingest_document(db, get_storage(), uuid.UUID(doc_id), enqueue_ingest, enqueue_index)


# worker-index работает потоками: одновременно — одна индексация (CPU и память модели), остальные
# потоки свободны для поисковых запросов. Занято — задача возвращается в очередь, поток не ждёт.
_INDEX_SLOT = threading.Lock()


@contextmanager
def _index_slot(task):
    if not _INDEX_SLOT.acquire(blocking=False):
        raise task.retry(countdown=15)  # без лимита повторов: max_retries=None в декораторе задачи
    try:
        yield
    finally:
        _INDEX_SLOT.release()


@celery_app.task(name="documents.index", bind=True, max_retries=None)
def index_document_task(self, doc_id: str) -> None:
    with _index_slot(self), get_sessionmaker()() as db:
        process.index_document(db, uuid.UUID(doc_id))


@celery_app.task(name="index.embed_query", expires=60)  # запрос, который уже никто не ждёт, не считаем
def embed_query_task(text: str) -> list[float]:
    """Эмбеддинг поискового запроса для API (модель загружена только в worker-index)."""
    from app.pipeline.embedder import get_embedder

    return get_embedder().embed_query(text)


@celery_app.task(name="references.ingest", bind=True, max_retries=None)
def ingest_reference_task(self, ref_id: str) -> None:
    with _index_slot(self), get_sessionmaker()() as db:
        references.ingest_reference(db, get_storage(), uuid.UUID(ref_id))


@celery_app.task(name="passport.extract")
def extract_passport_task(passport_id: str, run_id: str) -> None:
    with get_sessionmaker()() as db:
        passport.run_extraction(db, uuid.UUID(passport_id), uuid.UUID(run_id))


@celery_app.task(name="assessment.run", soft_time_limit=5 * 3600, time_limit=5 * 3600 + 300)
def run_assessment_task(run_id: str, only: list[str] | None = None) -> None:
    """Пункты прогона по зависимостям; повторный запуск задачи берёт только пункты в статусе pending."""
    run_items(get_sessionmaker(), uuid.UUID(run_id), only)


@celery_app.task(name="report.render", soft_time_limit=20 * 60, time_limit=25 * 60)
def render_report_task(report_id: str) -> None:
    """DOCX + PDF отчёта (LibreOffice — в воркере разбора документов, очередь default)."""
    from app.services.reports import render

    with get_sessionmaker()() as db:
        render(db, get_storage(), uuid.UUID(report_id))


@celery_app.task(name="risks.run", soft_time_limit=2 * 3600, time_limit=2 * 3600 + 300)
def run_risks_task(run_id: str) -> None:
    """Риски (15 обязательных + дополнительные) и выводы раздела 5 по результатам пунктов."""
    from app.assessment.risks import run_stage

    run_stage(get_sessionmaker(), uuid.UUID(run_id))


@celery_app.task(name="review.llm", soft_time_limit=30 * 60, time_limit=35 * 60)
def llm_review_task(run_id: str) -> None:
    """LLM-ревью отчёта: замечания экспертам (не блокируют выпуск)."""
    from app.services.review import run_llm_review

    with get_sessionmaker()() as db:
        run_llm_review(db, uuid.UUID(run_id))
