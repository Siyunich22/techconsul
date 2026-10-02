import enum
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import Timestamps, UUIDPk


class UserRole(enum.StrEnum):
    admin = "admin"  # администратор платформы
    manager = "manager"  # руководитель проекта (Исполнитель)
    expert = "expert"
    observer = "observer"  # Заказчик/Банк — фаза 9


class Organization(UUIDPk, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(500))
    bin: Mapped[str] = mapped_column(String(12), unique=True)
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    logo_key: Mapped[str | None] = mapped_column(String(1024))
    reference_docx_key: Mapped[str | None] = mapped_column(String(1024))
    token_limit_month: Mapped[int | None] = mapped_column(Integer)
    # язык отчёта, формат номеров страниц, шрифт по умолчанию (TZ §4.2 «Настройки»)
    settings_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # None — приглашён, пароль не задан
    full_name: Mapped[str] = mapped_column(String(300))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    position: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(lazy="joined")


class TokenPurpose(enum.StrEnum):
    reset = "reset"
    invite = "invite"


class UserToken(UUIDPk, Base):
    """Одноразовые токены: сброс пароля, приглашение. В БД хранится только SHA-256 токена."""

    __tablename__ = "user_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[TokenPurpose] = mapped_column(Enum(TokenPurpose, name="token_purpose"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshSession(UUIDPk, Base):
    """Сессия refresh-токена (id = jti). Ротация при каждом refresh, отзыв при logout."""

    __tablename__ = "refresh_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Expert(UUIDPk, Timestamps, Base):
    """Реестр экспертов организации (сведения для п. 3.1.2 отчёта)."""

    __tablename__ = "experts"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    full_name: Mapped[str] = mapped_column(String(300))
    specialization: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    education: Mapped[str] = mapped_column(Text, default="")
    research_experience: Mapped[str] = mapped_column(Text, default="")
    years: Mapped[int | None] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    contacts: Mapped[str] = mapped_column(Text, default="")
    cv_file_key: Mapped[str | None] = mapped_column(String(1024))
    cv_filename: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User | None] = relationship(lazy="joined")


class AuditLog(UUIDPk, Base):
    __tablename__ = "audit_log"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
