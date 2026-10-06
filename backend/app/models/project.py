import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk
from app.models.org import Expert, User


class TemplateVersion(UUIDPk, Base):
    __tablename__ = "template_versions"

    code: Mapped[str] = mapped_column(String(200), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    yaml_text: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings_json: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]"
    )  # предупреждения проверки
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectStatus(enum.StrEnum):
    draft = "draft"  # Черновик
    documents_uploaded = "documents_uploaded"  # Документы загружены
    analysis = "analysis"  # Анализ идёт
    review = "review"  # На проверке экспертов
    approved = "approved"  # Утверждён
    released = "released"  # Выпущен
    archived = "archived"  # Архив


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_org_updated", "org_id", "updated_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    customer_name: Mapped[str] = mapped_column(String(500))
    customer_bin: Mapped[str] = mapped_column(String(12), default="")
    industry: Mapped[str] = mapped_column(String(10), default="")  # секция ОКЭД
    region: Mapped[str] = mapped_column(String(200), default="")
    site: Mapped[str] = mapped_column(Text, default="")
    capacity_text: Mapped[str] = mapped_column(Text, default="")
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    template_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_versions.id"))
    bank_name: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"), default=ProjectStatus.draft, index=True
    )
    pre_archive_status: Mapped[ProjectStatus | None] = mapped_column(
        Enum(ProjectStatus, name="project_status")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    independence_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # необязательные пункты шаблона, включённые для проекта (например, 3.6 — TZ §13)
    enabled_optional_items: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    integral_risk: Mapped[str | None] = mapped_column(String(50))
    coverage_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    owner: Mapped[User] = relationship(lazy="joined")
    template_version: Mapped[TemplateVersion] = relationship(lazy="joined")
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )


class MemberRole(enum.StrEnum):
    lead = "lead"  # руководитель команды экспертов
    expert = "expert"


class ProjectMember(UUIDPk, Base):
    """Эксперт в команде проекта; assigned_items — id разделов шаблона (3.2…3.7, 4)."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "expert_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), index=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole, name="member_role"), default=MemberRole.expert)
    assigned_items: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)

    project: Mapped[Project] = relationship(back_populates="members")
    expert: Mapped[Expert] = relationship(lazy="joined")
