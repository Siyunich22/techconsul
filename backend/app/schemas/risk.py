"""Риски (TZ §5.4) и матрица покрытия с валидатором (TZ §4.5 Е, §5.6)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.assessment import SectionPayload, SourceOut


class RiskPayload(BaseModel):
    """Ответ модели по одному риску: вероятность 1–5, влияние 1–3 (шкалы из шаблона)."""

    probability: int = Field(ge=1, le=5, description="Вероятность по шкале шаблона, 1–5")
    impact: int = Field(ge=1, le=3, description="Масштаб влияния по шкале шаблона, 1–3")
    rationale: str = Field(description="Аргументированное обоснование со ссылками [S12] на источники")
    mitigation: list[str] = Field(description="Мероприятия по управлению, снижению, устранению риска")
    related_items: list[str] = Field(
        default_factory=list, description="Пункты ТЗ, на которые опирается оценка"
    )

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, v: str) -> str:
        if len(v.strip()) < 40:
            raise ValueError("Обоснование риска слишком короткое — нужна аргументация с фактами")
        return v.strip()

    @field_validator("mitigation")
    @classmethod
    def _mitigation(cls, v: list[str]) -> list[str]:
        v = [m.strip() for m in v if m.strip()]
        if not v:
            raise ValueError("Нужно хотя бы одно мероприятие по управлению риском")
        return v


class ExtraRisk(RiskPayload):
    title: str = Field(description="Формулировка риска: «Риск …»")
    group: Literal["4.1", "4.2"] = Field(description="4.1 — технический, 4.2 — закупки")


class ExtraRisks(BaseModel):
    risks: list[ExtraRisk] = Field(default_factory=list, max_length=5)


class Integral(BaseModel):
    """Интегральный уровень риска проекта — детерминированно (не ИИ)."""

    level: Literal["red", "yellow", "green"] | None
    red: int
    yellow: int
    green: int
    scored: int
    total: int
    index: float  # средняя доля p·i от максимума (0–1)


class RiskOut(BaseModel):
    code: str
    group: str
    group_title: str
    title: str
    mandatory: bool
    status: str
    version: int
    probability: int | None = None
    impact: int | None = None
    zone: str | None = None
    rationale: str | None = None
    mitigation: list[str] = Field(default_factory=list)
    related_items: list[str] = Field(default_factory=list)
    review_status: str
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class RisksOut(BaseModel):
    run_id: uuid.UUID | None
    status: str | None  # queued | running | done | failed | None — ещё не оценивались
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    probability_scale: dict[int, str]
    impact_scale: dict[int, str]
    zone_matrix: dict[str, str] = Field(default_factory=dict)  # «p,i» → зона (по правилам шаблона)
    risks: list[RiskOut]
    integral: Integral
    conclusions: SectionPayload | None = None
    conclusions_review: str = "ai_draft"
    sources: list[SourceOut] = Field(default_factory=list)
    estimate_usd_min: Decimal = Decimal(0)  # ориентировочная стоимость этапа
    estimate_usd_max: Decimal = Decimal(0)


class ReviewIn(BaseModel):
    status: Literal["ai_draft", "in_review", "approved"]


class CoverageRow(BaseModel):
    ref: str  # id пункта / код риска / «5»
    kind: Literal["item", "risk", "conclusions"]
    title: str
    section: str = ""
    status: str  # done | failed | pending | missing
    disclosure_status: str | None = None
    sources: int = 0
    experts: list[str] = Field(default_factory=list)
    review_status: str


class Issue(BaseModel):
    level: Literal["error", "warning"]
    rule: str
    message: str
    ref: str | None = None


class CoverageOut(BaseModel):
    run_id: uuid.UUID | None
    rows: list[CoverageRow]
    issues: list[Issue]
    approved: int
    total: int
    can_release: bool
