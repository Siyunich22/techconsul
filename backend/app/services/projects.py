import uuid
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.models import (
    Expert,
    Project,
    ProjectMember,
    ProjectStatus,
    TemplateVersion,
    User,
    UserRole,
)
from app.schemas.project import MemberIn, ProjectCreate, ProjectPatch, SortField
from app.services.audit import audit
from app.services.tenancy import get_scoped, scoped
from app.template_engine.loader import assignable_sections, ensure_default_template, parse_template


def visible_projects(principal: Principal) -> Select[tuple[Project]]:
    """Проекты организации, видимые пользователю: руководителю — все, эксперту — где он в команде."""
    stmt = scoped(Project, principal.org_id)
    if principal.role == UserRole.expert:
        stmt = stmt.where(
            Project.id.in_(
                select(ProjectMember.project_id)
                .join(Expert, Expert.id == ProjectMember.expert_id)
                .where(Expert.user_id == principal.user_id)
            )
        )
    elif principal.role == UserRole.observer:
        stmt = stmt.where(False)  # доступ наблюдателей к выпущенным версиям — фаза 9
    return stmt


def get_project(db: Session, principal: Principal, project_id: uuid.UUID) -> Project:
    project = db.scalar(visible_projects(principal).where(Project.id == project_id))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project


def list_projects(
    db: Session,
    principal: Principal,
    *,
    statuses: list[ProjectStatus] | None = None,
    include_archived: bool = False,
    industry: str | None = None,
    region: str | None = None,
    owner_id: uuid.UUID | None = None,
    integral_risk: str | None = None,
    updated_from: date | None = None,
    updated_to: date | None = None,
    q: str | None = None,
    sort: SortField = "updated_at",
    desc: bool = True,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Project], int]:
    stmt = visible_projects(principal)
    if statuses:
        stmt = stmt.where(Project.status.in_(statuses))
    elif not include_archived:
        stmt = stmt.where(Project.status != ProjectStatus.archived)
    if industry:
        stmt = stmt.where(Project.industry == industry)
    if region:
        stmt = stmt.where(Project.region == region)
    if owner_id:
        stmt = stmt.where(Project.owner_id == owner_id)
    if integral_risk:
        stmt = stmt.where(Project.integral_risk == integral_risk)
    if updated_from:
        stmt = stmt.where(Project.updated_at >= datetime.combine(updated_from, time.min))
    if updated_to:
        stmt = stmt.where(Project.updated_at < datetime.combine(updated_to + timedelta(days=1), time.min))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Project.name.ilike(pattern), Project.customer_name.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    column = getattr(Project, sort)
    order = column.desc().nulls_last() if desc else column.asc().nulls_last()
    items = db.scalars(stmt.order_by(order, Project.id).offset((page - 1) * page_size).limit(page_size)).all()
    return list(items), total


def _template(db: Session, template_version_id: uuid.UUID | None) -> TemplateVersion:
    if template_version_id is None:
        return db.scalar(select(TemplateVersion).where(TemplateVersion.is_default.is_(True))) or (
            ensure_default_template(db)
        )
    tv = db.get(TemplateVersion, template_version_id)
    if tv is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Шаблон ТЗ не найден")
    return tv


def _validate_optional_items(tv: TemplateVersion, items: list[str]) -> list[str]:
    optional = {s.id for s in assignable_sections(parse_template(tv.yaml_text)) if s.optional}
    unknown = set(items) - optional
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Пункты не являются необязательными в шаблоне: {', '.join(sorted(unknown))}",
        )
    return sorted(set(items))


def _build_members(
    db: Session, principal: Principal, project: Project, members: list[MemberIn]
) -> list[ProjectMember]:
    sections = assignable_sections(parse_template(project.template_version.yaml_text))
    allowed = {s.id for s in sections if not s.optional or s.id in project.enabled_optional_items}
    seen: set[uuid.UUID] = set()
    result = []
    for m in members:
        if m.expert_id in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Эксперт указан в команде дважды")
        seen.add(m.expert_id)
        get_scoped(db, Expert, m.expert_id, principal.org_id)  # чужой эксперт → 404
        bad = set(m.assigned_items) - allowed
        if bad:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Разделы отсутствуют в шаблоне проекта или не включены: {', '.join(sorted(bad))}",
            )
        result.append(
            ProjectMember(expert_id=m.expert_id, role=m.role, assigned_items=sorted(set(m.assigned_items)))
        )
    return result


def create_project(db: Session, principal: Principal, data: ProjectCreate) -> Project:
    tv = _template(db, data.template_version_id)
    fields = data.model_dump(
        exclude={"template_version_id", "independence", "members", "enabled_optional_items"}
    )
    project = Project(
        org_id=principal.org_id,
        owner_id=principal.user_id,
        template_version_id=tv.id,
        template_version=tv,
        enabled_optional_items=_validate_optional_items(tv, data.enabled_optional_items),
        independence_json=data.independence.model_dump(exclude={"has_conflict", "is_complete"}),
        status=ProjectStatus.draft,
        **fields,
    )
    project.members = _build_members(db, principal, project, data.members)
    db.add(project)
    db.flush()
    audit(
        db,
        "project.created",
        org_id=principal.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        name=project.name,
    )
    return project


def update_project(db: Session, principal: Principal, project: Project, data: ProjectPatch) -> Project:
    changes = data.model_dump(exclude_unset=True, exclude={"independence", "enabled_optional_items"})
    for field in ("name", "customer_name", "owner_id", "currency"):
        if changes.get(field, ...) is None:
            changes.pop(field)
    if "owner_id" in changes:
        owner = get_scoped(db, User, changes["owner_id"], principal.org_id)
        if owner.role not in (UserRole.manager, UserRole.admin):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Ответственный должен быть руководителем"
            )
    for field, value in changes.items():
        setattr(project, field, "" if value is None and field != "budget_amount" else value)
    if data.independence is not None:
        project.independence_json = data.independence.model_dump(exclude={"has_conflict", "is_complete"})
    if data.enabled_optional_items is not None:
        project.enabled_optional_items = _validate_optional_items(
            project.template_version, data.enabled_optional_items
        )
    audit(
        db,
        "project.updated",
        org_id=principal.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        fields=sorted(data.model_fields_set),
    )
    return project


def set_members(db: Session, principal: Principal, project: Project, members: list[MemberIn]) -> Project:
    project.members = _build_members(db, principal, project, members)
    audit(
        db,
        "project.team_updated",
        org_id=principal.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        members=[m.model_dump(mode="json") for m in members],
    )
    return project


def archive(db: Session, principal: Principal, project: Project) -> Project:
    if project.status != ProjectStatus.archived:
        project.pre_archive_status = project.status
        project.status = ProjectStatus.archived
        audit(
            db, "project.archived", org_id=principal.org_id, user_id=principal.user_id, project_id=project.id
        )
    return project


def unarchive(db: Session, principal: Principal, project: Project) -> Project:
    if project.status == ProjectStatus.archived:
        project.status = project.pre_archive_status or ProjectStatus.draft
        project.pre_archive_status = None
        audit(
            db,
            "project.unarchived",
            org_id=principal.org_id,
            user_id=principal.user_id,
            project_id=project.id,
        )
    return project


def delete_project(db: Session, principal: Principal, project: Project) -> None:
    if project.status != ProjectStatus.draft:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Удалить можно только черновик — остальные проекты переводятся в архив"
        )
    audit(
        db,
        "project.deleted",
        org_id=principal.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        name=project.name,
    )
    db.delete(project)
