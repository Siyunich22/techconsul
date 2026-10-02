import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk

EMBEDDING_DIM = 1024  # = Settings.embedding_dim; смена модели эмбеддингов требует миграции


class DocumentStatus(enum.StrEnum):
    uploaded = "uploaded"  # файл в хранилище, ждёт обработки
    processing = "processing"
    recognized = "recognized"  # текст и таблицы извлечены
    indexed = "indexed"  # чанки и эмбеддинги записаны
    extracted = "extracted"  # контейнер (архив, письмо) — распакован в дочерние документы
    error = "error"


class CategorySource(enum.StrEnum):
    rules = "rules"
    llm = "llm"
    user = "user"


class UploadSession(UUIDPk, Base):
    """Загрузка файла частями напрямую в S3 multipart upload."""

    __tablename__ = "upload_sessions"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    relative_path: Mapped[str] = mapped_column(String(1000), default="")  # путь в загруженной папке
    size: Mapped[int] = mapped_column(BigInteger)
    chunk_size: Mapped[int] = mapped_column(Integer)
    file_key: Mapped[str] = mapped_column(String(1024))
    s3_upload_id: Mapped[str] = mapped_column(String(1024))
    parts_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # {номер: etag}
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(UUIDPk, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_project_status", "project_id", "status"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    relative_path: Mapped[str] = mapped_column(String(1000), default="")  # путь в папке/архиве
    mime: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    file_key: Mapped[str] = mapped_column(String(1024))
    preview_key: Mapped[str | None] = mapped_column(String(1024))  # PDF-версия офисного файла / PNG чертежа
    kind: Mapped[str] = mapped_column(
        String(30), default=""
    )  # pdf, office, sheet, image, cad, email, archive…
    pages: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(100))
    category_source: Mapped[CategorySource | None] = mapped_column(
        Enum(CategorySource, name="category_source")
    )
    category_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.uploaded
    )
    error: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Удаление из проекта — мягкое: файл в хранилище не трогаем (файлы неизменяемы).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages_rel: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_no"
    )


class DocumentPage(UUIDPk, Base):
    """Текст страницы (лист для xlsx, слайд для pptx) — для источников и предпросмотра с подсветкой."""

    __tablename__ = "document_pages"
    __table_args__ = (Index("ix_document_pages_doc_page", "document_id", "page_no", unique=True),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)  # с 1
    sheet: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")
    tables_json: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    ocr: Mapped[bool] = mapped_column(default=False)

    document: Mapped[Document] = relationship(back_populates="pages_rel")


class Chunk(UUIDPk, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(300))
    cell_range: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('russian', text)", persisted=True))


class ReferenceKind(enum.StrEnum):
    ndt = "ndt"  # справочники наилучших доступных техник РК
    law = "law"  # кодексы и законы (Экологический кодекс, КоАП)
    norm = "norm"  # СН/СП РК
    standard = "standard"  # отраслевые стандарты
    other = "other"


class ReferenceDoc(UUIDPk, Timestamps, Base):
    """Справочная библиотека платформы — глобальная, ведёт администратор."""

    __tablename__ = "reference_docs"

    title: Mapped[str] = mapped_column(String(500))
    kind: Mapped[ReferenceKind] = mapped_column(Enum(ReferenceKind, name="reference_kind"))
    filename: Mapped[str] = mapped_column(String(500))
    file_key: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(BigInteger)
    pages: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.uploaded
    )
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ReferenceChunk(UUIDPk, Base):
    __tablename__ = "reference_chunks"
    __table_args__ = (
        Index("ix_reference_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_reference_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    reference_doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_docs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('russian', text)", persisted=True))


class LlmCall(UUIDPk, Base):
    """Учёт каждого вызова LLM (TZ §9): токены, стоимость, назначение."""

    __tablename__ = "llm_calls"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    purpose: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    status: Mapped[str] = mapped_column(String(30), default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
