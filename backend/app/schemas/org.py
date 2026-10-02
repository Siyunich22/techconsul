import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, computed_field

from app.models import UserRole
from app.schemas.common import NonEmpty, ORMModel


class OrgSettings(BaseModel):
    report_language: Literal["ru", "kk", "en"] = "ru"  # KZ/EN — фаза 9
    page_number_format: Literal["arabic", "page_of_total"] = "arabic"
    default_font: str = "Times New Roman"
    default_font_size: int = Field(12, ge=8, le=16)


class OrgOut(ORMModel):
    id: uuid.UUID
    name: str
    bin: str
    address: str
    phone: str
    email: str
    token_limit_month: int | None
    logo_key: str | None = Field(exclude=True)
    reference_docx_key: str | None = Field(exclude=True)
    settings_json: dict = Field(exclude=True)

    @computed_field
    @property
    def has_logo(self) -> bool:
        return self.logo_key is not None

    @computed_field
    @property
    def has_reference_docx(self) -> bool:
        return self.reference_docx_key is not None

    @computed_field
    @property
    def settings(self) -> OrgSettings:
        return OrgSettings.model_validate(self.settings_json or {})


class OrgPatch(BaseModel):
    name: NonEmpty | None = None
    address: str | None = None
    phone: str | None = None
    email: EmailStr | Literal[""] | None = None
    settings: OrgSettings | None = None


class OrgUserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    position: str
    is_active: bool
    password_hash: str | None = Field(exclude=True)
    last_login_at: datetime | None

    @computed_field
    @property
    def invitation_pending(self) -> bool:
        return self.password_hash is None


class InviteIn(BaseModel):
    email: EmailStr
    full_name: NonEmpty
    role: Literal[UserRole.manager, UserRole.expert, UserRole.observer] = UserRole.expert
    expert_id: uuid.UUID | None = None  # привязать к записи реестра экспертов


class InviteOut(BaseModel):
    user: OrgUserOut
    # Ссылка возвращается руководителю, чтобы передать её вручную, если письмо не дошло.
    invite_link: str


class ExpertBase(BaseModel):
    full_name: NonEmpty
    specialization: list[str] = []
    education: str = ""
    research_experience: str = ""
    years: int | None = Field(None, ge=0, le=80)
    email: EmailStr | Literal[""] = ""
    phone: str = ""
    contacts: str = ""


class ExpertIn(ExpertBase):
    pass


class ExpertPatch(BaseModel):
    full_name: NonEmpty | None = None
    specialization: list[str] | None = None
    education: str | None = None
    research_experience: str | None = None
    years: int | None = Field(None, ge=0, le=80)
    email: EmailStr | Literal[""] | None = None
    phone: str | None = None
    contacts: str | None = None
    is_active: bool | None = None


class ExpertOut(ExpertBase, ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    cv_filename: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
