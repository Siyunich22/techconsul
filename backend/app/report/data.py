"""Данные отчёта: структура — из YAML шаблона (правило 1), содержание пунктов — последние версии прогона."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import Storage
from app.models import (
    AssessmentRun,
    Document,
    ItemStatus,
    Organization,
    Project,
    ProjectMember,
    ReviewStatus,
    SectionResult,
    Source,
)
from app.schemas.assessment import SectionPayload
from app.schemas.org import OrgSettings
from app.template_engine.loader import parse_template
from app.template_engine.tree import TreeNode, build_tree


@dataclass
class ItemResult:
    payload: SectionPayload | None
    status: ItemStatus | None
    error: str | None
    approved: bool


@dataclass
class SourceInfo:
    sid: str
    type: str
    title: str
    filename: str | None = None
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    url: str | None = None
    accessed: date | None = None


@dataclass
class RiskLine:
    code: str
    group: str
    title: str
    mandatory: bool
    probability: int | None
    impact: int | None
    zone: str | None
    rationale: str
    mitigation: list[str]
    approved: bool


@dataclass
class Member:
    full_name: str
    role: str  # lead | expert
    specialization: str
    education: str
    research: str
    years: int | None
    sections: list[str]


@dataclass
class ReportData:
    project_name: str
    customer_name: str
    customer_bin: str | None
    executor_name: str
    executor_bin: str
    executor_address: str
    independence: dict
    settings: OrgSettings
    tree: list[TreeNode]
    raw_sections: dict[str, dict]  # id раздела → узел YAML (render, intro, requirement)
    appendices: list[dict]
    items: dict[str, ItemResult]
    sources: dict[str, SourceInfo]
    members: list[Member]
    version: str
    released: bool
    created: date
    logo: bytes | None = None
    logo_type: str | None = None
    reference_docx: bytes | None = None
    sha256: str | None = None  # контрольная сумма содержания (выпущенная версия — в колонтитуле)
    risks: list[RiskLine] = field(default_factory=list)
    risk_groups: dict[str, str] = field(default_factory=dict)
    probability_scale: dict[int, str] = field(default_factory=dict)
    impact_scale: dict[int, str] = field(default_factory=dict)
    zones: dict[str, str] = field(default_factory=dict)
    integral: object | None = None  # app.schemas.risk.Integral
    conclusions: SectionPayload | None = None
    conclusions_approved: bool = False


def _read(storage: Storage, key: str | None) -> bytes | None:
    if not key:
        return None
    obj = storage.get(key)
    return b"".join(obj.body)


def collect(
    db: Session,
    storage: Storage,
    project: Project,
    run: AssessmentRun | None,
    *,
    version: str,
    released: bool,
    created: date,
) -> ReportData:
    org = db.scalar(select(Organization).where(Organization.id == project.org_id))
    template = parse_template(project.template_version.yaml_text)
    tree = build_tree(template, list(project.enabled_optional_items))

    items: dict[str, ItemResult] = {}
    sources: dict[str, SourceInfo] = {}
    if run is not None:
        rows = db.scalars(
            select(SectionResult)
            .where(SectionResult.run_id == run.id, SectionResult.org_id == project.org_id)
            .order_by(SectionResult.version)
        ).all()
        for r in {r.item_id: r for r in rows}.values():  # последняя версия пункта
            payload = (
                SectionPayload.model_validate(r.payload_json)
                if r.status == ItemStatus.done and r.payload_json
                else None
            )
            items[r.item_id] = ItemResult(
                payload, r.status, r.error, r.review_status == ReviewStatus.approved
            )
        src_rows = db.scalars(
            select(Source).where(Source.run_id == run.id, Source.org_id == project.org_id)
        ).all()
        names = {
            d.id: d.filename
            for d in db.scalars(
                select(Document).where(
                    Document.org_id == project.org_id,
                    Document.id.in_([s.document_id for s in src_rows if s.document_id]),
                )
            )
        }
        for s in src_rows:
            sources[s.sid] = SourceInfo(
                sid=s.sid,
                type=s.type,
                title=s.title,
                filename=names.get(s.document_id) if s.document_id else None,
                page=s.page,
                sheet=s.sheet,
                cell_range=s.cell_range,
                url=s.url,
                accessed=s.accessed_at.date() if s.accessed_at else None,
            )

    members = []
    for m in db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id)):
        e = m.expert
        members.append(
            Member(
                full_name=e.full_name,
                role=m.role.value,
                specialization=", ".join(e.specialization),
                education=e.education,
                research=e.research_experience,
                years=e.years,
                sections=list(m.assigned_items),
            )
        )
    members.sort(key=lambda m: (m.role != "lead", m.full_name))

    logo = _read(storage, org.logo_key) if org and org.logo_key else None
    logo_type = org.logo_key.rsplit(".", 1)[-1].lower() if logo else None
    if logo_type not in ("png", "jpg", "jpeg"):  # SVG в DOCX без растровой копии не вставляем
        logo, logo_type = None, None

    risks, spec_data, conclusions = _risks(db, project, run)
    content = json.dumps(
        {
            "items": {
                k: v.payload.model_dump(mode="json") if v.payload else None for k, v in sorted(items.items())
            },
            "risks": [r.__dict__ for r in risks],
            "conclusions": conclusions.model_dump(mode="json") if conclusions else None,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return ReportData(
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        risks=risks,
        conclusions=conclusions,
        conclusions_approved=bool(run and run.conclusions_review == ReviewStatus.approved),
        **spec_data,
        project_name=project.name,
        customer_name=project.customer_name,
        customer_bin=project.customer_bin,
        executor_name=org.name if org else "",
        executor_bin=org.bin if org else "",
        executor_address=org.address if org else "",
        independence=project.independence_json or {},
        settings=OrgSettings.model_validate((org.settings_json if org else None) or {}),
        tree=tree,
        raw_sections={str(s.get("id")): s for s in template["sections"]},
        appendices=list(template.get("appendices") or []),
        items=items,
        sources=sources,
        members=members,
        version=version,
        released=released,
        created=created,
        logo=logo,
        logo_type=logo_type,
        reference_docx=_read(storage, org.reference_docx_key) if org and org.reference_docx_key else None,
    )


def _risks(db: Session, project: Project, run: AssessmentRun | None):
    from app.assessment.risks import integral, latest_risks, risk_spec
    from app.schemas.risk import RiskPayload
    from app.template_engine.expr import zone_for

    spec = risk_spec(project)
    if spec is None:
        return [], {}, None
    rows = latest_risks(db, run.id) if run else {}
    order = {r.code: n for n, r in enumerate(spec.risks)}
    lines: list[RiskLine] = []
    visible = [c for c, r in rows.items() if r.mandatory or r.status != ItemStatus.failed]  # без снятых доп.
    for code in sorted(visible, key=lambda c: (order.get(c, 100), c)):
        row = rows[code]
        p = RiskPayload.model_validate(row.payload_json) if row.payload_json else None
        lines.append(
            RiskLine(
                code=code,
                group=row.group,
                title=row.title,
                mandatory=row.mandatory,
                probability=p.probability if p else None,
                impact=p.impact if p else None,
                zone=zone_for(spec.zones, p.probability, p.impact) if p else None,
                rationale=p.rationale if p else "",
                mitigation=p.mitigation if p else [],
                approved=row.review_status == ReviewStatus.approved,
            )
        )
    scored = [(r.probability, r.impact) for r in lines if r.mandatory and r.probability]
    conclusions = (
        SectionPayload.model_validate(run.conclusions_json)
        if run is not None and run.conclusions_json
        else None
    )
    return (
        lines,
        {
            "risk_groups": spec.groups,
            "probability_scale": spec.probability_scale,
            "impact_scale": spec.impact_scale,
            "zones": spec.zones,
            "integral": integral(spec, scored, len(spec.risks)),  # type: ignore[arg-type]
        },
        conclusions,
    )


def walk_items(nodes: list[TreeNode]):
    for n in nodes:
        if not n.enabled:
            continue
        if n.type == "item":
            yield n
        yield from walk_items(n.children)


def run_id_of(run: AssessmentRun | None) -> uuid.UUID | None:
    return run.id if run else None
