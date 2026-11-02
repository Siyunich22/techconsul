import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"  # все пункты обработаны (часть могла завершиться с ошибкой)
    failed = "failed"  # прогон не состоялся (например, ИИ недоступен)
    cancelled = "cancelled"


class ItemStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class ResultAuthor(enum.StrEnum):
    ai = "ai"
    system = "system"  # информационный пункт из данных проекта
    user = "user"


class ReviewStatus(enum.StrEnum):
    ai_draft = "ai_draft"  # черновик ИИ
    in_review = "in_review"  # на проверке
    approved = "approved"  # утверждён экспертом (фаза 7)


class AssessmentRun(UUIDPk, Timestamps, Base):
    """Прогон оценки проекта (TZ §6). Новый прогон не удаляет старые — история сохраняется."""

    __tablename__ = "assessment_runs"
    __table_args__ = (Index("ix_assessment_runs_project_created", "project_id", "created_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status"), default=RunStatus.queued)
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    web_search: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    # паспорт на момент запуска — агенты работают с зафиксированной версией
    passport_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class SectionResult(UUIDPk, Timestamps, Base):
    """Результат пункта ТЗ в прогоне. Пересборка пункта — новая версия (старые не перезаписываются)."""

    __tablename__ = "section_results"
    __table_args__ = (
        UniqueConstraint("run_id", "item_id", "version"),
        Index("ix_section_results_project_item", "project_id", "item_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_runs.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"), default=ItemStatus.pending
    )
    payload_json: Mapped[dict | None] = mapped_column(JSONB)
    author: Mapped[ResultAuthor] = mapped_column(
        Enum(ResultAuthor, name="result_author"), default=ResultAuthor.ai
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.ai_draft
    )
    instruction: Mapped[str] = mapped_column(Text, default="")  # доп. указание эксперта при пересборке
    error: Mapped[str | None] = mapped_column(Text)
    validator_warnings_json: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # отпечаток входных данных пункта: совпал с прошлым прогоном — результат берётся оттуда без ИИ
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    reused_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("section_results.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(UUIDPk, Base):
    """Источник прогона: метка S12 → документ+страница / справочник / URL с датой обращения."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("run_id", "sid"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_runs.id", ondelete="CASCADE"), index=True
    )
    sid: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(20))  # doc | reference | web | project
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(300))
    cell_range: Mapped[str | None] = mapped_column(String(100))
    reference_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reference_docs.id", ondelete="SET NULL")
    )
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
