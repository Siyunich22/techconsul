import json
import re
import uuid

import pytest
from sqlalchemy import select

from app.llm.client import LLMOutputError
from app.models import AuditLog, Passport, PassportStatus, Project, Source
from tests.conftest import upload_file

TEO = "ТЭО_завод.txt"
TEO_TEXT = "ТЭО. Производственная мощность — 320 тонн в сутки. Мельница Bühler, стоимость 6 950 млн тенге."


def _confirm_passport(db, project_id: str, doc_id: str) -> None:
    p = db.scalar(select(Passport).where(Passport.project_id == uuid.UUID(project_id)))
    project = db.get(Project, uuid.UUID(project_id))
    if p is None:
        p = Passport(org_id=project.org_id, project_id=project.id, status=PassportStatus.confirmed)
        db.add(p)
    p.status = PassportStatus.confirmed
    p.data_json = {
        "technology": {"capacity": {"value": "320", "unit": "т/сут", "refs": ["P1"], "confidence": 0.9}}
    }
    p.sources_json = {
        "P1": {"document_id": doc_id, "filename": TEO, "category": "тэо_bfs", "page": 1, "sheet": None}
    }
    p.discrepancies_json = []
    db.commit()


@pytest.fixture
def ready_project(project, db):
    c, p = project
    doc = upload_file(c, p["id"], TEO, TEO_TEXT.encode())
    _confirm_passport(db, p["id"], doc["id"])
    return c, p


def agent_handler(fail: set[str] | None = None, calls: list | None = None):
    """Мок агента: ищет по документам (получает метку S…), ссылается на неё и на веб-источник W1."""

    def handler(purpose, user, tools, server_tools):
        item_id = purpose.split(":", 1)[1]
        if calls is not None:
            calls.append(item_id)
        if fail and item_id in fail:
            raise LLMOutputError("модель вернула невалидный JSON дважды")
        found = (
            tools["search_project_docs"].run({"query": "мощность"}) if "search_project_docs" in tools else ""
        )
        passport = tools["get_passport"].run({})
        sid = (re.findall(r"\[(S\d+)\]", found) or re.findall(r"\[(S\d+)\]", passport))[0]
        return {
            "item_id": item_id,
            "disclosure_status": "partial",
            "summary": f"Вывод по пункту {item_id}.",
            "analysis_md": f"Мощность 320 т/сут [{sid}]. Мировые аналоги [W1]. Выдуманный источник [S999].",
            "findings": [{"text": "Мощность 320 т/сут", "source_ids": [sid]}],
            "remarks": ["Нет ПСД"],
            "assumptions": [f"Допущение {item_id}"],
            "sources": [
                {"id": sid, "type": "doc"},
                {"id": "W1", "type": "web", "url": "https://example.org/mills", "title": "Обзор мельниц"},
            ],
            "confidence": 0.7,
        }

    return handler


def test_estimate_and_preconditions(project, db):
    c, p = project
    base = f"/api/v1/projects/{p['id']}"
    est = c.get(f"{base}/assessment/estimate").json()
    assert est["items_ai"] == 40 and est["items_info"] == 3  # 43 пункта разделов 2–3, раздел 3.6 выключен
    assert not est["passport_confirmed"] and est["warnings"]
    assert float(est["cost_usd_max"]) > float(est["cost_usd_min"]) > 0
    assert c.post(f"{base}/runs").status_code == 409  # паспорт не подтверждён
    assert c.get(f"{base}/assessment").json() == {"run": None, "items": []}


def test_full_run_with_failed_item(ready_project, llm, db):
    """Критерий приёмки фазы 5: все пункты получают валидный SectionResult; падение одного не валит прогон."""
    c, p = ready_project
    order: list[str] = []
    llm.agent_handler = agent_handler(fail={"3.3.2"}, calls=order)
    base = f"/api/v1/projects/{p['id']}"
    r = c.post(f"{base}/runs")
    assert r.status_code == 202, r.text

    state = c.get(f"{base}/assessment").json()
    run, items = state["run"], {i["item_id"]: i for i in state["items"]}
    assert run["status"] == "done" and run["items_total"] == 43 == len(items)
    assert run["items_failed"] == 1 and run["items_done"] == 42
    assert items["3.3.2"]["status"] == "failed" and "невалидный" in items["3.3.2"]["error"]
    assert all(i["status"] == "done" for k, i in items.items() if k != "3.3.2")
    assert c.get(f"/api/v1/projects/{p['id']}").json()["status"] == "review"

    # порядок по depends_on: выводы после своих пунктов, 3.1.3 — после разделов 3.2–3.7
    assert order.index("3.3.5") > max(order.index(x) for x in ("3.3.1", "3.3.3", "3.3.4"))
    assert order.index("3.1.3") > order.index("3.7.7")
    conclusion = next(x for x in llm.calls if x["purpose"] == "assess:3.3.5")
    assert "Пункт 3.3.1" in conclusion["user"] and "Пункт 3.3.2: не выполнен" in conclusion["user"]
    assert "Допущение 3.4.1" in next(x for x in llm.calls if x["purpose"] == "assess:3.1.3")["user"]

    # инструменты — из YAML пункта; веб — только там, где он в tools
    call_341 = next(x for x in llm.calls if x["purpose"] == "assess:3.4.1")
    assert {"search_project_docs", "calc", "get_passport"} <= set(call_341["tools"])
    assert call_341["server_tools"] == ["web_search", "web_fetch"]
    assert next(x for x in llm.calls if x["purpose"] == "assess:3.3.3")["server_tools"] == []

    # информационные пункты — из данных проекта, без ИИ
    assert not any(x["purpose"] in ("assess:2.1", "assess:2.2", "assess:3.1.2") for x in llm.calls)
    docs = c.get(f"{base}/assessment/items/2.2").json()
    assert (
        docs["payload"]["tables"][0]["rows"][0][1] == TEO and docs["payload"]["disclosure_status"] == "full"
    )
    assert c.get(f"{base}/assessment/items/3.1.2").json()["payload"]["disclosure_status"] == "not_disclosed"

    # источники: S-метка документа со страницей, W1 → S-метка с датой обращения, выдуманная удалена
    detail = c.get(f"{base}/assessment/items/3.4.1").json()
    payload, sources = detail["payload"], {s["sid"]: s for s in detail["sources"]}
    assert "[S999]" not in payload["analysis_md"] and "[W1]" not in payload["analysis_md"]
    assert any("S999" in w for w in detail["warnings"])
    doc_src = next(s for s in sources.values() if s["type"] == "doc")
    assert doc_src["filename"] == TEO and doc_src["page"] == 1
    web = next(s for s in sources.values() if s["type"] == "web")
    assert web["url"] == "https://example.org/mills" and web["accessed_at"]
    assert set(re.findall(r"\[(S\d+)\]", payload["analysis_md"])) <= set(sources)
    assert db.scalar(select(AuditLog).where(AuditLog.action == "assessment.started")) is not None
    # один реестр на прогон: один и тот же фрагмент — одна метка во всех пунктах
    sids = db.scalars(select(Source.sid).where(Source.run_id == uuid.UUID(run["id"]))).all()
    assert len(sids) == len(set(sids))


