import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.config import get_settings
from app.core.storage import Storage, make_key
from app.models import (
    CategorySource,
    Chunk,
    Document,
    DocumentStatus,
    Project,
    ReferenceDoc,
    UploadSession,
)
from app.pipeline.ingest import is_supported, mime_for
from app.pipeline.process import project_size
from app.schemas.document import (
    CategoryCoverage,
    CompletenessOut,
    ItemCoverage,
    MissingCategory,
    MissingReference,
    UploadInit,
)
from app.services.audit import audit
from app.template_engine.loader import document_categories, iter_items, parse_template


class Enqueuer(Protocol):
    def ingest(self, doc_id: uuid.UUID) -> None: ...


class CeleryEnqueuer:
    def ingest(self, doc_id: uuid.UUID) -> None:
        from workers.tasks import enqueue_ingest

        enqueue_ingest(doc_id)


@lru_cache
def get_enqueuer() -> Enqueuer:
    return CeleryEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


# --- загрузка частями ---


def init_upload(
    db: Session, storage: Storage, principal: Principal, project: Project, data: UploadInit
) -> UploadSession:
    s = get_settings()
    if not is_supported(data.filename):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Формат файла «{data.filename}» не поддерживается"
        )
    if data.size > s.max_document_size_mb * 1024**2:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Файл больше {s.max_document_size_mb} МБ")
    pending = sum(
        u.size
        for u in db.scalars(
            select(UploadSession).where(
                UploadSession.project_id == project.id, UploadSession.completed_at.is_(None)
            )
        )
    )
    if project_size(db, project.id) + pending + data.size > s.max_project_size_gb * 1024**3:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Превышен лимит проекта {s.max_project_size_gb} ГБ"
        )
    key = make_key(
        "orgs", str(project.org_id), "projects", str(project.id), "documents", filename=data.filename
    )
    upload = UploadSession(
        org_id=project.org_id,
        project_id=project.id,
        filename=data.filename,
        relative_path=data.relative_path.strip("/"),
        size=data.size,
        chunk_size=s.upload_chunk_size,
        file_key=key,
        s3_upload_id=storage.create_multipart(key, mime_for(data.filename)),
        created_by=principal.user_id,
    )
    db.add(upload)
    db.commit()
    return upload


def get_upload(db: Session, project: Project, upload_id: uuid.UUID) -> UploadSession:
    upload = db.scalar(
        select(UploadSession).where(UploadSession.id == upload_id, UploadSession.project_id == project.id)
    )
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Загрузка не найдена")
    if upload.completed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Загрузка уже завершена")
    return upload


def parts_total(upload: UploadSession) -> int:
    return max(1, math.ceil(upload.size / upload.chunk_size))


def put_part(db: Session, storage: Storage, upload: UploadSession, part_no: int, data: bytes) -> None:
    total = parts_total(upload)
    if not 1 <= part_no <= total:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Номер части должен быть от 1 до {total}")
    expected = upload.chunk_size if part_no < total else upload.size - upload.chunk_size * (total - 1)
    if len(data) != expected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Размер части {len(data)} ≠ ожидаемому {expected}"
        )
    etag = storage.upload_part(upload.file_key, upload.s3_upload_id, part_no, data)
    upload.parts_json = {**upload.parts_json, str(part_no): etag}
    db.commit()


def complete_upload(
    db: Session,
    storage: Storage,
    enqueuer: Enqueuer,
    principal: Principal,
    project: Project,
    upload: UploadSession,
) -> Document:
    total = parts_total(upload)
    missing = [n for n in range(1, total + 1) if str(n) not in upload.parts_json]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Не загружены части: {missing[:10]}")
    storage.complete_multipart(
        upload.file_key, upload.s3_upload_id, {int(k): v for k, v in upload.parts_json.items()}
    )
    upload.completed_at = _now()
    doc = Document(
        org_id=project.org_id,
        project_id=project.id,
        filename=upload.filename,
        relative_path=upload.relative_path,
        mime=mime_for(upload.filename),
        size=upload.size,
        file_key=upload.file_key,
        uploaded_by=principal.user_id,
        status=DocumentStatus.uploaded,
    )
    db.add(doc)
    db.flush()
    audit(
        db,
        "document.uploaded",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        document_id=doc.id,
        filename=doc.filename,
        size=doc.size,
    )
    db.commit()
    enqueuer.ingest(doc.id)
    return doc


def abort_upload(db: Session, storage: Storage, upload: UploadSession) -> None:
    storage.abort_multipart(upload.file_key, upload.s3_upload_id)
    db.delete(upload)
    db.commit()


# --- документы ---


def list_documents(db: Session, project: Project) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.project_id == project.id,
                Document.org_id == project.org_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.uploaded_at, Document.relative_path, Document.filename)
        )
    )


