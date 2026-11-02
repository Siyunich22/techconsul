"""Результат анализа пункта ТЗ (TZ §5.3) и API оценки.

Правило 2 (CLAUDE.md): `full|partial` без источников — ошибка; для `not_disclosed` обязательны все 4 поля
`gap`. Ссылки `[S12]` в тексте должны указывать на источники из `sources`.
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DisclosureStatus = Literal["full", "partial", "not_disclosed", "not_applicable"]
SOURCE_REF = re.compile(r"\[(S\d+)\]")


class ResultTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str]]


class Finding(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)


class Gap(BaseModel):
    reason: str = Field(description="Суть недостатка: каких данных нет")
    impact_on_conclusions: str = Field(description="Как недостаток влияет на выводы")
    assumptions: str = Field(description="Принятые допущения")
    recommendation: str = Field(description="Оптимальный вариант для проекта: что сделать Заказчику")


SourceType = Literal["doc", "reference", "web", "project"]


class SourceRef(BaseModel):
    id: str = Field(description="S12 — из результатов инструментов; для веб-источников — W1, W2…")
    type: SourceType = "doc"
    url: str | None = None
    title: str = ""
    accessed_at: str | None = None


class SectionPayload(BaseModel):
    item_id: str
    disclosure_status: DisclosureStatus
    summary: str = Field(description="Краткий вывод, 1–3 предложения")
    analysis_md: str = Field(description="Аргументация с цифрами и ссылками [S12]")
    tables: list[ResultTable] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    remarks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, description="Допущения, принятые в этом пункте")
    gap: Gap | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _rules(self) -> "SectionPayload":
        """Правило 2. Ссылки в тексте без записи в sources допустимы на входе от модели — код дополняет
        sources из реестра (finalize) и затем вызывает check_citations()."""
        if self.disclosure_status in ("full", "partial") and not (self.sources or self.cited()):
            raise ValueError("Статус full/partial требует хотя бы одного источника")
        if self.disclosure_status == "not_disclosed":
            gap = self.gap
            if gap is None or not all(
                v.strip()
                for v in (gap.reason, gap.impact_on_conclusions, gap.assumptions, gap.recommendation)
            ):
                raise ValueError(
                    "Статус not_disclosed требует заполненных gap: "
                    "reason, impact_on_conclusions, assumptions, recommendation"
                )
        return self

    def cited(self) -> set[str]:
        return set(SOURCE_REF.findall(self.analysis_md)) | {
            sid for f in self.findings for sid in f.source_ids
        }

    def check_citations(self) -> None:
        if self.disclosure_status in ("full", "partial") and not self.sources:
            raise ValueError("Статус full/partial требует хотя бы одного источника в sources")
        unknown = sorted(self.cited() - {s.id for s in self.sources})
        if unknown:
            raise ValueError(f"Ссылки на источники, которых нет в sources: {', '.join(unknown)}")


# --- API ---

RunStatus = Literal["queued", "running", "done", "failed", "cancelled"]
ItemStatus = Literal["pending", "running", "done", "failed"]


class SourceOut(BaseModel):
    sid: str
    type: SourceType
    document_id: uuid.UUID | None = None
    filename: str | None = None
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    reference_doc_id: uuid.UUID | None = None
    url: str | None = None
    title: str = ""
    accessed_at: datetime | None = None


class ItemOut(BaseModel):
    item_id: str
    title: str
    section_id: str
    section_title: str = ""
    kind: str | None
    status: ItemStatus
    version: int
    disclosure_status: DisclosureStatus | None = None
    summary: str | None = None
    error: str | None = None
    review_status: str
    instruction: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reused: bool = False  # взят из прошлого прогона (входные данные не изменились)


class ItemDetailOut(ItemOut):
    payload: SectionPayload | None = None
    sources: list[SourceOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RunOut(BaseModel):
    id: uuid.UUID
    status: RunStatus
    items_total: int
    items_done: int
    items_failed: int
    cost_usd: Decimal
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    web_search: bool


class AssessmentOut(BaseModel):
    run: RunOut | None
    items: list[ItemOut]


class EstimateOut(BaseModel):
    items_ai: int
    items_info: int
    items_reused: int = 0  # входные данные не изменились — возьмутся из прошлого прогона без ИИ
    items_strong: int = 0  # пункты на сильной модели (выводы)
    budget_usd: Decimal | None = None  # остановка прогона при достижении расходов
    # довыполнение: пункты последнего прогона с ошибкой (готовые сохраняются)
    resume_items: int = 0
    resume_cost_usd_min: Decimal = Decimal(0)
    resume_cost_usd_max: Decimal = Decimal(0)
    cost_usd_min: Decimal
    cost_usd_max: Decimal
    minutes: int
    passport_confirmed: bool
    web_search: bool
    warnings: list[str]


class RerunIn(BaseModel):
    instruction: str = Field(default="", max_length=4000)
