"""Маршруты и повторы задач Celery (без брокера)."""

import pytest
from celery.exceptions import Retry

from workers import tasks
from workers.celery_app import celery_app


def test_routes_separate_llm_and_query_queues():
    routes = celery_app.conf.task_routes
    assert routes["assessment.run"]["queue"] == routes["passport.extract"]["queue"] == "llm"
    assert routes["index.embed_query"]["queue"] == "query"  # запрос не ждёт индексацию пакета
    assert routes["documents.index"]["queue"] == "index"


def test_busy_index_slot_requeues_without_retry_limit():
    """Пока идёт одна индексация, вторая возвращается в очередь — сколько угодно раз, а не 3."""
    assert tasks.index_document_task.max_retries is None
    assert tasks.ingest_reference_task.max_retries is None
    with tasks._INDEX_SLOT, pytest.raises(Retry):
        tasks.index_document_task.apply(args=["00000000-0000-0000-0000-000000000000"], throw=True)
