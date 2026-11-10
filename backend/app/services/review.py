"""Ревью экспертов (фаза 7): правки пунктов, рисков и выводов новыми версиями, история, комментарии,
LLM-ревью. Эксперт — автор (правило 3): ИИ-версия не перезаписывается, правка — отдельная версия с автором.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.assessment.risks import (
    check_scales,
    latest_items,
    latest_risks,
    record_conclusions,
    results_digest,
    risk_spec,
)
from app.assessment.runner import plan_items
from app.llm.client import CallContext, LLMError, get_llm
from app.llm.prompts import render
from app.models import (
    AssessmentRun,
    Comment,
    ItemStatus,
    Project,
    ResultAuthor,
    ReviewStatus,
    RiskResult,
    SectionResult,
    Source,
    User,
    UserRole,
)
from app.schemas.assessment import SOURCE_REF, SectionPayload, SourceRef
from app.schemas.review import CommentIn, CommentOut, ItemEdit, LlmReview, RiskEdit, VersionOut
from app.schemas.risk import RiskPayload
from app.services.assessment import _is_active, latest_run
from app.services.audit import audit
from app.services.risks import _can_review, _risks_busy, risk_ref
from app.template_engine.loader import parse_template


class ReviewEnqueuer(Protocol):
    def llm_review(self, run_id: uuid.UUID) -> None: ...


class CeleryReviewEnqueuer:
    def llm_review(self, run_id: uuid.UUID) -> None:
        from workers.tasks import enqueue_llm_review

        enqueue_llm_review(run_id)


@lru_cache
def get_review_enqueuer() -> ReviewEnqueuer:
    return CeleryReviewEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


def _run(db: Session, project: Project) -> AssessmentRun:
    run = latest_run(db, project)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Оценка ещё не запускалась")
    return run


def _run_sources(db: Session, run: AssessmentRun) -> dict[str, str]:
    return dict(db.execute(select(Source.sid, Source.type).where(Source.run_id == run.id)).all())


def _check_refs(texts: list[str], known: dict[str, str]) -> set[str]:
    cited = {sid for t in texts for sid in SOURCE_REF.findall(t or "")}
    unknown = sorted(cited - set(known))
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Нет таких источников в прогоне: {', '.join(unknown)} — ссылайтесь на метки из списка",
        )
    return cited


_ESCAPED_REF = re.compile(r"\\\[(S\d+)\\\]")


def _unescape(value):
    """Редакторы Markdown экранируют скобки ссылок на источники — возвращаем вид [S12]."""
    if isinstance(value, str):
        return _ESCAPED_REF.sub(lambda m: f"[{m.group(1)}]", value)
    if isinstance(value, list):
        return [_unescape(v) for v in value]
    if isinstance(value, dict):
        return {k: _unescape(v) for k, v in value.items()}
    return value


def _merge_payload(base: SectionPayload, edit: ItemEdit, known: dict[str, str]) -> SectionPayload:
    edit = ItemEdit.model_validate(_unescape(edit.model_dump(exclude_unset=True)))
    data = base.model_dump(mode="json")
    for field in (
        "disclosure_status",
        "summary",
        "analysis_md",
        "remarks",
        "recommendations",
        "assumptions",
        "gap",
        "tables",
    ):
        value = getattr(edit, field)
        if value is not None:
            data[field] = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            if field == "tables":
                data[field] = [t.model_dump(mode="json") for t in edit.tables or []]
    if data["disclosure_status"] != "not_disclosed":
        data["gap"] = None  # поля нераскрытия — только у нераскрытого пункта
    texts = [
        data["summary"],
        data["analysis_md"],
        *data["remarks"],
        *data["recommendations"],
        *data["assumptions"],
    ]
    texts += list((data.get("gap") or {}).values())
    texts += [str(c) for t in data["tables"] for row in t["rows"] for c in row]
    cited = _check_refs(texts, known) | {x for f in data["findings"] for x in f["source_ids"]}
    listed = {s["id"] for s in data["sources"] if s["id"] in known}
    data["sources"] = [
        SourceRef(id=sid, type=known[sid]).model_dump()  # type: ignore[arg-type]
        for sid in sorted(cited | listed, key=lambda x: int(x[1:]))
    ]
    try:
        payload = SectionPayload.model_validate(data)
        payload.check_citations()
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _human(exc)) from exc
    return payload


def _human(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(e["msg"].replace("Value error, ", "") for e in exc.errors())
    return str(exc)


# --- пункты ---


def edit_item(
    db: Session, principal: Principal, project: Project, item_id: str, edit: ItemEdit
) -> SectionResult:
    _can_review(db, principal, project, item_id)
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())  # версии — по очереди
    run = _run(db, project)
    rows = db.scalars(
        select(SectionResult)
        .where(SectionResult.run_id == run.id, SectionResult.item_id == item_id)
        .order_by(SectionResult.version)
    ).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пункт не найден в прогоне")
    current = rows[-1]
    if current.status in (ItemStatus.pending, ItemStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, "Пункт ещё выполняется")
    base_row = next((r for r in reversed(rows) if r.status == ItemStatus.done and r.payload_json), None)
    if base_row is not None:
        base = SectionPayload.model_validate(base_row.payload_json)
    else:  # пункт не сформирован ИИ — эксперт пишет его сам
        base = SectionPayload.model_construct(
            item_id=item_id,
            disclosure_status="not_disclosed",
            summary="",
            analysis_md="",
            tables=[],
            findings=[],
            remarks=[],
            recommendations=[],
            assumptions=[],
            gap=None,
            sources=[],
            confidence=1.0,
        )
    payload = _merge_payload(base, edit, _run_sources(db, run))
    row = SectionResult(
        org_id=project.org_id,
        project_id=project.id,
        run_id=run.id,
        item_id=item_id,
        version=current.version + 1,
        status=ItemStatus.done,
        payload_json=payload.model_dump(mode="json"),
        author=ResultAuthor.user,
        edited_by=principal.user_id,
        review_status=ReviewStatus.in_review,
        instruction=edit.note.strip(),
        started_at=_now(),
        finished_at=_now(),
    )
    db.add(row)
    audit(
        db,
        "review.item_edited",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run.id,
        item_id=item_id,
        version=row.version,
        from_version=current.version,
        note=edit.note,
    )
    db.commit()
    return row


def versions(db: Session, project: Project, item_id: str) -> list[VersionOut]:
    run = _run(db, project)
    rows = db.scalars(
        select(SectionResult)
        .where(
            SectionResult.run_id == run.id,
            SectionResult.org_id == project.org_id,
            SectionResult.item_id == item_id,
        )
        .order_by(SectionResult.version.desc())
    ).all()
    names = _names(db, {r.edited_by for r in rows})
    return [
        VersionOut(
            version=r.version,
            author=r.author.value,
            editor=names.get(r.edited_by) if r.edited_by else None,
            instruction=r.instruction,
            status=r.status.value,
            review_status=r.review_status.value,
            created_at=r.created_at,
            payload=SectionPayload.model_validate(r.payload_json) if r.payload_json else None,
        )
        for r in rows
    ]


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all())


# --- риски и выводы ---


def edit_risk(db: Session, principal: Principal, project: Project, code: str, edit: RiskEdit) -> RiskResult:
    spec = risk_spec(project)
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
    probe = latest_risks(db, _run(db, project).id).get(code)
    _can_review(db, principal, project, risk_ref(spec, probe.group if probe else "4.1"))
    run = _run(db, project)
    if _risks_busy(run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Идёт оценка рисков — дождитесь окончания")
    current = latest_risks(db, run.id).get(code)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Риск не найден — сначала оцените риски")
    if current.status != ItemStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "Риск не оценён — дождитесь оценки или оцените заново")
    try:
        check_scales(spec, edit.probability, edit.impact)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _check_refs([edit.rationale, *edit.mitigation], _run_sources(db, run))
    old = RiskPayload.model_validate(current.payload_json) if current.payload_json else None
    try:
        payload = RiskPayload(
            probability=edit.probability,
            impact=edit.impact,
            rationale=edit.rationale,
            mitigation=edit.mitigation,
            related_items=old.related_items if old else [],
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _human(exc)) from exc
    row = RiskResult(
        org_id=project.org_id,
        project_id=project.id,
        run_id=run.id,
        code=code,
        group=current.group,
        title=current.title,
        mandatory=current.mandatory,
        version=current.version + 1,
        status=ItemStatus.done,
        payload_json=payload.model_dump(),
        author=ResultAuthor.user,
        edited_by=principal.user_id,
        review_status=ReviewStatus.in_review,
    )
    db.add(row)
    audit(
        db,
        "review.risk_edited",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        code=code,
        version=row.version,
        note=edit.note,
    )
    db.commit()
    return row


def edit_conclusions(db: Session, principal: Principal, project: Project, edit: ItemEdit) -> AssessmentRun:
    template = parse_template(project.template_version.yaml_text)
    section = next((s for s in template["sections"] if s.get("kind") == "conclusion"), {})
    ref = str(section.get("id", "5"))
    _can_review(db, principal, project, ref)
    db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())  # правки по очереди
    run = _run(db, project)
    if _risks_busy(run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Идёт оценка рисков — дождитесь окончания")
    if not run.conclusions_json:
        raise HTTPException(status.HTTP_409_CONFLICT, "Выводы ещё не сформированы — оцените риски и выводы")
    before = run.conclusions_json
    payload = _merge_payload(SectionPayload.model_validate(before), edit, _run_sources(db, run))
    record_conclusions(run, payload.model_dump(mode="json"), "user", principal.user_id)
    run.conclusions_edited_by = principal.user_id
    run.conclusions_review = ReviewStatus.in_review
    # у выводов нет версий-строк: прежний текст — в журнале правок
    audit(
        db,
        "review.conclusions_edited",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        note=edit.note,
        before=before,
    )
    db.commit()
    return run


# --- комментарии ---


def _comment_out(c: Comment, names: dict) -> CommentOut:
    return CommentOut(
        id=c.id,
        ref=c.ref,
        author=names.get(c.author_id, "—"),
        text=c.text,
        created_at=c.created_at,
        resolved_at=c.resolved_at,
        resolved_by=names.get(c.resolved_by) if c.resolved_by else None,
    )


def list_comments(db: Session, project: Project, ref: str | None = None) -> list[CommentOut]:
    run = latest_run(db, project)
    if run is None:
        return []
    query = select(Comment).where(Comment.run_id == run.id, Comment.org_id == project.org_id)
    if ref:
        query = query.where(Comment.ref == ref)
    rows = db.scalars(query.order_by(Comment.created_at)).all()
    names = _names(db, {c.author_id for c in rows} | {c.resolved_by for c in rows})
    return [_comment_out(c, names) for c in rows]


def _staff(principal: Principal) -> None:
    if principal.role == UserRole.observer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")


def add_comment(db: Session, principal: Principal, project: Project, data: CommentIn) -> CommentOut:
    _staff(principal)
    run = _run(db, project)
    c = Comment(
        org_id=project.org_id,
        project_id=project.id,
        run_id=run.id,
        ref=data.ref,
        author_id=principal.user_id,
        text=data.text.strip(),
    )
    db.add(c)
    db.flush()
    audit(
        db,
        "review.comment",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        ref=data.ref,
    )
    db.commit()
    return _comment_out(c, _names(db, {c.author_id}))


def resolve_comment(db: Session, principal: Principal, project: Project, comment_id: uuid.UUID) -> CommentOut:
    _staff(principal)
    c = db.scalar(
        select(Comment).where(
            Comment.id == comment_id, Comment.project_id == project.id, Comment.org_id == project.org_id
        )
    )
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Комментарий не найден")
    if c.resolved_at is None:
        c.resolved_at, c.resolved_by = _now(), principal.user_id
        db.commit()
    return _comment_out(c, _names(db, {c.author_id, c.resolved_by}))


def open_comments(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    return dict(
        db.execute(
            select(Comment.ref, func.count())
            .where(Comment.run_id == run_id, Comment.resolved_at.is_(None))
            .group_by(Comment.ref)
        ).all()
    )


# --- LLM-ревью ---


def start_llm_review(
    db: Session, enqueuer: ReviewEnqueuer, principal: Principal, project: Project
) -> AssessmentRun:
    _staff(principal)
    run = _run(db, project)
    if _is_active(db, run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания оценки пунктов")
    stale = run.llm_review_at is None or _now() - run.llm_review_at > LLM_REVIEW_STALE
    if run.llm_review_status in ("queued", "running") and not stale:
        raise HTTPException(status.HTTP_409_CONFLICT, "Проверка уже выполняется")
    run.llm_review_status, run.llm_review_at = "queued", _now()
    audit(db, "review.llm_started", org_id=project.org_id, user_id=principal.user_id, project_id=project.id)
    db.commit()
    try:
        enqueuer.llm_review(run.id)
    except Exception as exc:  # очередь недоступна — не оставляем «в очереди» навсегда
        run.llm_review_status = "failed"
        db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Очередь задач недоступна, повторите позже"
        ) from exc
    db.refresh(run)
    return run


LLM_REVIEW_STALE = timedelta(minutes=40)  # больше лимита времени задачи review.llm (35 мин)


def run_llm_review(db: Session, run_id: uuid.UUID, llm=None) -> None:
    try:
        _llm_review(db, run_id, llm)
    except Exception as exc:  # любой сбой — «ошибка», а не вечное «выполняется»
        db.rollback()
        run = db.get(AssessmentRun, run_id)
        if run is not None:
            run.llm_review_status, run.llm_review_at = "failed", _now()
            run.llm_review_json = [
                {"kind": "error", "refs": [], "message": f"{type(exc).__name__}: {exc}"[:500]}
            ]
            db.commit()


def _llm_review(db: Session, run_id: uuid.UUID, llm=None) -> None:
    run = db.get(AssessmentRun, run_id)
    if run is None or run.llm_review_status not in ("queued", "running"):
        return
    run.llm_review_status = "running"
    db.commit()
    project = db.scalar(select(Project).where(Project.id == run.project_id, Project.org_id == run.org_id))
    template = parse_template(project.template_version.yaml_text)
    plan = plan_items(template, list(project.enabled_optional_items))
    report = results_digest(plan, latest_items(db, run.id))
    risks = [r for r in latest_risks(db, run.id).values() if r.payload_json]
    if risks:
        report += "\n\n# Риски\n" + "\n".join(
            f"- {r.code} «{r.title}»: вероятность {r.payload_json['probability']}, влияние "
            f"{r.payload_json['impact']}. {r.payload_json['rationale'][:500]}"
            for r in risks
        )
    if run.conclusions_json:
        c = SectionPayload.model_validate(run.conclusions_json)
        report += f"\n\n# Выводы\n{c.summary}\n{c.analysis_md[:6000]}"
    try:
        result = (llm or get_llm()).structured(
            ctx=CallContext(
                purpose="llm_review", org_id=run.org_id, project_id=run.project_id, run_id=run.id
            ),
            role="items",
            system=render("llm_review", report=report),
            user="Проверь отчёт и перечисли замечания.",
            schema=LlmReview,
            max_tokens=8192,
        )
    except LLMError as exc:
        run.llm_review_status, run.llm_review_json = (
            "failed",
            [{"kind": "error", "refs": [], "message": str(exc)[:500]}],
        )
        run.llm_review_at = _now()
        db.commit()
        return
    run.llm_review_json = [i.model_dump() for i in result.issues]
    run.llm_review_status, run.llm_review_at = "done", _now()
    db.commit()
