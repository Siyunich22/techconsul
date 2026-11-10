"""Фазы 6–7: риски (15 обязательных из шаблона), выводы, ревью экспертов, матрица покрытия и валидатор."""

import re
import uuid

from app.assessment.risks import integral, risk_spec
from app.models import Project
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура


def risk_handler(llm, fail: set[str] | None = None, extra: int = 1):
    """Мок модели этапа рисков: по риску — оценка со ссылкой на метку из результатов пунктов."""

    def handler(purpose, user, schema):
        system = llm.calls[-1]["system"]
        sids = re.findall(r"\[(S\d+)\]", system.split("# Результаты пунктов отчёта", 1)[-1])
        sid = sids[0] if sids else "S1"
        if purpose.startswith("risk:") and purpose != "risk:extra":
            code = purpose.split(":", 1)[1]
            if fail and code in fail:
                raise ValueError("сбой")
            p = 5 if code == "T3" else 2
            return {
                "probability": p,
                "impact": 3 if code == "T3" else 1,
                "rationale": f"Обоснование риска {code}: мощность 320 т/сут [{sid}], выдумка [S999].",
                "mitigation": [f"Мероприятие по {code}"],
                "related_items": ["3.4.1", "9.9.9"],
            }
        if purpose == "risk:extra":
            return {
                "risks": [
                    {
                        "title": f"Риск дополнительный {n}",
                        "group": "4.1",
                        "probability": 3,
                        "impact": 2,
                        "rationale": f"Дополнительный риск по результатам пунктов [{sid}] — обоснование.",
                        "mitigation": ["Мера"],
                    }
                    for n in range(1, extra + 1)
                ]
            }
        if purpose == "conclusions":
            return {
                "item_id": "5",
                "disclosure_status": "partial",
                "summary": f"Проект реализуем при условиях [{sid}].",
                "analysis_md": f"### Достаточность\nДокументация частично достаточна [{sid}].",
                "tables": [
                    {
                        "title": "Нераскрытые пункты",
                        "columns": ["Пункт", "Причина"],
                        "rows": [["3.1.2", "Нет команды"]],
                    }
                ],
                "sources": [{"id": sid, "type": "doc"}],
                "confidence": 0.7,
            }
        raise AssertionError(purpose)

    return handler


def test_risks_conclusions_review_and_validator(ready_project, llm, db, mailer, new_client):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    assert c.post(f"{base}/risks/run").status_code == 409  # оценки ещё нет
    llm.agent_handler = agent_handler()
    assert c.post(f"{base}/runs").status_code == 202

    llm.handler = risk_handler(llm, fail={"T7"})
    r = c.post(f"{base}/risks/run")
    assert r.status_code == 202, r.text
    data = c.get(f"{base}/risks").json()
    assert data["status"] == "done" and "T7" in data["error"]
    codes = [x["code"] for x in data["risks"]]
    spec = risk_spec(db.get(Project, uuid.UUID(p["id"])))
    assert codes[:15] == [d.code for d in spec.risks] and codes[15:] == ["X1"]  # порядок шаблона + доп.
    t3 = next(x for x in data["risks"] if x["code"] == "T3")
    assert (t3["probability"], t3["impact"], t3["zone"]) == (5, 3, "red")
    assert "[S999]" not in t3["rationale"] and any("S999" in w for w in t3["warnings"])
    assert t3["related_items"] == ["3.4.1"]  # несуществующий пункт отброшен
    assert next(x for x in data["risks"] if x["code"] == "T7")["status"] == "failed"
    # интегральный уровень — по обязательным оценённым рискам, худшая зона
    assert data["integral"] == {
        "level": "red",
        "red": 1,
        "yellow": 0,
        "green": 13,
        "scored": 14,
        "total": 15,
        "index": round((15 + 13 * 2) / (14 * 15), 3),
    }
    assert data["conclusions"]["item_id"] == "5" and data["conclusions"]["tables"]
    assert data["sources"] and all(s["sid"].startswith("S") for s in data["sources"])
    # модели: риски — обычная (Sonnet), выводы — сильная
    roles = {x["purpose"]: x["role"] for x in llm.calls if x["purpose"].startswith(("risk:", "conclusions"))}
    assert roles["risk:T1"] == "items" and roles["conclusions"] == "analysis"

    # матрица покрытия: валидатор блокирует выпуск
    cov = c.get(f"{base}/coverage").json()
    kinds = {row["kind"] for row in cov["rows"]}
    assert kinds == {"item", "risk", "conclusions"} and not cov["can_release"]
    rules = {i["rule"] for i in cov["issues"] if i["level"] == "error"}
    assert {
        "all_items_expert_approved",
        "mandatory_risks_scored_with_rationale_and_mitigation",
        "independence_block_completed",
    } <= rules
    assert c.post(f"{base}/reports", json={"release": True}).status_code == 409

    # ревью: эксперт вне раздела — 403; руководитель утверждает; неготовое не утверждается
    ex = c.post("/api/v1/org/experts", json={"full_name": "Эксперт 3.2"}).json()
    c.put(f"{base}/members", json=[{"expert_id": ex["id"], "assigned_items": ["3.2"]}])
    c.post(
        "/api/v1/org/invitations", json={"email": "r32@example.kz", "full_name": "Э", "expert_id": ex["id"]}
    )
    token = mailer.last_link("r32@example.kz").rsplit("/", 1)[-1]
    e = new_client()
    e.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})
    assert e.put(f"{base}/assessment/items/3.4.1/review", json={"status": "approved"}).status_code == 403
    assert e.put(f"{base}/assessment/items/3.2.1/review", json={"status": "approved"}).status_code == 200
    assert e.put(f"{base}/risks/T1/review", json={"status": "approved"}).status_code == 403
    assert c.put(f"{base}/risks/T7/review", json={"status": "approved"}).status_code == 409  # не оценён

    # всё утвердить → валидатор пропускает (кроме T7 и независимости)
    for row in cov["rows"]:
        if row["kind"] == "item":
            c.put(f"{base}/assessment/items/{row['ref']}/review", json={"status": "approved"})
        elif row["kind"] == "risk" and row["ref"] != "T7":
            c.put(f"{base}/risks/{row['ref']}/review", json={"status": "approved"})
    c.put(f"{base}/risks/X1/review", json={"status": "approved"})
    assert c.put(f"{base}/conclusions/review", json={"status": "approved"}).status_code == 200
    errors = [i for i in c.get(f"{base}/coverage").json()["issues"] if i["level"] == "error"]
    remaining = {(i["rule"], i["ref"]) for i in errors if i["rule"] != "independence_block_completed"}
    # T7 не оценён; в 3.4.1 таблица сравнения короче min_alternatives из шаблона
    assert remaining == {
        ("mandatory_risks_scored_with_rationale_and_mitigation", "T7"),
        ("min_alternatives_3_4_1", "3.4.1"),
    }


