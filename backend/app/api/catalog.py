"""Справочники интерфейса и шаблоны ТЗ (чтение)."""

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession
from app.core.reference import CURRENCIES, OKED_SECTIONS, REGIONS_KZ
from app.models import ProjectStatus, TemplateVersion
from app.schemas.project import AssignableSectionOut, TemplateBrief
from app.services import templates as templates_svc
from app.services.projects import get_project
from app.template_engine.loader import assignable_sections, parse_template
from app.template_engine.tree import build_tree

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


class TemplateDetailOut(TemplateOut):
    summary: dict
    warnings: list[dict]
    document_categories: dict[str, str]
    tree: list[dict]


@router.get("/templates/{template_id}", response_model=TemplateDetailOut)
def template_detail(template_id: uuid.UUID, _: CurrentPrincipal, db: DbSession):
    tv = templates_svc.get(db, template_id)
    data = parse_template(tv.yaml_text)
    return TemplateDetailOut(
        id=tv.id,
        code=tv.code,
        title=tv.title,
        is_default=tv.is_default,
        summary=templates_svc.summary(tv),
        warnings=tv.warnings_json or [],
        document_categories=data.get("document_categories", {}),
        tree=[n.as_dict() for n in build_tree(data)],
    )


@router.get("/templates/{template_id}/yaml")
def template_yaml(template_id: uuid.UUID, _: CurrentPrincipal, db: DbSession):
    tv = templates_svc.get(db, template_id)
    return Response(
        tv.yaml_text,
        media_type="application/x-yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{tv.code}.yaml"'},
    )


@router.get("/projects/{project_id}/items")
def project_items(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    """Дерево пунктов ТЗ проекта: зафиксированная версия шаблона + включённые необязательные разделы."""

    project = get_project(db, principal, project_id)
    data = parse_template(project.template_version.yaml_text)
    return [n.as_dict() for n in build_tree(data, project.enabled_optional_items)]


@router.get("/templates/{template_id}/sections", response_model=list[AssignableSectionOut])
def template_sections(template_id: uuid.UUID, _: CurrentPrincipal, db: DbSession):
    tv = db.get(TemplateVersion, template_id)
    if tv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return [AssignableSectionOut(**s.__dict__) for s in assignable_sections(parse_template(tv.yaml_text))]
