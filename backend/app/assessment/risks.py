"""Этап рисков и выводов (TZ §5.4–5.5): после пунктов раздела 3.

- Обязательные риски, шкалы, зоны и группы — из раздела шаблона с `kind: risks` (правило 1).
- Каждый риск — отдельный вызов модели; результаты всех пунктов — в общем системном промпте
  (кэшируется на все вызовы этапа). Ссылаться можно только на метки из результатов пунктов.
- Дополнительные риски (X1…) — один вызов; выводы раздела 5 — сильная модель по пунктам и рискам.
- Интегральный уровень — формула в коде: худшая зона среди оценённых рисков, доля p·i (не ИИ).
"""

import logging
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assessment.runner import plan_items
from app.core.config import get_settings
from app.llm.client import BILLING_MESSAGE, LLM, CallContext, LLMBillingError, LLMError, get_llm
from app.llm.prompts import render
from app.models import (
    AssessmentRun,
    ItemStatus,
    Project,
    ReviewStatus,
    RiskResult,
    SectionResult,
    Source,
)
from app.schemas.assessment import SOURCE_REF, SectionPayload, SourceRef
from app.schemas.risk import ExtraRisks, Integral, RiskPayload
from app.template_engine.expr import zone_for
from app.template_engine.loader import parse_template

log = logging.getLogger(__name__)

DbFactory = Callable[[], Session]
DEFAULT_GROUPS = {"4.1": "Технические риски", "4.2": "Риски по закупкам"}
ITEM_DIGEST_CHARS = 1800


@dataclass(frozen=True)
class RiskDef:
    code: str
    group: str
    title: str
    related: tuple[str, ...]  # пункты плана (маски раскрыты, выключенные отброшены)
    related_raw: tuple[str, ...]  # как в шаблоне


@dataclass(frozen=True)
class RiskSpec:
    section_id: str
    section_title: str
    requirement: str
    probability_scale: dict[int, str]
    impact_scale: dict[int, str]
    zones: dict[str, str]
    groups: dict[str, str]
    risks: tuple[RiskDef, ...]


def _now() -> datetime:
    return datetime.now(UTC)


def risk_spec(project: Project) -> RiskSpec | None:
    template = parse_template(project.template_version.yaml_text)
    section = next((s for s in template["sections"] if s.get("kind") == "risks"), None)
    if section is None:
        return None
    plan_ids = [i.id for i in plan_items(template, list(project.enabled_optional_items))]

    def expand(pattern: str) -> list[str]:
        if pattern.endswith(".*"):
            prefix = pattern[:-1]
            return [i for i in plan_ids if i.startswith(prefix)]
        return [pattern] if pattern in plan_ids else []

    risks = tuple(
        RiskDef(
            code=r["code"],
            group=str(r["group"]),
            title=r["title"],
            related=tuple(dict.fromkeys(i for p in r.get("related", []) for i in expand(p))),
            related_raw=tuple(r.get("related", [])),
        )
        for r in section["mandatory_risks"]
    )
    return RiskSpec(
        section_id=str(section["id"]),
        section_title=section["title"],
        requirement=" ".join(str(section.get("requirement", "")).split()),
        probability_scale={int(k): v for k, v in section["probability_scale"].items()},
        impact_scale={int(k): v for k, v in section["impact_scale"].items()},
        zones=dict(section["zones"]),
        groups={**DEFAULT_GROUPS, **{str(k): v for k, v in (section.get("risk_groups") or {}).items()}},
        risks=risks,
    )


def integral(spec: RiskSpec, scored: list[tuple[int, int]], total: int) -> Integral:
    """Интегральный уровень: худшая зона среди оценённых рисков; индекс — средняя доля p·i от максимума."""
    zones = [zone_for(spec.zones, p, i) for p, i in scored]
    level = next((z for z in ("red", "yellow", "green") if z in zones), None)
    pmax, imax = max(spec.probability_scale), max(spec.impact_scale)
    index = sum(p * i for p, i in scored) / (len(scored) * pmax * imax) if scored else 0.0
    return Integral(
        level=level,
        red=zones.count("red"),
        yellow=zones.count("yellow"),
        green=zones.count("green"),
        scored=len(scored),
        total=total,
        index=round(index, 3),
    )


