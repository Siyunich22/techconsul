"""Справочники интерфейса и шаблоны ТЗ (чтение)."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession
from app.core.reference import CURRENCIES, OKED_SECTIONS, REGIONS_KZ
from app.models import ProjectStatus, TemplateVersion
from app.schemas.project import AssignableSectionOut, TemplateBrief
from app.template_engine.loader import assignable_sections, parse_template

router = APIRouter(tags=["catalog"])


class Option(BaseModel):
    value: str
    label: str


class ReferenceOut(BaseModel):
    industries: list[Option]
    currencies: list[str]
    regions: list[str]
    statuses: list[str]


class TemplateOut(TemplateBrief):
    is_default: bool


@router.get("/reference", response_model=ReferenceOut)
def reference(_: CurrentPrincipal):
    return ReferenceOut(
        industries=[Option(value=k, label=f"{k} — {v}") for k, v in OKED_SECTIONS.items()],
        currencies=CURRENCIES,
        regions=REGIONS_KZ,
        statuses=[s.value for s in ProjectStatus],
    )


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(_: CurrentPrincipal, db: DbSession):
    stmt = select(TemplateVersion).order_by(TemplateVersion.is_default.desc(), TemplateVersion.created_at)
    return db.scalars(stmt).all()


@router.get("/templates/{template_id}/sections", response_model=list[AssignableSectionOut])
def template_sections(template_id: uuid.UUID, _: CurrentPrincipal, db: DbSession):
    tv = db.get(TemplateVersion, template_id)
    if tv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return [AssignableSectionOut(**s.__dict__) for s in assignable_sections(parse_template(tv.yaml_text))]
