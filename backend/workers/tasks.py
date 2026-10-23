"""Celery-задачи конвейера. Логика — в app.pipeline (тестируется без Celery)."""

import uuid

from app.core.db import get_sessionmaker
from app.core.storage import get_storage
from app.pipeline import passport, process, references
from workers.celery_app import celery_app


def enqueue_ingest(doc_id: uuid.UUID) -> None:
    ingest_document_task.delay(str(doc_id))


def enqueue_index(doc_id: uuid.UUID) -> None:
    index_document_task.delay(str(doc_id))


def enqueue_passport(passport_id: uuid.UUID, run_id: uuid.UUID) -> None:
    extract_passport_task.delay(str(passport_id), str(run_id))


@celery_app.task(name="documents.ingest", max_retries=2, default_retry_delay=30)
def ingest_document_task(doc_id: str) -> None:
    with get_sessionmaker()() as db:
        process.ingest_document(db, get_storage(), uuid.UUID(doc_id), enqueue_ingest, enqueue_index)


@celery_app.task(name="documents.index", max_retries=2, default_retry_delay=30)
def index_document_task(doc_id: str) -> None:
    with get_sessionmaker()() as db:
        process.index_document(db, uuid.UUID(doc_id))


@celery_app.task(name="index.embed_query")
def embed_query_task(text: str) -> list[float]:
    """Эмбеддинг поискового запроса для API (модель загружена только в worker-index)."""
    from app.pipeline.embedder import get_embedder

    return get_embedder().embed_query(text)


@celery_app.task(name="references.ingest")
def ingest_reference_task(ref_id: str) -> None:
    with get_sessionmaker()() as db:
        references.ingest_reference(db, get_storage(), uuid.UUID(ref_id))


@celery_app.task(name="passport.extract")
def extract_passport_task(passport_id: str, run_id: str) -> None:
    with get_sessionmaker()() as db:
        passport.run_extraction(db, uuid.UUID(passport_id), uuid.UUID(run_id))
