"""Ревью экспертов (фаза 7): правки с версиями, история, комментарии, LLM-ревью."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.assessment import DisclosureStatus, Gap, ResultTable, SectionPayload


class ItemEdit(BaseModel):
    """Правка пункта экспертом — новая версия (author=user). Не переданное поле остаётся как было."""

    disclosure_status: DisclosureStatus | None = None
    summary: str | None = Field(default=None, max_length=5000)
    analysis_md: str | None = Field(default=None, max_length=100_000)
    remarks: list[str] | None = None
    recommendations: list[str] | None = None
    assumptions: list[str] | None = None
    gap: Gap | None = None
    tables: list[ResultTable] | None = None
    note: str = Field(default="", max_length=2000)  # что исправлено — в журнал


class RiskEdit(BaseModel):
    probability: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=3)
    rationale: str = Field(min_length=1, max_length=20_000)
    mitigation: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=2000)


class VersionOut(BaseModel):
    version: int
    author: str  # ai | system | user
    editor: str | None = None
    instruction: str = ""
    status: str
    review_status: str
    created_at: datetime
    payload: SectionPayload | None = None


class CommentIn(BaseModel):
    ref: str = Field(pattern=r"^([0-9]+(\.[0-9]+)*|[A-Z][A-Z0-9]{0,5})$", max_length=20)
    text: str = Field(min_length=1, max_length=5000)


class CommentOut(BaseModel):
    id: uuid.UUID
    ref: str
    author: str
    text: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class ReviewIssue(BaseModel):
    """Замечание LLM-ревью: неблокирующее, эксперт решает сам."""

    kind: Literal["contradiction", "unsupported", "ambiguous", "excess"] = Field(
        description="contradiction — противоречие между разделами; unsupported — вывод без аргументов; "
        "ambiguous — двусмысленная формулировка; excess — информация, не ведущая к выводу"
    )
    refs: list[str] = Field(default_factory=list, description="Пункты/риски, к которым относится замечание")
    message: str = Field(description="Суть замечания с цифрами: что с чем не сходится и где")


class LlmReview(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=40)
