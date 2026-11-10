"""Регрессия по проверке фаз 6–8: гонки этапа рисков, история выводов, выпуск, устойчивость сборки."""

import contextlib
import io
import uuid

from docx import Document
from sqlalchemy import select

from app.models import AssessmentRun, Project, ReportStatus, ReportVersion
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура
from tests.api.test_risks import risk_handler


def _run(db, p) -> AssessmentRun:
    return db.scalars(
        select(AssessmentRun)
        .where(AssessmentRun.project_id == uuid.UUID(p["id"]))
        .order_by(AssessmentRun.created_at.desc())
    ).first()


def _sid(c, base: str) -> str:
    return c.get(f"{base}/assessment/items/3.4.1").json()["sources"][0]["sid"]


def test_no_edits_while_risks_stage_runs_and_versions_are_explicit(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")
    llm.handler = risk_handler(llm)
    c.post(f"{base}/risks/run")
    sid = _sid(c, base)
    run = _run(db, p)
    run.risks_status = "running"
    db.commit()
    body = {
        "probability": 2,
        "impact": 2,
        "rationale": f"Пересмотр экспертом по документам проекта [{sid}].",
        "mitigation": ["M"],
    }
    assert c.put(f"{base}/risks/T1/edit", json=body).status_code == 409
    assert c.put(f"{base}/risks/T1/review", json={"status": "approved"}).status_code == 409
    assert c.put(f"{base}/conclusions/edit", json={"summary": "x"}).status_code == 409
    run.risks_status = "done"
    db.commit()
    assert (
        c.put(f"{base}/risks/T1/edit", json=body | {"probability": 9}).status_code == 422
    )  # вне шкалы шаблона
    assert c.put(f"{base}/risks/T1/edit", json=body).status_code == 200
    # утверждение конкретной версии: устаревшая — 409, текущая — да
    assert c.put(f"{base}/risks/T1/review", json={"status": "approved", "version": 1}).status_code == 409
    assert c.put(f"{base}/risks/T1/review", json={"status": "approved", "version": 2}).status_code == 200
    item_v = c.get(f"{base}/assessment/items/3.4.1").json()["version"]
    assert (
        c.put(
            f"{base}/assessment/items/3.4.1/review", json={"status": "approved", "version": item_v + 1}
        ).status_code
        == 409
    )


def test_rerun_keeps_conclusions_history_and_supersedes_extra_risks(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")
    llm.handler = risk_handler(llm, extra=2)
    c.post(f"{base}/risks/run")
    sid = _sid(c, base)
    assert (
        c.put(f"{base}/conclusions/edit", json={"summary": f"Экспертный вывод [{sid}]."}).status_code == 200
    )

    llm.handler = risk_handler(llm, extra=1)  # повторная оценка: 1 дополнительный риск вместо 2
    c.post(f"{base}/risks/run")
    data = c.get(f"{base}/risks").json()
    assert [x["code"] for x in data["risks"] if not x["mandatory"]] == ["X1"]  # X2 снят
    run = _run(db, p)
    db.refresh(run)
    authors = [h["author"] for h in run.conclusions_history]
    assert authors == ["ai", "user", "ai"]  # правка эксперта сохранена в истории
    assert run.conclusions_history[1]["payload"]["summary"] == f"Экспертный вывод [{sid}]."
    assert run.conclusions_edited_by is None  # новые выводы ИИ не помечены как правка
    cov = c.get(f"{base}/coverage").json()
    assert "X2" not in {r["ref"] for r in cov["rows"]}

    # сбой выводов при повторной оценке — прежние выводы остаются
    def broken(purpose, user, schema):
        if purpose == "conclusions":
            raise ValueError("сбой")
        return risk_handler(llm)(purpose, user, schema)

    llm.handler = broken
    c.post(f"{base}/risks/run")
    assert c.get(f"{base}/risks").json()["conclusions"] is not None


def test_release_rechecked_at_render_and_failed_release_frees_number(ready_project, llm, db):  # noqa: F811
    from app.api.deps import Principal
    from app.models import User
    from app.services import reports as rsvc

    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")

    class NoQueue:
        def render(self, report_id):
            pass

    user = db.scalar(select(User).where(User.email == c.email))
    project = db.get(Project, uuid.UUID(p["id"]))
    rel = ReportVersion(
        org_id=project.org_id,
        project_id=project.id,
        run_id=_run(db, p).id,
        number=1,
        version="1.0",
        status=ReportStatus.released,
        filename="x",
        created_by=user.id,
    )
    db.add(rel)
    db.commit()
    rsvc.render(db, None, rel.id, with_pdf=False)  # валидатор не чист — выпуск не собирается
    db.refresh(rel)
    assert rel.render_status.value == "failed" and "состав изменился" in rel.error
    draft = rsvc.create_draft(db, NoQueue(), Principal(user=user), project)
    assert draft.version == "0.2"
    _ = io


def test_reference_docx_without_heading_styles_and_control_chars(ready_project, llm, db, storage):  # noqa: F811
    from datetime import date

    from app.report.data import collect
    from app.report.docx_render import render_docx

    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")
    sid = _sid(c, base)
    c.put(f"{base}/assessment/items/3.4.1/edit", json={"summary": f"Текст\x0bс\x0cуправляющими [{sid}]."})

    ref = Document()
    for name in ("Heading 1", "Heading 2", "Heading 3", "Caption", "List Bullet"):
        with contextlib.suppress(KeyError):
            ref.styles.element.remove(ref.styles[name].element)
    buf = io.BytesIO()
    ref.save(buf)
    up = c.post(
        "/api/v1/org/reference-docx", files={"file": ("ref.docx", buf.getvalue(), "application/octet-stream")}
    )
    assert up.status_code == 200, up.text
    project = db.get(Project, uuid.UUID(p["id"]))
    data = collect(db, storage, project, _run(db, p), version="0.1", released=False, created=date.today())
    docx, toc = render_docx(data)
    doc = Document(io.BytesIO(docx))
    assert any(par.style.name == "Heading 3" and par.text.startswith("3.4.1 ") for par in doc.paragraphs)
    assert "Текст с управляющими" in "\n".join(par.text for par in doc.paragraphs)


def test_llm_review_unexpected_failure_is_not_stuck(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")

    def boom(purpose, user, schema):
        raise RuntimeError("обрыв")

    llm.handler = boom
    assert c.post(f"{base}/coverage/llm-review").status_code == 202
    assert c.get(f"{base}/coverage").json()["llm_review_status"] == "failed"
    assert c.post(f"{base}/coverage/llm-review").status_code == 202  # можно повторить