def test_integral_formula(ready_project, db):  # noqa: F811
    _, p = ready_project
    spec = risk_spec(db.get(Project, uuid.UUID(p["id"])))
    assert integral(spec, [], 15).level is None
    assert integral(spec, [(1, 1), (2, 2)], 15).level == "green"  # 4 < 5
    assert integral(spec, [(1, 1), (5, 1)], 15).level == "yellow"  # p·i = 5
    one_red = integral(spec, [(1, 1), (4, 3)], 15)  # p ≥ 4 и i = 3
    assert one_red.level == "red" and (one_red.red, one_red.green) == (1, 1)
    assert integral(spec, [(5, 3)], 15).index == 1.0


def test_release_report_with_risks_and_conclusions(ready_project, llm, db):  # noqa: F811
    """Полный цикл: пункты → риски и выводы → утверждение → выпуск v1.0 без водяного знака."""
    import io
    import shutil

    import pytest
    from docx import Document

    if not shutil.which("soffice"):
        pytest.skip("нужен LibreOffice")
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    normal = agent_handler()

    def with_alternatives(purpose, user, tools, server_tools):
        out = normal(purpose, user, tools, server_tools)
        out["tables"] = [
            {
                "title": "Сравнение",
                "columns": ["Модель", "Цена"],
                "rows": [["A", "1"], ["B", "2"], ["C", "3"]],
            }
        ]
        return out

    llm.agent_handler = with_alternatives
    assert c.post(f"{base}/runs").status_code == 202
    llm.handler = risk_handler(llm)
    assert c.post(f"{base}/risks/run").status_code == 202
    c.patch(
        base,
        json={"independence": {"affiliated": False, "participated_in_docs": False, "is_supplier": False}},
    )
    for row in c.get(f"{base}/coverage").json()["rows"]:
        url = {
            "item": f"{base}/assessment/items/{row['ref']}/review",
            "risk": f"{base}/risks/{row['ref']}/review",
            "conclusions": f"{base}/conclusions/review",
        }[row["kind"]]
        assert c.put(url, json={"status": "approved"}).status_code == 200, row
    cov = c.get(f"{base}/coverage").json()
    assert cov["can_release"], cov["issues"]

    draft = c.post(f"{base}/reports")
    assert draft.status_code == 202
    rel = c.post(f"{base}/reports", json={"release": True})
    assert rel.status_code == 202, rel.text
    versions = c.get(f"{base}/reports").json()
    assert [(v["version"], v["status"]) for v in versions] == [("1.0", "released"), ("0.1", "draft")]
    r = versions[0]
    assert r["render_status"] == "ready", r["error"]
    doc = Document(io.BytesIO(c.get(f"{base}/reports/{r['id']}/download").content))
    text = "\n".join(x.text for x in doc.paragraphs)
    headings = [x.text for x in doc.paragraphs if x.style.name.startswith("Heading")]
    assert "4.1 Технические риски" in headings and "4.2 Риски по закупкам" in headings
    assert (
        "Интегральный уровень риска проекта" in text and "Рисунок 1 – Тепловая карта проектных рисков" in text
    )
    registry = next(t for t in doc.tables if t.rows[0].cells[0].text == "Риск")
    assert any(
        row.cells[0].text.startswith("T3. ") and row.cells[3].text == "Высокий" for row in registry.rows
    )
    assert doc.inline_shapes  # тепловая карта
    assert "Проект реализуем при условиях" in text  # раздел 5
    assert "ЧЕРНОВИК" not in doc.sections[0].header._element.xml  # выпуск — без водяного знака
    footer = "\n".join(x.text for x in doc.sections[0].footer.paragraphs)
    assert "Версия 1.0" in footer and "черновик" not in footer.lower()
