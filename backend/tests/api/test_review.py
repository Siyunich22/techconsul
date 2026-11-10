"""Фаза 7: правки экспертов версиями, история, комментарии, LLM-ревью; эксперт — автор."""

import re

from sqlalchemy import select

from app.models import AuditLog
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура
from tests.api.test_risks import risk_handler


def _sid(c, base: str, item: str) -> str:
    return c.get(f"{base}/assessment/items/{item}").json()["sources"][0]["sid"]


def test_expert_edits_item_as_new_version(ready_project, llm, db, mailer, new_client):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    assert c.post(f"{base}/runs").status_code == 202
    sid = _sid(c, base, "3.4.1")
    url = f"{base}/assessment/items/3.4.1/edit"

    assert c.put(url, json={"summary": "Ссылка на чужое [S999]."}).status_code == 422
    bad = c.put(
        url, json={"disclosure_status": "full", "summary": "Без источников.", "analysis_md": "Текст."}
    )
    assert bad.status_code == 200  # источники пункта сохраняются из прежней версии
    ok = c.put(
        url,
        json={
            "summary": f"Оборудование выбрано обоснованно [{sid}].",
            "analysis_md": f"Мощность мельницы 320 т/сут [{sid}].",
            "remarks": ["Запросить третье КП"],
            "note": "Уточнён вывод",
        },
    )
    assert ok.status_code == 200, ok.text
    d = ok.json()
    assert d["version"] == 3 and d["author"] == "user" and d["review_status"] == "in_review"
    assert d["payload"]["remarks"] == ["Запросить третье КП"] and d["sources"]

    versions = c.get(f"{base}/assessment/items/3.4.1/versions").json()
    assert [(v["version"], v["author"]) for v in versions] == [(3, "user"), (2, "user"), (1, "ai")]
    assert versions[0]["editor"] == "Иванов Иван" and versions[0]["instruction"] == "Уточнён вывод"
    assert "Оборудование выбрано" not in versions[2]["payload"]["summary"]  # ИИ-версия сохранена
    assert db.scalar(select(AuditLog).where(AuditLog.action == "review.item_edited")) is not None

    # экранированные редактором Markdown ссылки (обратная косая перед скобками) сохраняются как [S1]
    esc = c.put(url, json={"analysis_md": "Уточнение \\[" + sid + "\\]."}).json()
    assert esc["payload"]["analysis_md"] == f"Уточнение [{sid}]."

    # нераскрытый пункт без 4 полей — ошибка правила 2
    gap = c.put(url, json={"disclosure_status": "not_disclosed"})
    assert gap.status_code == 422

    # эксперт чужого раздела не правит
    ex = c.post("/api/v1/org/experts", json={"full_name": "Эксперт 3.2"}).json()
    c.put(f"{base}/members", json=[{"expert_id": ex["id"], "assigned_items": ["3.2"]}])
    c.post(
        "/api/v1/org/invitations", json={"email": "ed32@example.kz", "full_name": "Э", "expert_id": ex["id"]}
    )
    token = mailer.last_link("ed32@example.kz").rsplit("/", 1)[-1]
    e = new_client()
    e.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})
    assert e.put(url, json={"summary": "x"}).status_code == 403
    sid32 = _sid(c, base, "3.2.1")
    assert (
        e.put(f"{base}/assessment/items/3.2.1/edit", json={"summary": f"Площадка [{sid32}]."}).status_code
        == 200
    )

    # отчёт берёт последнюю версию (правку эксперта); матрица отмечает правку
    cov = {r["ref"]: r for r in c.get(f"{base}/coverage").json()["rows"]}
    assert cov["3.4.1"]["edited"] and not cov["3.3.1"]["edited"]


def test_risk_and_conclusions_edit_comments_llm_review(ready_project, llm, db, new_client):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    llm.agent_handler = agent_handler()
    c.post(f"{base}/runs")
    llm.handler = risk_handler(llm)
    c.post(f"{base}/risks/run")
    sid = _sid(c, base, "3.4.1")

    r = c.put(
        f"{base}/risks/T3/edit",
        json={
            "probability": 3,
            "impact": 2,
            "rationale": f"Пересмотрено экспертом: получены два КП с близкими ценами [{sid}].",
            "mitigation": ["Тендер"],
        },
    )
    assert r.status_code == 200, r.text
    t3 = next(x for x in r.json()["risks"] if x["code"] == "T3")
    assert (t3["probability"], t3["impact"], t3["zone"], t3["version"]) == (3, 2, "yellow", 2)
    assert (
        c.put(
            f"{base}/risks/T3/edit",
            json={"probability": 9, "impact": 2, "rationale": "x", "mitigation": ["y"]},
        ).status_code
        == 422
    )

    concl = c.put(f"{base}/conclusions/edit", json={"summary": f"Проект реализуем [{sid}].", "note": "Итог"})
    assert concl.status_code == 200 and concl.json()["conclusions"]["summary"].startswith("Проект реализуем")
    assert concl.json()["conclusions_review"] == "in_review"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "review.conclusions_edited"))
    assert log is not None and "before" in log.payload_json  # прежний текст — в журнале

    # комментарии: добавить, видны в матрице как предупреждение, закрыть
    created = c.post(f"{base}/comments", json={"ref": "3.4.1", "text": "Нет третьего КП"})
    assert created.status_code == 201
    cov = c.get(f"{base}/coverage").json()
    assert {r["ref"]: r["comments"] for r in cov["rows"]}["3.4.1"] == 1
    assert any(i["rule"] == "comments" and i["level"] == "warning" for i in cov["issues"])
    assert [x["text"] for x in c.get(f"{base}/comments", params={"ref": "3.4.1"}).json()] == [
        "Нет третьего КП"
    ]
    resolved = c.post(f"{base}/comments/{created.json()['id']}/resolve").json()
    assert resolved["resolved_at"] and resolved["resolved_by"] == "Иванов Иван"
    assert {r["ref"]: r["comments"] for r in c.get(f"{base}/coverage").json()["rows"]}["3.4.1"] == 0

    # LLM-ревью: замечания — предупреждения матрицы, выпуск не блокируют
    def review(purpose, user, schema):
        assert purpose == "llm_review"
        assert re.search(r"3\.4\.1", llm.calls[-1]["system"])  # отчёт целиком в контексте
        return {
            "issues": [
                {"kind": "contradiction", "refs": ["3.3.3", "3.4.5"], "message": "Мощность 320 и 300 т/сут"}
            ]
        }

    llm.handler = review
    out = c.post(f"{base}/coverage/llm-review")
    assert out.status_code == 202
    cov = c.get(f"{base}/coverage").json()
    assert cov["llm_review_status"] == "done"
    llm_issues = [i for i in cov["issues"] if i["rule"] == "llm_review"]
    assert llm_issues and llm_issues[0]["level"] == "warning" and "320 и 300" in llm_issues[0]["message"]
