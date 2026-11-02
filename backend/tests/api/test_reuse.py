"""Экономия токенов: повторный прогон берёт неизменившиеся пункты из прошлого, модель по виду пункта,
общий контекст прогона (паспорт с метками + выжимки документов), бюджет прогона."""

import re
import uuid
from contextlib import nullcontext
from decimal import Decimal

from sqlalchemy import select

from app.assessment.runner import run_items
from app.core.config import get_settings
from app.models import AssessmentRun, Document, ItemStatus, LlmCall, Project, SectionResult, Source
from app.services import assessment as svc
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура
from tests.api.test_audit_fixes import _queued_run
from tests.conftest import upload_file


def _agent_items(llm, since: int) -> list[str]:
    return [c["purpose"].split(":", 1)[1] for c in llm.calls[since:] if c["purpose"].startswith("assess:")]


def test_unchanged_project_rerun_costs_nothing(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    assert c.post(f"{base}/runs").status_code == 202
    first_run = c.get(f"{base}/assessment").json()["run"]["id"]

    est = c.get(f"{base}/assessment/estimate").json()
    assert est["items_reused"] == 40 and Decimal(est["cost_usd_max"]) == 0
    calls = len(llm.calls)
    assert c.post(f"{base}/runs").status_code == 202
    assert _agent_items(llm, calls) == []  # ни одного обращения к ИИ

    state = c.get(f"{base}/assessment").json()
    assert state["run"]["id"] != first_run and state["run"]["status"] == "done"
    ai_items = [i for i in state["items"] if i["kind"] != "info"]
    assert all(i["reused"] and i["status"] == "done" for i in ai_items)
    # источники перенесены в реестр нового прогона: метки текста разрешаются, документ со страницей
    detail = c.get(f"{base}/assessment/items/3.4.1").json()
    sids = set(re.findall(r"\[(S\d+)\]", detail["payload"]["analysis_md"]))
    assert sids and sids <= {s["sid"] for s in detail["sources"]}
    assert any(s["type"] == "doc" and s["page"] for s in detail["sources"])
    own = set(db.scalars(select(Source.sid).where(Source.run_id == uuid.UUID(state["run"]["id"]))))
    assert sids <= own
    assert any("без обращения к ИИ" in w for w in detail["warnings"])


def test_new_document_recomputes_only_affected_items(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    assert c.post(f"{base}/runs").status_code == 202

    doc = upload_file(
        c, p["id"], "Смета_проекта.txt", "Сводный сметный расчёт. Итого 12 500 млн тенге.".encode()
    )
    category = db.get(Document, uuid.UUID(doc["id"])).category
    plan = svc._plan(db.get(Project, uuid.UUID(p["id"])))
    affected = {i.id for i in plan if not i.is_info and (not i.node.inputs or category in i.node.inputs)}
    changed = True
    while changed:  # и выводы над ними
        changed = False
        for i in plan:
            if not i.is_info and i.id not in affected and any(d in affected for d in i.depends_on):
                affected.add(i.id)
                changed = True
    assert 0 < len(affected) < 40

    calls = len(llm.calls)
    assert c.post(f"{base}/runs").status_code == 202
    assert set(_agent_items(llm, calls)) == affected
    items = {i["item_id"]: i for i in c.get(f"{base}/assessment").json()["items"]}
    assert all(items[i]["reused"] is (i not in affected) for i in items if items[i]["kind"] != "info")


def test_model_by_item_kind_and_shared_context(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    plan = {i.id: i for i in svc._plan(db.get(Project, uuid.UUID(p["id"])))}
    strong = set(get_settings().analysis_strong_kinds)

    def cite_passport_only(purpose, user, tools, server_tools):
        """Пункт ссылается на метку паспорта из общего контекста, не вызывая инструменты."""
        system = llm.calls[-1]["system"]
        passport = system.split("## Подтверждённый паспорт проекта")[1].split("## Документы проекта")[0]
        sid = re.search(r"\[(S\d+)\]", passport).group(1)
        item_id = purpose.split(":", 1)[1]
        return {
            "item_id": item_id,
            "disclosure_status": "partial",
            "summary": f"Мощность 320 т/сут [{sid}].",
            "analysis_md": f"По паспорту мощность 320 т/сут [{sid}].",
            "sources": [{"id": sid, "type": "doc"}],
            "confidence": 0.6,
        }

    llm.agent_handler = cite_passport_only
    assert c.post(f"/api/v1/projects/{p['id']}/runs").status_code == 202
    agent_calls = [x for x in llm.calls if x["purpose"].startswith("assess:")]
    for call in agent_calls:
        item = plan[call["purpose"].split(":", 1)[1]]
        assert call["role"] == ("analysis" if item.kind in strong else "items")
    assert any(x["role"] == "analysis" for x in agent_calls) and any(
        x["role"] == "items" for x in agent_calls
    )
    # общий контекст одинаков у всех пунктов одной модели (кэш), паспорт — с метками источников
    systems = {x["system"] for x in agent_calls}
    assert len(systems) == 1 and "Подтверждённый паспорт проекта" in next(iter(systems))
    detail = c.get(f"/api/v1/projects/{p['id']}/assessment/items/3.4.1").json()
    assert detail["status"] == "done" and detail["sources"][0]["type"] == "doc"  # ссылка из контекста принята


def test_run_budget_stops_ai_items(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    llm.agent_handler = agent_handler()
    run = _queued_run(c, p, db)
    db.add(
        LlmCall(
            org_id=run.org_id, project_id=run.project_id, run_id=run.id, purpose="x", model="m", cost_usd=999
        )
    )
    db.commit()
    run_items(lambda: nullcontext(db), run.id, concurrency=1)
    db.refresh(run)
    rows = db.scalars(select(SectionResult).where(SectionResult.run_id == run.id)).all()
    plan = {i.id: i for i in svc._plan(db.get(Project, run.project_id))}
    ai = [r for r in rows if not plan[r.item_id].is_info]
    assert all(r.status == ItemStatus.failed and "Бюджет" in r.error for r in ai)
    assert all(r.status == ItemStatus.done for r in rows if plan[r.item_id].is_info)  # бесплатные — выполнены
    assert run.status.value == "done" and "Бюджет" in run.error
    assert db.get(AssessmentRun, run.id) is not None


def test_document_digest_built_and_shown_in_context(ready_project, llm, db):  # noqa: F811
    from app.pipeline.digest import DocDigest, ensure_digests

    c, p = ready_project
    pid = uuid.UUID(p["id"])
    project = db.get(Project, pid)

    def digest(purpose, user, schema):
        assert purpose == "document_digest" and schema is DocDigest and "[стр. 1]" in user
        return {
            "summary": "ТЭО мукомольного завода.",
            "sections": [{"title": "Технология", "pages": "1"}],
            "key_facts": [{"fact": "Мощность 320 т/сут", "page": 1}],
        }

    llm.handler = digest
    assert ensure_digests(db, project.org_id, pid) == 1
    assert ensure_digests(db, project.org_id, pid) == 0  # один раз на документ
    llm.agent_handler = agent_handler()
    assert c.post(f"/api/v1/projects/{p['id']}/runs").status_code == 202
    system = next(x["system"] for x in llm.calls if x["purpose"].startswith("assess:"))
    assert "ТЭО мукомольного завода." in system and "Мощность 320 т/сут (стр. 1)" in system


def test_out_of_credit_stops_run_and_resume_finishes_only_failed(ready_project, llm, db):  # noqa: F811
    """Баланс Anthropic кончился посреди прогона: платные пункты не перебираются впустую; после пополнения
    «Довыполнить» считает только пункты с ошибкой, готовые не пересчитываются."""
    from app.llm.client import BILLING_MESSAGE, LLMBillingError

    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    normal = agent_handler()
    paid: list[str] = []

    def until_broke(purpose, user, tools, server_tools):
        paid.append(purpose)
        if len(paid) > 5:
            raise LLMBillingError(BILLING_MESSAGE)
        return normal(purpose, user, tools, server_tools)

    llm.agent_handler = until_broke
    assert c.post(f"{base}/runs").status_code == 202
    assert len(paid) == 6  # после первой ошибки баланса платные пункты не запускались
    state = c.get(f"{base}/assessment").json()
    done_before = {i["item_id"] for i in state["items"] if i["status"] == "done"}
    failed = [i for i in state["items"] if i["status"] == "failed"]
    assert len(failed) == 35 and all("балансе Anthropic" in i["error"] for i in failed)
    assert all(i["status"] == "done" for i in state["items"] if i["kind"] == "info")  # бесплатные выполнены

    est = c.get(f"{base}/assessment/estimate").json()
    assert est["resume_items"] == 35 and Decimal(est["resume_cost_usd_max"]) > 0

    llm.agent_handler = normal
    calls = len(llm.calls)
    assert c.post(f"{base}/assessment/resume").status_code == 202
    resumed = set(_agent_items(llm, calls))
    assert resumed == {i["item_id"] for i in failed}  # готовые не пересчитывались
    after = c.get(f"{base}/assessment").json()
    assert after["run"]["status"] == "done" and after["run"]["items_failed"] == 0
    assert {
        i["item_id"] for i in after["items"] if i["version"] == 1 and i["status"] == "done"
    } >= done_before
    assert c.post(f"{base}/assessment/resume").status_code == 409  # больше нечего довыполнять