def get_document(db: Session, project: Project, doc_id: uuid.UUID) -> Document:
    doc = db.scalar(
        select(Document).where(
            Document.id == doc_id,
            Document.project_id == project.id,
            Document.org_id == project.org_id,
            Document.deleted_at.is_(None),
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return doc


def set_category(
    db: Session, principal: Principal, project: Project, doc: Document, category: str
) -> Document:
    categories = document_categories(parse_template(project.template_version.yaml_text))
    if category not in categories:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Категория отсутствует в шаблоне ТЗ проекта"
        )
    previous = doc.category
    doc.category, doc.category_source, doc.category_confidence = category, CategorySource.user, 1.0
    audit(
        db,
        "document.category_changed",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        document_id=doc.id,
        previous=previous,
        category=category,
    )
    db.commit()
    return doc


def delete_document(db: Session, principal: Principal, project: Project, doc: Document) -> None:
    """Мягкое удаление документа и всех вложенных; файлы в хранилище не удаляются (неизменяемость)."""
    ids = [doc.id]
    frontier = [doc.id]
    while frontier:
        children = list(db.scalars(select(Document.id).where(Document.parent_id.in_(frontier))))
        ids.extend(children)
        frontier = children
    db.execute(update(Document).where(Document.id.in_(ids)).values(deleted_at=_now()))
    db.execute(delete(Chunk).where(Chunk.document_id.in_(ids)))
    audit(
        db,
        "document.deleted",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        document_id=doc.id,
        filename=doc.filename,
        nested=len(ids) - 1,
    )
    db.commit()


def reprocess(
    db: Session, enqueuer: Enqueuer, principal: Principal, project: Project, doc: Document
) -> Document:
    if doc.status in (DocumentStatus.uploaded, DocumentStatus.processing):
        raise HTTPException(status.HTTP_409_CONFLICT, "Документ уже обрабатывается")
    if doc.status == DocumentStatus.extracted:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Архив уже распакован — переобработайте вложенные файлы"
        )
    doc.status, doc.error = DocumentStatus.uploaded, None
    audit(
        db,
        "document.reprocess",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        document_id=doc.id,
    )
    db.commit()
    enqueuer.ingest(doc.id)
    return doc


# --- полнота пакета ---

READY = (DocumentStatus.recognized, DocumentStatus.indexed)
IN_PROGRESS = (DocumentStatus.uploaded, DocumentStatus.processing)


@dataclass
class _Counts:
    by_category: Counter
    processing: int
    failed: int
    total: int


def _counts(docs: list[Document]) -> _Counts:
    files = [d for d in docs if d.status != DocumentStatus.extracted]
    return _Counts(
        by_category=Counter(d.category for d in files if d.status in READY and d.category),
        processing=sum(d.status in IN_PROGRESS for d in files),
        failed=sum(d.status == DocumentStatus.error for d in files),
        total=len(files),
    )


def completeness(db: Session, project: Project) -> CompletenessOut:
    """Какие категории документов есть и каких не хватает для пунктов ТЗ (связь — inputs в YAML)."""
    template = parse_template(project.template_version.yaml_text)
    categories = document_categories(template)
    items = [i for i in iter_items(template, project.enabled_optional_items)]
    c = _counts(list_documents(db, project))

    required_by: dict[str, list[str]] = {code: [] for code in categories}
    item_cov: list[ItemCoverage] = []
    for item in items:
        if not item.inputs:
            continue
        for code in item.inputs:
            required_by.setdefault(code, []).append(item.id)
        available = [code for code in item.inputs if c.by_category[code]]
        state = "full" if len(available) == len(item.inputs) else "partial" if available else "none"
        item_cov.append(
            ItemCoverage(
                item_id=item.id, title=item.title, inputs=list(item.inputs), available=available, status=state
            )
        )

    by_item = {i.item_id: i for i in item_cov}
    missing = []
    for code, item_ids in required_by.items():
        if not item_ids or c.by_category[code]:
            continue
        missing.append(
            MissingCategory(
                code=code,
                title=categories.get(code, code),
                partial_items=[i for i in item_ids if by_item[i].status == "partial"],
                blocked_items=[i for i in item_ids if by_item[i].status == "none"],
            )
        )
    missing.sort(key=lambda m: -(len(m.partial_items) + 2 * len(m.blocked_items)))

    ref_kinds = {
        k.value
        for k in db.scalars(select(ReferenceDoc.kind).where(ReferenceDoc.status == DocumentStatus.indexed))
    }
    missing_refs: dict[str, list[str]] = {}
    for item in items:
        for kind in item.requires_reference:
            if kind not in ref_kinds:
                missing_refs.setdefault(kind, []).append(item.id)

    return CompletenessOut(
        documents_total=c.total,
        documents_processing=c.processing,
        documents_failed=c.failed,
        categories=[
            CategoryCoverage(
                code=code, title=title, documents=c.by_category[code], required_by=required_by.get(code, [])
            )
            for code, title in categories.items()
        ],
        items=item_cov,
        missing=missing,
        missing_references=[MissingReference(kind=k, items=v) for k, v in missing_refs.items()],
        items_full=sum(i.status == "full" for i in item_cov),
        items_partial=sum(i.status == "partial" for i in item_cov),
        items_none=sum(i.status == "none" for i in item_cov),
    )
