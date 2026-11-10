import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk


class ReportStatus(enum.StrEnum):
    draft = "draft"  # черновик (водяной знак «Черновик»)
    released = "released"  # выпущенная версия — только после утверждения всех пунктов (фаза 7)


class RenderStatus(enum.StrEnum):
    queued = "queued"
    rendering = "rendering"
    ready = "ready"
    failed = "failed"


class ReportVersion(UUIDPk, Timestamps, Base):
    """Версия отчёта (TZ §6): v0.1, v0.2… — черновики, v1.0 — выпуск. Файлы версии неизменяемы (правило 8):
    новая сборка — новая версия с новыми ключами хранилища."""

    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment_runs.id", ondelete="SET NULL"))
    number: Mapped[int] = mapped_column(Integer)  # порядковый номер сборки в проекте
    version: Mapped[str] = mapped_column(String(20))  # «0.1»
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"), default=ReportStatus.draft
    )
    render_status: Mapped[RenderStatus] = mapped_column(
        Enum(RenderStatus, name="render_status"), default=RenderStatus.queued
    )
    error: Mapped[str | None] = mapped_column(Text)
    docx_key: Mapped[str | None] = mapped_column(String(1024))
    pdf_key: Mapped[str | None] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(300), default="")  # без расширения
    sha256: Mapped[str | None] = mapped_column(String(64))  # DOCX
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
