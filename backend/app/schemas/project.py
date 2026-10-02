import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from app.core.reference import CURRENCIES, OKED_SECTIONS
from app.models import MemberRole, ProjectStatus
from app.schemas.common import NonEmpty, OptionalBin, ORMModel


class Independence(BaseModel):
    """Чек-лист конфликта интересов (п. 6.4 ТЗ банка) → п. 2.1 отчёта."""

    affiliated: bool | None = None  # аффилированность Исполнителя с Заказчиком
    participated_in_docs: bool | None = None  # участие в разработке БП/ТЭО
    is_supplier: bool | None = None  # Исполнитель — поставщик по проекту
    justification: str = ""  # обязательно при «да» на любой из трёх вопросов
    joint_experience: str = ""  # опыт совместной деятельности

    @computed_field
    @property
    def has_conflict(self) -> bool:
        return any((self.affiliated, self.participated_in_docs, self.is_supplier))

    @computed_field
    @property
    def is_complete(self) -> bool:
        """Блок заполнен: на все три вопроса дан ответ, при конфликте есть обоснование.
        Без этого проект не может быть выпущен (проверка валидатора, фаза 7)."""
        answered = None not in (self.affiliated, self.participated_in_docs, self.is_supplier)
        return answered and (not self.has_conflict or bool(self.justification.strip()))


class MemberIn(BaseModel):
    expert_id: uuid.UUID
    role: MemberRole = MemberRole.expert
    assigned_items: list[str] = []


def _check_industry(v: str | None) -> str | None:
    if v and v not in OKED_SECTIONS:
        raise ValueError("Неизвестная секция ОКЭД")
    return v


def _check_currency(v: str | None) -> str | None:
    if v is not None and v not in CURRENCIES:
        raise ValueError(f"Валюта должна быть одной из: {', '.join(CURRENCIES)}")
    return v


class ProjectFields(BaseModel):
    customer_bin: OptionalBin = ""
    industry: str = ""
    region: str = ""
    site: str = ""
    capacity_text: str = ""
    budget_amount: Decimal | None = Field(None, ge=0, max_digits=20, decimal_places=2)
    currency: str = "KZT"
    bank_name: str = ""

    _industry = field_validator("industry")(_check_industry)
    _currency = field_validator("currency")(_check_currency)


class ProjectCreate(ProjectFields):
    name: NonEmpty
    customer_name: NonEmpty
    template_version_id: uuid.UUID | None = None  # None — шаблон по умолчанию
    enabled_optional_items: list[str] = []
    independence: Independence = Independence()
    members: list[MemberIn] = []


class ProjectPatch(BaseModel):
    name: NonEmpty | None = None
    customer_name: NonEmpty | None = None
    customer_bin: OptionalBin | None = None
    industry: str | None = None
    region: str | None = None
    site: str | None = None
    capacity_text: str | None = None
    budget_amount: Decimal | None = Field(None, ge=0, max_digits=20, decimal_places=2)
    currency: str | None = None
    bank_name: str | None = None
    owner_id: uuid.UUID | None = None
    enabled_optional_items: list[str] | None = None
    independence: Independence | None = None

    _industry = field_validator("industry")(_check_industry)
    _currency = field_validator("currency")(_check_currency)


class UserBrief(ORMModel):
    id: uuid.UUID
    full_name: str


class TemplateBrief(ORMModel):
    id: uuid.UUID
    code: str
    title: str


class ExpertBrief(ORMModel):
    id: uuid.UUID
    full_name: str
    specialization: list[str]
    user_id: uuid.UUID | None


class MemberOut(ORMModel):
    id: uuid.UUID
    role: MemberRole
    assigned_items: list[str]
    expert: ExpertBrief


class ProjectListItem(ORMModel):
    id: uuid.UUID
    name: str
    customer_name: str
    industry: str
    region: str
    budget_amount: Decimal | None
    currency: str
    status: ProjectStatus
    coverage_pct: Decimal | None
    integral_risk: str | None
    owner: UserBrief
    created_at: datetime
    updated_at: datetime


class ProjectOut(ProjectListItem):
    customer_bin: str
    site: str
    capacity_text: str
    bank_name: str
    template_version: TemplateBrief
    enabled_optional_items: list[str]
    independence_json: dict = Field(exclude=True)
    members: list[MemberOut]

    @computed_field
    @property
    def independence(self) -> Independence:
        return Independence.model_validate(self.independence_json or {})


SortField = Literal[
    "name", "customer_name", "budget_amount", "status", "coverage_pct", "created_at", "updated_at"
]


class AssignableSectionOut(BaseModel):
    id: str
    title: str
    expert_role: str | None
    optional: bool
    extension: bool
