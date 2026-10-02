import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import CategorySource, DocumentStatus, ReferenceKind
from app.schemas.common import NonEmpty, ORMModel


class UploadInit(BaseModel):
    filename: NonEmpty
    size: int = Field(gt=0)
    relative_path: str = ""  # путь внутри загружаемой папки


class UploadInitOut(BaseModel):
    upload_id: uuid.UUID
    chunk_size: int
    parts_total: int


class DocumentOut(ORMModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    filename: str
    relative_path: str
    mime: str
    size: int
    kind: str
    pages: int | None
    category: str | None
    category_source: CategorySource | None
    category_confidence: float | None
    status: DocumentStatus
    error: str | None
    ocr_pages: int
    chunk_count: int
    meta_json: dict = Field(serialization_alias="meta")
    preview_key: str | None = Field(exclude=True)
    uploaded_at: datetime
    processed_at: datetime | None


class CategoryPatch(BaseModel):
    category: str


class PageOut(BaseModel):
    page_no: int
    pages_total: int
    sheet: str | None
    text: str
    tables: list
    ocr: bool
    has_image: bool


class SearchHitOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    category: str | None
    page: int | None
    page_end: int | None
    sheet: str | None
    cell_range: str | None
    text: str
    score: float


class CategoryCoverage(BaseModel):
    code: str
    title: str
    documents: int
    required_by: list[str]


class ItemCoverage(BaseModel):
    item_id: str
    title: str
    inputs: list[str]
    available: list[str]
    status: Literal["full", "partial", "none"]


class MissingCategory(BaseModel):
    code: str
    title: str
    partial_items: list[str]  # будут раскрыты частично
    blocked_items: list[str]  # не останется ни одного исходного документа


class MissingReference(BaseModel):
    kind: str
    items: list[str]


class CompletenessOut(BaseModel):
    documents_total: int
    documents_processing: int
    documents_failed: int
    categories: list[CategoryCoverage]
    items: list[ItemCoverage]
    missing: list[MissingCategory]
    missing_references: list[MissingReference]
    items_full: int
    items_partial: int
    items_none: int


class ReferenceDocOut(ORMModel):
    id: uuid.UUID
    title: str
    kind: ReferenceKind
    filename: str
    size: int
    pages: int | None
    valid_from: date | None
    valid_to: date | None
    status: DocumentStatus
    error: str | None
    chunk_count: int
    created_at: datetime
