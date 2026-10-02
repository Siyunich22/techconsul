"""Обработка загруженного документа: ingest → classify → index (TZ §5).

Две стадии (две очереди Celery): `ingest_document` — разбор, OCR, распаковка, классификация;
`index_document` — чанки и эмбеддинги. Обе идемпотентны: повторный запуск перезаписывает
страницы/чанки документа. Ошибка одного файла не влияет на остальные.
"""

import hashlib
import logging
import re
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.storage import Storage, make_key
from app.llm.client import CallContext
from app.models import (
    CategorySource,
    Chunk,
    Document,
    DocumentPage,
    DocumentStatus,
    Project,
    ProjectStatus,
)
from app.pipeline.classify import classify
from app.pipeline.embedder import get_embedder
from app.pipeline.index import chunk_pages
from app.pipeline.ingest import is_supported, mime_for, parse_file
from app.pipeline.ingest.base import IngestError, Page, Parsed, Table
from app.template_engine.loader import document_categories, parse_template

log = logging.getLogger(__name__)

Enqueue = Callable[[uuid.UUID], None]
EMBED_BATCH = 32


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def _fail(db: Session, doc: Document, message: str) -> None:
    doc.status = DocumentStatus.error
    doc.error = message[:2000]
    doc.processed_at = _now()
    db.commit()


def ingest_document(
    db: Session, storage: Storage, doc_id: uuid.UUID, enqueue_ingest: Enqueue, enqueue_index: Enqueue
) -> None:
    doc = db.get(Document, doc_id)
    if doc is None or doc.deleted_at is not None:
        return
    doc.status, doc.error = DocumentStatus.processing, None
    db.commit()
    try:
        with tempfile.TemporaryDirectory(prefix="ingest_") as tmp:
            work = Path(tmp)
            src = work / f"source{Path(doc.filename).suffix.lower()}"
            storage.download(doc.file_key, src)
            doc.sha256 = _sha256(src)
            parsed = parse_file(src, doc.filename, work)
            _save_children(db, storage, doc, parsed, enqueue_ingest)
            if parsed.is_container:
                doc.kind, doc.status, doc.processed_at = parsed.kind, DocumentStatus.extracted, _now()
                doc.meta_json = parsed.meta
                db.commit()
                return
            _save_pages(db, storage, doc, parsed)
    except IngestError as exc:
        _fail(db, doc, str(exc))
        return
    except Exception as exc:  # неожиданная ошибка — фиксируем у документа, прогон остальных продолжается
        log.exception("Ошибка обработки документа %s", doc_id)
        db.rollback()
        _fail(db, doc, f"Внутренняя ошибка обработки: {exc.__class__.__name__}: {exc}")
        return

    if doc.category_source != CategorySource.user:
        _classify(db, doc, parsed)
    doc.status = DocumentStatus.recognized
    db.commit()
    enqueue_index(doc.id)


def _save_children(db: Session, storage: Storage, doc: Document, parsed: Parsed, enqueue: Enqueue) -> None:
    for child in parsed.children:
        rel = f"{doc.relative_path or doc.filename}/{child.relative_path}"
        key = make_key(
            "orgs", str(doc.org_id), "projects", str(doc.project_id), "documents", filename=child.name
        )
        with open(child.path, "rb") as f:
            storage.put(key, f, mime_for(child.name))
        size = child.path.stat().st_size
        supported = is_supported(child.name)
        child_doc = Document(
            org_id=doc.org_id,
            project_id=doc.project_id,
            parent_id=doc.id,
            filename=child.name,
            relative_path=rel,
            mime=mime_for(child.name),
            size=size,
            file_key=key,
            uploaded_by=doc.uploaded_by,
            status=DocumentStatus.uploaded if supported else DocumentStatus.error,
            error=None
            if supported
            else f"Формат {Path(child.name).suffix or 'без расширения'} не поддерживается",
        )
        db.add(child_doc)
        db.flush()
        if supported:
            db.commit()
            enqueue(child_doc.id)
    db.commit()


