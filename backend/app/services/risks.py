"""Риски и выводы (TZ §5.4–5.5), ревью экспертов и матрица покрытия с валидатором (TZ §4.5 Е, §5.6)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.assessment.risks import integral, latest_items, latest_risks, risk_spec
from app.assessment.runner import plan_items
from app.models import (
    AssessmentRun,
    Document,
    Expert,
    ItemStatus,
    Project,
    ProjectMember,
    ReviewStatus,
    RiskResult,
    Source,
)
from app.schemas.assessment import SOURCE_REF, SectionPayload, SourceOut
from app.schemas.risk import CoverageOut, CoverageRow, Issue, RiskOut, RiskPayload, RisksOut
from app.services.assessment import _expert_assigned, _is_active, latest_run
from app.services.audit import audit
from app.template_engine.expr import zone_for
from app.template_engine.loader import parse_template

# 15 обязательных рисков + дополнительные (Sonnet) и выводы (Opus), USD — по первым прогонам уточнить
RISKS_COST_MIN, RISKS_COST_MAX = Decimal("0.6"), Decimal("2.5")
RISKS_STALE = timedelta(minutes=60)


class RisksEnqueuer(Protocol):
    def run(self, run_id: uuid.UUID) -> None: ...


class CeleryRisksEnqueuer:
    def run(self, run_id: uuid.UUID) -> None:
        from workers.tasks import enqueue_risks

        enqueue_risks(run_id)


@lru_cache
def get_risks_enqueuer() -> RisksEnqueuer:
    return CeleryRisksEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


def _run(db: Session, project: Project) -> AssessmentRun:
    run = latest_run(db, project)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка ещё не запускалась")
    return run


def _risks_busy(run: AssessmentRun) -> bool:
    started = run.risks_started_at or run.updated_at
    return run.risks_status in ("queued", "running") and _now() - started < RISKS_STALE


# --- запуск этапа ---


def start(db: Session, enqueuer: RisksEnqueuer, principal: Principal, project: Project) -> AssessmentRun:
    if not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Запускать оценку рисков может руководитель проекта")
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
    run = _run(db, project)
    if _is_active(db, run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания оценки пунктов")
    if _risks_busy(run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Риски уже оцениваются")
    if risk_spec(project) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "В шаблоне ТЗ нет раздела рисков")
    if not any(r.status == ItemStatus.done for r in latest_items(db, run.id).values()):
        raise HTTPException(status.HTTP_409_CONFLICT, "Нет выполненных пунктов — риски не из чего оценивать")
    run.risks_status, run.risks_error, run.risks_started_at = "queued", None, _now()
    audit(
        db,
        "risks.started",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
    )
    db.commit()
    enqueuer.run(run.id)
    db.refresh(run)
    return run


# --- чтение ---


def _sources_out(db: Session, run: AssessmentRun, sids: set[str]) -> list[SourceOut]:
    if not sids:
        return []
    rows = db.scalars(
        select(Source).where(Source.run_id == run.id, Source.org_id == run.org_id, Source.sid.in_(sids))
    ).all()
    names = {
        d.id: d.filename
        for d in db.scalars(
            select(Document).where(
                Document.org_id == run.org_id, Document.id.in_([s.document_id for s in rows if s.document_id])
            )
        )
    }
    return [
        SourceOut(
            sid=s.sid,
            type=s.type,  # type: ignore[arg-type]
            document_id=s.document_id,
            filename=names.get(s.document_id) if s.document_id else None,
            page=s.page,
            sheet=s.sheet,
            cell_range=s.cell_range,
            reference_doc_id=s.reference_doc_id,
            url=s.url,
            title=s.title,
            accessed_at=s.accessed_at,
        )
        for s in sorted(rows, key=lambda x: int(x.sid[1:]))
    ]


def risks(db: Session, project: Project) -> RisksOut:
    spec = risk_spec(project)
    run = latest_run(db, project)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "В шаблоне ТЗ нет раздела рисков")
    rows = latest_risks(db, run.id) if run else {}
    order = {r.code: n for n, r in enumerate(spec.risks)}
    out: list[RiskOut] = []
    sids: set[str] = set()
    scored: list[tuple[int, int]] = []
    defs = {r.code: r for r in spec.risks}
    for code in sorted(set(rows) | set(defs), key=lambda c: (order.get(c, 100), c)):
        row = rows.get(code)
        d = defs.get(code)
        p = RiskPayload.model_validate(row.payload_json) if row and row.payload_json else None
        if p is not None:
            sids |= set(SOURCE_REF.findall(p.rationale))
            if row.mandatory:
                scored.append((p.probability, p.impact))
        group = row.group if row else d.group  # type: ignore[union-attr]
        out.append(
            RiskOut(
                code=code,
                group=group,
                group_title=spec.groups.get(group, group),
                title=row.title if row else d.title,  # type: ignore[union-attr]
                mandatory=row.mandatory if row else True,
                status=row.status.value if row else "pending",
                version=row.version if row else 0,
                probability=p.probability if p else None,
                impact=p.impact if p else None,
                zone=zone_for(spec.zones, p.probability, p.impact) if p else None,
                rationale=p.rationale if p else None,
                mitigation=p.mitigation if p else [],
                related_items=p.related_items if p else list(d.related if d else []),
                review_status=row.review_status.value if row else "ai_draft",
                error=row.error if row else None,
                warnings=list(row.validator_warnings_json or []) if row else [],
            )
        )
    conclusions = (
        SectionPayload.model_validate(run.conclusions_json) if run and run.conclusions_json else None
    )
    if conclusions:
        sids |= {s.id for s in conclusions.sources}
    return RisksOut(
        run_id=run.id if run else None,
        status=run.risks_status if run else None,
        error=run.risks_error if run else None,
        started_at=run.risks_started_at if run else None,
        finished_at=run.risks_finished_at if run else None,
        probability_scale=spec.probability_scale,
        impact_scale=spec.impact_scale,
        zone_matrix={
            f"{p},{i}": zone_for(spec.zones, p, i) for p in spec.probability_scale for i in spec.impact_scale
        },
        risks=out,
        integral=integral(spec, scored, len(spec.risks)),
        conclusions=conclusions,
        conclusions_review=run.conclusions_review.value if run else "ai_draft",
        sources=_sources_out(db, run, sids) if run else [],
        estimate_usd_min=RISKS_COST_MIN,
        estimate_usd_max=RISKS_COST_MAX,
    )


# --- ревью экспертов ---


def _can_review(db: Session, principal: Principal, project: Project, ref: str) -> None:
    if principal.can_manage:
        return
    if principal.role.value != "expert" or not _expert_assigned(db, principal, project, ref):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Утверждать может эксперт, назначенный на раздел")


def review_item(db: Session, principal: Principal, project: Project, item_id: str, value: str):
    from app.services.assessment import _latest_rows

    _can_review(db, principal, project, item_id)
    run = _run(db, project)
    row = _latest_rows(db, run).get(item_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пункт не найден в прогоне")
    if value == "approved" and row.status != ItemStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "Утвердить можно только сформированный пункт")
    row.review_status = ReviewStatus(value)
    audit(
        db,
        "review.item",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
        item_id=item_id,
        status=value,
        version=row.version,
    )
    db.commit()
    return row


def review_risk(db: Session, principal: Principal, project: Project, code: str, value: str) -> RiskResult:
    spec = risk_spec(project)
    _can_review(db, principal, project, spec.section_id if spec else "4")
    run = _run(db, project)
    row = latest_risks(db, run.id).get(code)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Риск не найден")
    if value == "approved" and row.status != ItemStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "Утвердить можно только оценённый риск")
    row.review_status = ReviewStatus(value)
    audit(
        db,
        "review.risk",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        code=code,
        status=value,
    )
    db.commit()
    return row


def review_conclusions(db: Session, principal: Principal, project: Project, value: str) -> AssessmentRun:
    template = parse_template(project.template_version.yaml_text)
    section = next((s for s in template["sections"] if s.get("kind") == "conclusion"), {})
    _can_review(db, principal, project, str(section.get("id", "5")))
    run = _run(db, project)
    if value == "approved" and not run.conclusions_json:
        raise HTTPException(status.HTTP_409_CONFLICT, "Выводы ещё не сформированы")
    run.conclusions_review = ReviewStatus(value)
    audit(
        db,
        "review.conclusions",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        status=value,
    )
    db.commit()
    return run


# --- матрица покрытия и валидатор ---


def _experts_by_section(db: Session, project: Project) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = db.execute(
        select(ProjectMember.assigned_items, ProjectMember.role, Expert.full_name)
        .join(Expert, Expert.id == ProjectMember.expert_id)
        .where(ProjectMember.project_id == project.id, Expert.org_id == project.org_id)
    ).all()
    for sections, _role, name in rows:
        for sec in sections:
            out.setdefault(sec, []).append(name)
    return out


def _experts_for(ref: str, by_section: dict[str, list[str]]) -> list[str]:
    parts = ref.split(".")
    for n in range(len(parts), 0, -1):
        if (key := ".".join(parts[:n])) in by_section:
            return by_section[key]
    return []


def coverage(db: Session, project: Project) -> CoverageOut:
    template = parse_template(project.template_version.yaml_text)
    plan = plan_items(template, list(project.enabled_optional_items))
    rules = set(template.get("validation_rules") or [])
    run = latest_run(db, project)
    items = latest_items(db, run.id) if run else {}
    risk_rows = latest_risks(db, run.id) if run else {}
    spec = risk_spec(project)
    by_section = _experts_by_section(db, project)
    rows: list[CoverageRow] = []
    issues: list[Issue] = []

    def issue(rule: str, message: str, ref: str | None = None, level: str = "error") -> None:
        if rule in rules or rule.startswith("_"):
            issues.append(Issue(level=level, rule=rule.lstrip("_"), message=message, ref=ref))  # type: ignore[arg-type]

    for item in plan:
        row = items.get(item.id)
        p = (
            SectionPayload.model_validate(row.payload_json)
            if row and row.status == ItemStatus.done and row.payload_json
            else None
        )
        rows.append(
            CoverageRow(
                ref=item.id,
                kind="item",
                title=item.title,
                section=item.section,
                status=row.status.value if row else "missing",
                disclosure_status=p.disclosure_status if p else None,
                sources=len({s.id for s in p.sources}) if p else 0,
                experts=_experts_for(item.id, by_section),
                review_status=row.review_status.value if row else "ai_draft",
            )
        )
        if p is None:
            issue("every_item_present", f"Пункт {item.id} не сформирован", item.id)
            continue
        if p.disclosure_status in ("full", "partial") and not p.sources:
            issue("sourced_unless_not_disclosed", f"Пункт {item.id}: вывод без источника", item.id)
        if p.disclosure_status == "not_disclosed" and (
            p.gap is None
            or not all([p.gap.reason, p.gap.impact_on_conclusions, p.gap.assumptions, p.gap.recommendation])
        ):
            issue(
                "gap_fields_required_when_not_disclosed",
                f"Пункт {item.id}: не заполнены поля нераскрытия",
                item.id,
            )
        need = (item.node.output_hints or {}).get("min_alternatives")
        if need and p.disclosure_status in ("full", "partial"):
            best = max((len(t.rows) for t in p.tables), default=0)
            if best < int(need):
                issue(
                    "min_alternatives_3_4_1",
                    f"Пункт {item.id}: в таблице сравнения меньше {need} альтернатив",
                    item.id,
                )
        if row.validator_warnings_json:
            issue(
                "_warnings",
                f"Пункт {item.id}: {'; '.join(row.validator_warnings_json)[:300]}",
                item.id,
                "warning",
            )
        if row.review_status != ReviewStatus.approved:
            issue("all_items_expert_approved", f"Пункт {item.id} не утверждён экспертом", item.id)

    if spec is not None:
        for d in spec.risks:
            r = risk_rows.get(d.code)
            rows.append(
                CoverageRow(
                    ref=d.code,
                    kind="risk",
                    title=d.title,
                    section=f"{d.group} {spec.groups.get(d.group, '')}".strip(),
                    status=r.status.value if r else "missing",
                    experts=_experts_for(spec.section_id, by_section),
                    review_status=r.review_status.value if r else "ai_draft",
                )
            )
            if r is None or r.status != ItemStatus.done:
                issue(
                    "mandatory_risks_scored_with_rationale_and_mitigation",
                    f"Риск {d.code} не оценён (вероятность, влияние, обоснование, мероприятия)",
                    d.code,
                )
            elif r.review_status != ReviewStatus.approved:
                issue("all_items_expert_approved", f"Риск {d.code} не утверждён экспертом", d.code)
        for code, r in sorted(risk_rows.items()):
            if r.mandatory:
                continue
            rows.append(
                CoverageRow(
                    ref=code,
                    kind="risk",
                    title=f"{r.title} (дополнительный)",
                    section=f"{r.group} {spec.groups.get(r.group, '')}".strip(),
                    status=r.status.value,
                    experts=_experts_for(spec.section_id, by_section),
                    review_status=r.review_status.value,
                )
            )
            if r.status == ItemStatus.done and r.review_status != ReviewStatus.approved:
                issue("all_items_expert_approved", f"Дополнительный риск {code} не утверждён экспертом", code)

    section5 = next((s for s in template["sections"] if s.get("kind") == "conclusion"), None)
    if section5 is not None:
        ref = str(section5["id"])
        done = bool(run and run.conclusions_json)
        rows.append(
            CoverageRow(
                ref=ref,
                kind="conclusions",
                title=section5["title"],
                section=f"{ref} {section5['title']}",
                status="done" if done else "missing",
                experts=_experts_for(ref, by_section),
                review_status=run.conclusions_review.value if run else "ai_draft",
            )
        )
        if not done:
            issue("every_item_present", "Раздел выводов не сформирован", ref)
        elif run.conclusions_review != ReviewStatus.approved:
            issue("all_items_expert_approved", "Выводы не утверждены экспертом", ref)

    ind = project.independence_json or {}
    if any(ind.get(k) is None for k in ("affiliated", "participated_in_docs", "is_supplier")):
        issue("independence_block_completed", "Не заполнен блок независимости (карточка проекта)")
    if run is not None:
        bad = db.scalars(
            select(Source.sid).where(
                Source.run_id == run.id,
                Source.type == "web",
                (Source.url.is_(None)) | (Source.accessed_at.is_(None)),
            )
        ).all()
        for sid in bad:
            issue(
                "web_sources_have_url_and_access_date",
                f"Веб-источник {sid} без адреса или даты обращения",
                sid,
            )

    approved = sum(r.review_status == "approved" for r in rows)
    return CoverageOut(
        run_id=run.id if run else None,
        rows=rows,
        issues=issues,
        approved=approved,
        total=len(rows),
        can_release=run is not None and not any(i.level == "error" for i in issues),
    )