def latest_items(db: Session, run_id: uuid.UUID) -> dict[str, SectionResult]:
    rows = db.scalars(
        select(SectionResult).where(SectionResult.run_id == run_id).order_by(SectionResult.version)
    ).all()
    return {r.item_id: r for r in rows}


def latest_risks(db: Session, run_id: uuid.UUID) -> dict[str, RiskResult]:
    rows = db.scalars(
        select(RiskResult).where(RiskResult.run_id == run_id).order_by(RiskResult.version)
    ).all()
    return {r.code: r for r in rows}


_STATUS = {
    "full": "раскрыт",
    "partial": "раскрыт частично",
    "not_disclosed": "НЕ РАСКРЫТ",
    "not_applicable": "не применим",
}


def results_digest(plan, rows: dict[str, SectionResult]) -> str:
    """Результаты пунктов для рисков и выводов: вывод, факты со ссылками, замечания, недостаток данных."""
    blocks = []
    for item in plan:
        row = rows.get(item.id)
        head = f"### {item.id} {item.title}"
        if row is None or row.status != ItemStatus.done or not row.payload_json:
            blocks.append(f"{head} — не сформирован (нет результата)")
            continue
        p = SectionPayload.model_validate(row.payload_json)
        parts = [f"{head} — {_STATUS.get(p.disclosure_status, p.disclosure_status)}", f"Вывод: {p.summary}"]
        if p.findings:
            parts.append(
                "Факты: "
                + "; ".join(f"{f.text} {' '.join(f'[{s}]' for s in f.source_ids)}" for f in p.findings[:10])
            )
        if p.remarks:
            parts.append("Замечания: " + "; ".join(p.remarks[:6]))
        if p.gap:
            parts.append(f"Недостаток данных: {p.gap.reason}; влияние: {p.gap.impact_on_conclusions}")
        blocks.append("\n".join(parts)[:ITEM_DIGEST_CHARS])
    return "\n\n".join(blocks)


def _scale_text(scale: dict[int, str]) -> str:
    return "; ".join(f"{k} — {v}" for k, v in sorted(scale.items()))


def _clean_refs(text: str, allowed: set[str], warnings: list[str]) -> str:
    unknown = sorted(set(SOURCE_REF.findall(text)) - allowed)
    if unknown:
        warnings.append(
            f"Ссылки на источники, которых нет в результатах пунктов, удалены: {', '.join(unknown)}"
        )
        text = re.sub(r"\s?\[(S\d+)\]", lambda m: "" if m.group(1) in unknown else m.group(0), text)
    return text


def _risk_payload(payload: RiskPayload, allowed: set[str], plan_ids: set[str]) -> tuple[dict, list[str]]:
    """Только метки источников из результатов пунктов и пункты плана; поля риска (без title/group)."""
    warnings: list[str] = []
    data = {k: getattr(payload, k) for k in RiskPayload.model_fields}
    data["rationale"] = _clean_refs(payload.rationale, allowed, warnings)
    data["related_items"] = [i for i in payload.related_items if i in plan_ids]
    return RiskPayload.model_validate(data).model_dump(), warnings


def _conclusions(payload: SectionPayload, allowed: dict[str, str]) -> tuple[SectionPayload, list[str]]:
    """Выводы: только метки из результатов пунктов; источники — по реестру прогона; правило 2."""
    warnings: list[str] = []
    data = payload.model_dump()
    keep = set(allowed)
    data["analysis_md"] = _clean_refs(payload.analysis_md, keep, warnings)
    data["summary"] = _clean_refs(payload.summary, keep, warnings)
    cited = set(SOURCE_REF.findall(data["analysis_md"] + data["summary"]))
    cited |= {s.id for s in payload.sources if s.id in keep}
    for f in data["findings"]:
        f["source_ids"] = [x for x in f["source_ids"] if x in keep]
        cited |= set(f["source_ids"])
    data["sources"] = [
        SourceRef(id=sid, type=allowed[sid]).model_dump()  # type: ignore[arg-type]
        for sid in sorted(cited, key=lambda x: int(x[1:]))
    ]
    data["item_id"] = "5"
    result = SectionPayload.model_validate(data)
    result.check_citations()
    return result, warnings