def test_rerun_item_with_instruction(ready_project, llm):
    c, p = ready_project
    llm.agent_handler = agent_handler(fail={"3.4.2"})
    base = f"/api/v1/projects/{p['id']}"
    c.post(f"{base}/runs")
    assert c.post(f"{base}/runs").status_code in (202, 409)  # повторный запуск после завершения допустим

    llm.agent_handler = agent_handler()
    r = c.post(f"{base}/assessment/items/3.4.2/rerun", json={"instruction": "учти КП от 15.09"})
    assert r.status_code == 202, r.text
    item = c.get(f"{base}/assessment/items/3.4.2").json()
    assert item["status"] == "done" and item["version"] == 2 and item["instruction"] == "учти КП от 15.09"
    assert "Дополнительное указание эксперта: учти КП от 15.09" in llm.calls[-1]["user"]
    assert c.get(f"{base}/assessment").json()["run"]["status"] == "done"
    assert c.post(f"{base}/assessment/items/9.9/rerun", json={}).status_code == 404


def test_events_stream_and_isolation(ready_project, llm, register):
    c, p = ready_project
    llm.agent_handler = agent_handler()
    base = f"/api/v1/projects/{p['id']}"
    run_id = c.post(f"{base}/runs").json()["id"]
    with c.stream("GET", f"{base}/runs/{run_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    events = re.findall(r"event: (\w+)\ndata: (.*)\n\n", body)
    assert [e for e, _ in events] == ["progress", "end"]
    assert json.loads(events[0][1])["run"]["status"] == "done"

    other = register("ТОО Чужая")
    for url in ("/assessment", "/assessment/estimate", f"/runs/{run_id}/events", "/assessment/items/3.4.1"):
        assert other.get(f"{base}{url}").status_code == 404, url
    assert other.post(f"{base}/runs").status_code == 404
    assert other.post(f"{base}/assessment/items/3.4.1/rerun", json={}).status_code == 404


@pytest.mark.live
def test_live_agent_items(project, sample_files, db):
    """Агент на реальной модели (Opus): пункты с документами, расчётами и веб-поиском дают валидный
    SectionResult с источниками-страницами. Весь прогон не запускаем — дорого для теста."""
    import shutil
    from contextlib import nullcontext

    from app.assessment.runner import run_items
    from app.llm.client import set_llm_override
    from app.services import assessment as svc
    from tests.fixtures.sample_project.generate import EXPECTED_CATEGORIES

    if not (shutil.which("tesseract") and shutil.which("soffice")):
        pytest.skip("нужны tesseract и LibreOffice")
    set_llm_override(None)
    c, p = project
    docs = [upload_file(c, p["id"], n, (sample_files / n).read_bytes()) for n in EXPECTED_CATEGORIES]
    teo = next(d for d in docs if d["filename"].startswith("ТЭО"))
    _confirm_passport(db, p["id"], teo["id"])

    class NoQueue:
        def run(self, run_id, only=None):
            pass

    from app.api.deps import Principal
    from app.models import User

    user = db.scalar(select(User).where(User.email == c.email))
    proj = db.get(Project, uuid.UUID(p["id"]))
    run = svc.start_run(db, NoQueue(), Principal(user=user), proj)
    items = ["2.2", "3.4.2", "3.4.1"]
    run_items(lambda: nullcontext(db), run.id, items, concurrency=1)

    base = f"/api/v1/projects/{p['id']}/assessment/items"
    for item_id in items:
        d = c.get(f"{base}/{item_id}").json()
        assert d["status"] == "done", (item_id, d["error"])
        assert d["payload"]["disclosure_status"] in ("full", "partial"), d["payload"]
        assert any(s["type"] == "doc" and s["page"] for s in d["sources"]), d["sources"]
    equipment = c.get(f"{base}/3.4.1").json()
    print(
        "3.4.1:", equipment["payload"]["summary"], [s["sid"] + " " + s["type"] for s in equipment["sources"]]
    )
