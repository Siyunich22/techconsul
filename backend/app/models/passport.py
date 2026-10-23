import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.org import User


class PassportStatus(enum.StrEnum):
    extracting = "extracting"
    draft = "draft"  # извлечён или правится, не подтверждён
    confirmed = "confirmed"  # подтверждён экспертом — основа для анализа пунктов
    failed = "failed"


class Passport(UUIDPk, Timestamps, Base):
    """Паспорт проекта (TZ §4.5 Б, §6): один на проект."""

    __tablename__ = "passports"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    status: Mapped[PassportStatus] = mapped_column(Enum(PassportStatus, name="passport_status"))
    data_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    ai_data_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )  # как извлёк ИИ — для сравнения с правкой эксперта
    discrepancies_json: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    sources_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )  # метка P12 → документ, страница, лист
    warnings_json: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    progress_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column()  # текущее извлечение (идемпотентность задачи)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    confirmer: Mapped[User | None] = relationship(foreign_keys=[confirmed_by], lazy="joined")