def run_stage(
    db_factory: DbFactory, run_id: uuid.UUID, llm: LLM | None = None, concurrency: int | None = None
) -> None:
    llm = llm or get_llm()
    s = get_settings()
    concurrency = concurrency or s.analysis_concurrency
    with db_factory() as db:
        run = db.get(AssessmentRun, run_id)
        if run is None or run.risks_status not in ("queued", "running"):
            return
        project = db.scalar(select(Project).where(Project.id == run.project_id, Project.org_id == run.org_id))
        spec = risk_spec(project)
        template = parse_template(project.template_version.yaml_text)
        plan = plan_items(template, list(project.enabled_optional_items))
        rows = latest_items(db, run.id)
        digest = results_digest(plan, rows)
        source_types = {
            sid: t
            for sid, t in db.execute(select(Source.sid, Source.type).where(Source.run_id == run.id)).all()
        }
        allowed = set(SOURCE_REF.findall(digest)) & set(source_types)
        run.risks_status, run.risks_started_at, run.risks_error = "running", _now(), None
        org_id, project_id = run.org_id, run.project_id
        versions = {
            code: v
            for code, v in db.execute(
                select(RiskResult.code, func.max(RiskResult.version))
                .where(RiskResult.run_id == run.id)
                .group_by(RiskResult.code)
            ).all()
        }
        for r in spec.risks:
            db.add(
                RiskResult(
                    org_id=org_id,
                    project_id=project_id,
                    run_id=run.id,
                    code=r.code,
                    group=r.group,
                    title=r.title,
                    mandatory=True,
                    version=versions.get(r.code, 0) + 1,
                    status=ItemStatus.running,
                )
            )
        db.commit()
        section5 = next((x for x in template["sections"] if x.get("kind") == "conclusion"), {})

    plan_ids = {i.id for i in plan}
    system = render(
        "risk_system",
        probability_scale=_scale_text(spec.probability_scale),
        impact_scale=_scale_text(spec.impact_scale),
        results=digest,
    )
    ctx = lambda purpose: CallContext(purpose=purpose, org_id=org_id, project_id=project_id, run_id=run_id)  # noqa: E731
    role = "items"  # риски — модель обычных пунктов (Sonnet); выводы раздела 5 — сильная модель
    billing = {"hit": False}

    def assess(r: RiskDef) -> tuple[str, RiskPayload | None, list[str], str | None]:
        if billing["hit"]:
            return r.code, None, [], BILLING_MESSAGE
        missing = [
            p for p in r.related_raw if not any(i.startswith(p.rstrip("*")) or i == p for i in plan_ids)
        ]
        note = (
            f"Пункты {', '.join(missing)} в отчёт не входят (раздел выключен) — оцени по остальным данным "
            "и отметь недостаток данных.\n"
            if missing
            else ""
        )
        try:
            payload = llm.structured(
                ctx=ctx(f"risk:{r.code}"),
                role=role,
                system=system,
                user=render(
                    "risk_item",
                    code=r.code,
                    title=r.title,
                    group=r.group,
                    group_title=spec.groups.get(r.group, r.group),
                    related=", ".join(r.related) or "нет",
                    related_note=note,
                ),
                schema=RiskPayload,
                max_tokens=4096,
            )
            data, warnings = _risk_payload(payload, allowed, plan_ids)
            return r.code, RiskPayload.model_validate(data), warnings, None
        except LLMBillingError:
            billing["hit"] = True
            return r.code, None, [], BILLING_MESSAGE
        except (LLMError, ValidationError, ValueError) as exc:
            return r.code, None, [], str(exc)[:2000]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(assess, spec.risks))

    with db_factory() as db:
        current = latest_risks(db, run_id)
        for code, payload, warnings, error in results:
            row = current[code]
            if payload is not None:
                row.status, row.payload_json, row.error = ItemStatus.done, payload.model_dump(), None
            else:
                row.status, row.error = ItemStatus.failed, error
            row.validator_warnings_json = warnings
        db.commit()

    scored = {code: p for code, p, _, _ in results if p is not None}
    if not billing["hit"]:
        _extra(db_factory, run_id, llm, ctx, system, spec, scored, allowed, plan_ids, org_id, project_id)
    if not billing["hit"]:
        _conclusions_step(db_factory, run_id, llm, ctx, digest, spec, section5, source_types, allowed)

    with db_factory() as db:
        run = db.get(AssessmentRun, run_id)
        run.risks_finished_at = _now()
        if billing["hit"]:
            run.risks_status, run.risks_error = "failed", BILLING_MESSAGE
        else:
            failed = [r for r in latest_risks(db, run_id).values() if r.status == ItemStatus.failed]
            run.risks_status = "done"
            run.risks_error = f"Не оценены риски: {', '.join(r.code for r in failed)}" if failed else None
        db.commit()


