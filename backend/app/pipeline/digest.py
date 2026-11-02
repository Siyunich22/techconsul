"""Выжимка документа (дешёвая модель, один раз на документ).

Агенты оценки получают выжимки всех документов проекта в общем контексте прогона (кэшируется для всех
пунктов) и сразу знают, где что лежит, — меньше обращений к поиску. Ссылаться на выжимку нельзя:
источник по-прежнему фрагмент из поиска или паспорт (правило 2).
"""

import logging
import uuid
from collections.abc import Iterable

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.client import LLM, CallContext, LLMError, get_llm
from app.llm.prompts import render
from app.models import Document, DocumentPage, DocumentStatus

log = logging.getLogger(__name__)


class DigestSection(BaseModel):
    title: str
    pages: str = Field(description="Страницы раздела, например «12–15», или лист Excel")


class DigestFact(BaseModel):
    fact: str = Field(description="Факт с числом и единицей")
    page: int | None = Field(default=None, description="Страница, где указан факт")


class DocDigest(BaseModel):
    summary: str = Field(description="2–4 предложения: что за документ и о чём")
    sections: list[DigestSection] = Field(default_factory=list, max_length=20)
    key_facts: list[DigestFact] = Field(default_factory=list, max_length=15)


def _source_text(pages: Iterable[tuple[int, str | None, str]], limit: int) -> str:
    parts, size = [], 0
    for no, sheet, text in pages:
        block = f"[{'лист «' + sheet + '»' if sheet else 'стр. ' + str(no)}]\n{text.strip()}\n"
        if size + len(block) > limit:
            parts.append(block[: max(0, limit - size)] + "\n[…документ обрезан…]")
            break
        parts.append(block)
        size += len(block)
    return "\n".join(parts)


def make_digest(
    doc: Document, pages: Iterable[tuple[int, str | None, str]], llm: LLM | None = None
) -> dict | None:
    """Выжимка или None (ИИ недоступен, пустой документ) — без выжимки всё работает, просто дороже."""
    text = _source_text(pages, get_settings().digest_max_chars)
    if not text.strip():
        return None
    try:
        digest = (llm or get_llm()).structured(
            ctx=CallContext(purpose="document_digest", org_id=doc.org_id, project_id=doc.project_id),
            role="cheap",
            system=render("document_digest"),
            user=f"Документ «{doc.filename}» (категория: {doc.category or 'не определена'}).\n\n{text}",
            schema=DocDigest,
            max_tokens=4096,
        )
    except LLMError as exc:
        log.info("Выжимка документа %s не построена: %s", doc.id, exc)
        return None
    return digest.model_dump(mode="json")


def ensure_digests(db: Session, org_id: uuid.UUID, project_id: uuid.UUID, llm: LLM | None = None) -> int:
    """Выжимки для обработанных документов проекта, у которых их ещё нет (документы, загруженные до
    появления выжимок, или ИИ был недоступен при загрузке). Возвращает число построенных."""
    docs = db.scalars(
        select(Document).where(
            Document.org_id == org_id,
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
            Document.digest_json.is_(None),
            Document.status.in_((DocumentStatus.recognized, DocumentStatus.indexed)),
        )
    ).all()
    built = 0
    for doc in docs:
        pages = db.execute(
            select(DocumentPage.page_no, DocumentPage.sheet, DocumentPage.text)
            .where(DocumentPage.document_id == doc.id)
            .order_by(DocumentPage.page_no)
        ).all()
        digest = make_digest(doc, pages, llm)
        if digest is not None:
            doc.digest_json = digest
            db.commit()
            built += 1
    return built