def _save_pages(db: Session, storage: Storage, doc: Document, parsed: Parsed) -> None:
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
    for page in parsed.pages:
        db.add(
            DocumentPage(
                document_id=doc.id,
                page_no=page.no,
                sheet=page.sheet,
                text=page.text,
                tables_json=[t.as_dict() for t in page.tables],
                ocr=page.ocr,
            )
        )
    for preview, ctype in ((parsed.preview_pdf, "application/pdf"), (parsed.preview_png, "image/png")):
        if preview is not None and preview.exists():
            key = make_key(
                "orgs", str(doc.org_id), "projects", str(doc.project_id), "previews", filename=preview.name
            )
            with open(preview, "rb") as f:
                storage.put(key, f, ctype)
            doc.preview_key = key
    doc.kind = parsed.kind
    doc.pages = len(parsed.pages)
    doc.ocr_pages = sum(p.ocr for p in parsed.pages)
    doc.meta_json = parsed.meta
    if not parsed.text.strip():
        doc.meta_json = {
            **parsed.meta,
            "warning": "Текст не извлечён (пустой документ или нераспознанный скан)",
        }


def _classify(db: Session, doc: Document, parsed: Parsed) -> None:
    project = db.get(Project, doc.project_id)
    categories = document_categories(parse_template(project.template_version.yaml_text))
    result = classify(
        categories,
        doc.filename,
        doc.relative_path,
        parsed.text,
        sheets=parsed.meta.get("sheets"),
        ctx=CallContext(purpose="classify_document", org_id=doc.org_id, project_id=doc.project_id),
    )
    doc.category = result.category
    doc.category_confidence = result.confidence
    doc.category_source = CategorySource(result.source)


_ROW_RE = re.compile(r"^\[(\d+)\] ")


def _pages_from_db(doc: Document) -> list[Page]:
    """Восстанавливает страницы для чанкинга; у листов Excel — номера строк для адресов ячеек."""
    pages = []
    for p in doc.pages_rel:
        page = Page(
            no=p.page_no,
            text=p.text,
            sheet=p.sheet,
            ocr=p.ocr,
            tables=[
                Table(rows=t.get("rows", []), title=t.get("title"), cell_range=t.get("cell_range"))
                for t in p.tables_json
            ],
        )
        if doc.kind == "sheet":
            rows = [(int(m.group(1)), line) for line in p.text.splitlines() if (m := _ROW_RE.match(line))]
            cell_range = next((t.cell_range for t in page.tables if t.cell_range), None)
            page.rows = rows
            page.max_col = re.sub(r"\d+", "", cell_range.split(":")[1]) if cell_range else "A"
        pages.append(page)
    return pages


def index_document(db: Session, doc_id: uuid.UUID) -> None:
    doc = db.get(Document, doc_id)
    if (
        doc is None
        or doc.deleted_at is not None
        or doc.status not in (DocumentStatus.recognized, DocumentStatus.indexed)
    ):
        return
    try:
        drafts = chunk_pages(_pages_from_db(doc))
        embedder = get_embedder()
        db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        for start in range(0, len(drafts), EMBED_BATCH):
            batch = drafts[start : start + EMBED_BATCH]
            vectors = embedder.embed_passages([d.text for d in batch])
            for i, (draft, vec) in enumerate(zip(batch, vectors, strict=True)):
                db.add(
                    Chunk(
                        org_id=doc.org_id,
                        project_id=doc.project_id,
                        document_id=doc.id,
                        seq=start + i,
                        page=draft.page,
                        page_end=draft.page_end,
                        sheet=draft.sheet,
                        cell_range=draft.cell_range,
                        text=draft.text,
                        token_count=draft.token_count,
                        embedding=vec,
                    )
                )
        doc.chunk_count = len(drafts)
        doc.status = DocumentStatus.indexed
        doc.processed_at = _now()
        _mark_project_documents_uploaded(db, doc.project_id)
        db.commit()
    except Exception as exc:
        log.exception("Ошибка индексации документа %s", doc_id)
        db.rollback()
        _fail(db, doc, f"Ошибка индексации: {exc.__class__.__name__}: {exc}")


def _mark_project_documents_uploaded(db: Session, project_id: uuid.UUID) -> None:
    project = db.get(Project, project_id)
    if project is not None and project.status == ProjectStatus.draft:
        project.status = ProjectStatus.documents_uploaded


def project_size(db: Session, project_id: uuid.UUID) -> int:
    """Объём загруженного пользователем (без распакованных из архивов файлов)."""
    return (
        db.scalar(
            select(func.coalesce(func.sum(Document.size), 0)).where(
                Document.project_id == project_id, Document.parent_id.is_(None), Document.deleted_at.is_(None)
            )
        )
        or 0
    )