def _extra(db_factory, run_id, llm, ctx, system, spec, scored, allowed, plan_ids, org_id, project_id) -> None:
    lines = "\n".join(
        f"- {r.code} «{r.title}»: вероятность {scored[r.code].probability}, влияние {scored[r.code].impact}"
        for r in spec.risks
        if r.code in scored
    )
    try:
        extra = llm.structured(
            ctx=ctx("risk:extra"),
            role="items",
            system=system,
            user=render("risk_extra", mandatory=lines or "нет"),
            schema=ExtraRisks,
            max_tokens=8192,
        )
    except LLMError as exc:
        log.warning("Дополнительные риски не предложены: %s", exc)
        return
    with db_factory() as db:
        for n, x in enumerate(extra.risks, start=1):
            data, warnings = _risk_payload(x, allowed, plan_ids)
            code = f"X{n}"
            version = (
                db.scalar(
                    select(func.max(RiskResult.version)).where(
                        RiskResult.run_id == run_id, RiskResult.code == code
                    )
                )
                or 0
            )
            db.add(
                RiskResult(
                    org_id=org_id,
                    project_id=project_id,
                    run_id=run_id,
                    code=code,
                    group=x.group,
                    title=x.title,
                    mandatory=False,
                    version=version + 1,
                    status=ItemStatus.done,
                    payload_json=RiskPayload.model_validate(data).model_dump(),
                    validator_warnings_json=warnings,
                )
            )
        db.commit()


def _conclusions_step(db_factory, run_id, llm, ctx, digest, spec, section5, source_types, allowed) -> None:
    with db_factory() as db:
        risks = [
            r for r in latest_risks(db, run_id).values() if r.status == ItemStatus.done and r.payload_json
        ]
        scored = [(r.payload_json["probability"], r.payload_json["impact"]) for r in risks if r.mandatory]
        integ = integral(spec, scored, len(spec.risks))
        lines = "\n".join(
            f"- {r.code} «{r.title}»: вероятность {spec.probability_scale[r.payload_json['probability']]}, "
            f"влияние {spec.impact_scale[r.payload_json['impact']]}, зона "
            f"{zone_for(spec.zones, r.payload_json['probability'], r.payload_json['impact'])}. "
            f"{r.payload_json['rationale'][:400]}"
            for r in risks
        )
    level = {"red": "высокий", "yellow": "средний", "green": "низкий", None: "не определён"}[integ.level]
    try:
        payload = llm.structured(
            ctx=ctx("conclusions"),
            role="analysis",
            system=render("assess_system") + "\n\n# Результаты пунктов отчёта\n" + digest,
            user=render(
                "conclusions",
                requirement=" ".join(str(section5.get("requirement", "")).split()),
                integral=f"{level} (зоны: красная {integ.red}, жёлтая {integ.yellow}, зелёная {integ.green})",
                risks=lines or "Риски не оценены.",
            ),
            schema=SectionPayload,
            max_tokens=16_000,
            constrained=False,  # схема SectionPayload велика для грамматики (D-065)
        )
        result, warnings = _conclusions(payload, {sid: source_types[sid] for sid in allowed})
        if warnings:
            log.info("Выводы прогона %s: %s", run_id, "; ".join(warnings))
    except (LLMError, ValidationError, ValueError) as exc:
        log.warning("Выводы не сформированы: %s", exc)
        with db_factory() as db:
            run = db.get(AssessmentRun, run_id)
            run.conclusions_json = None
            db.commit()
        return
    with db_factory() as db:
        run = db.get(AssessmentRun, run_id)
        run.conclusions_json = result.model_dump(mode="json")
        run.conclusions_review = ReviewStatus.ai_draft
        db.commit()
