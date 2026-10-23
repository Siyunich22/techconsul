import os
import re
import shutil

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog
from tests.conftest import upload_file
from tests.fixtures.sample_project.generate import EXPECTED_CATEGORIES

BP = "Бизнес-план_завод.txt"
TEO = "ТЭО_завод.txt"
BP_TEXT = "Бизнес-план. Производственная мощность завода — 300 тонн в сутки. CAPEX 12 500 млн тенге."
TEO_TEXT = "Технико-экономическое обоснование. Производственная мощность — 320 тонн в сутки."


def _labels(user: str) -> dict[str, str]:
    """Метка страницы → имя файла из текста пакета, как его видит модель."""
    return {name: label for label, name in re.findall(r"\[(P\d+)\] «(.+?)»", user)}


def _fact(value, unit, refs, quote=""):
    return {"value": value, "unit": unit, "refs": refs, "quote": quote, "confidence": 0.9}


def passport_handler(labels: dict[str, str]):
    """Мок модели: map видит один документ и возвращает его мощность; reduce сводит и находит расхождение."""

    def handler(purpose, user, schema):
        assert purpose == "passport_extract"
        if schema.__name__ == "PassportData":
            found = _labels(user)
            labels.update(found)
            if BP in found:
                return {
                    "technology": {"capacity": _fact("300", "т/сут", [found[BP]], "300 тонн в сутки")},
                    "budget": {"capex_total": _fact("12 500", "млн тенге", [found[BP]])},
                }
            return {"technology": {"capacity": _fact("320", "т/сут", [found[TEO]], "320 тонн в сутки")}}
        bp, teo = labels[BP], labels[TEO]
        return {
            "passport": {
                "technology": {
                    "capacity": _fact("320", "т/сут", [teo]),
                    "product": _fact("мука", "", ["P999"]),  # выдуманный источник — должен отброситься
                },
                "budget": {
                    "capex_total": _fact("12 500", "млн тенге", [bp]),
                    "capex_items": [
                        {"item": "Оборудование", "amount": "6 950", "refs": [bp], "confidence": 0.8},
                        {"item": "Без источника", "amount": "1", "refs": [], "confidence": 0.8},
                    ],
                },
            },
            "discrepancies": [
                {
                    "field": "technology.capacity",
                    "title": "Мощность завода",
                    "description": "Бизнес-план — 300 т/сут, ТЭО — 320 т/сут.",
                    "severity": "high",
                    "values": [{"value": "300 т/сут", "refs": [bp]}, {"value": "320 т/сут", "refs": [teo]}],
                }
            ],
        }

    return handler


@pytest.fixture
def two_docs(project, monkeypatch):
    c, p = project
    upload_file(c, p["id"], BP, BP_TEXT.encode())
    upload_file(c, p["id"], TEO, TEO_TEXT.encode())
    monkeypatch.setattr(get_settings(), "passport_batch_chars", 150)  # по пакету на документ → map + reduce
    return c, p


def test_extract_passport_with_sources_and_discrepancies(two_docs, llm, db):
    c, p = two_docs
    labels: dict[str, str] = {}
    llm.handler = passport_handler(labels)
    base = f"/api/v1/projects/{p['id']}/passport"
    assert c.get(base).json()["status"] == "empty"

    r = c.post(f"{base}/extract")
    assert r.status_code == 202, r.text
    passport = c.get(base).json()
    assert passport["status"] == "draft", passport
    assert [x["schema"] for x in llm.calls] == ["PassportData", "PassportData", "ReducedPassport"]

    tech = passport["data"]["technology"]
    assert tech["capacity"]["value"] == "320" and tech["capacity"]["refs"] == [labels[TEO]]
    assert tech["product"] is None  # источник P999 не существует — поле отброшено
    rows = passport["data"]["budget"]["capex_items"]
    assert [r["item"] for r in rows] == ["Оборудование"]  # строка без источника отброшена

    src = passport["sources"][labels[TEO]]
    assert src["filename"] == TEO and src["page"] == 1
    assert set(passport["sources"]) == {labels[BP], labels[TEO]}

    (d,) = passport["discrepancies"]
    assert d["field"] == "technology.capacity" and len(d["values"]) == 2
    assert passport["progress"] == {"stage": "reduce", "done": 1, "total": 1}
    assert db.scalar(select(AuditLog).where(AuditLog.action == "passport.extract_started")) is not None


def test_single_batch_skips_reduce(project, llm, monkeypatch):
    c, p = project
    upload_file(c, p["id"], BP, BP_TEXT.encode())
    llm.handler = passport_handler({})
    c.post(f"/api/v1/projects/{p['id']}/passport/extract")
    passport = c.get(f"/api/v1/projects/{p['id']}/passport").json()
    assert passport["status"] == "draft"
    assert [x["schema"] for x in llm.calls] == ["PassportData"]
    assert passport["data"]["technology"]["capacity"]["value"] == "300"
    assert passport["discrepancies"] == []


def test_edit_and_confirm(two_docs, llm):
    c, p = two_docs
    llm.handler = passport_handler({})
    base = f"/api/v1/projects/{p['id']}/passport"
    c.post(f"{base}/extract")
    passport = c.get(base).json()
    data, discrepancies = passport["data"], passport["discrepancies"]

    # значение без источника — только если его ввёл эксперт
    data["site"]["address"] = {"value": "г. Костанай", "refs": [], "confidence": 1}
    r = c.put(base, json={"data": data, "discrepancies": discrepancies})
    assert r.status_code == 422 and "site.address" in r.json()["detail"]
    data["site"]["address"] = {"value": "г. Костанай", "refs": ["P404"], "confidence": 1, "manual": True}
    assert c.put(base, json={"data": data, "discrepancies": discrepancies}).status_code == 422
    data["site"]["address"] = {"value": "г. Костанай", "refs": [], "confidence": 1, "manual": True}
    discrepancies[0]["resolution"] = "Принято 320 т/сут по ТЭО (с резервом)"
    r = c.put(base, json={"data": data, "discrepancies": discrepancies})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["site"]["address"]["manual"] is True
    assert r.json()["discrepancies"][0]["resolution"].startswith("Принято")

    r = c.put(f"{base}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["confirmed_by"] == "Иванов Иван" and r.json()["confirmed_at"]

    # правка после подтверждения снимает подтверждение
    r = c.put(base, json={"data": data, "discrepancies": discrepancies})
    assert r.json()["status"] == "draft" and r.json()["confirmed_at"] is None


def test_extract_preconditions_and_failures(project, llm):
    c, p = project
    base = f"/api/v1/projects/{p['id']}/passport"
    assert c.post(f"{base}/extract").status_code == 409  # нет документов
    assert c.put(f"{base}/confirm").status_code == 409  # нечего подтверждать

    upload_file(c, p["id"], BP, BP_TEXT.encode())
    r = c.post(f"{base}/extract")  # FakeLLM без ответов = ИИ недоступен
    assert r.status_code == 202
    passport = c.get(base).json()
    assert passport["status"] == "failed" and "ИИ недоступен" in passport["error"]

    llm.handler = passport_handler({})
    c.post(f"{base}/extract")
    assert c.get(base).json()["status"] == "draft"  # повторный запуск после сбоя


def test_passport_isolation(two_docs, register, llm):
    c, p = two_docs
    llm.handler = passport_handler({})
    c.post(f"/api/v1/projects/{p['id']}/passport/extract")
    other = register("ТОО Чужая")
    base = f"/api/v1/projects/{p['id']}/passport"
    assert other.get(base).status_code == 404
    assert other.post(f"{base}/extract").status_code == 404
    assert other.put(f"{base}/confirm").status_code == 404


def test_layout(project):
    c, _ = project
    sections = {s["key"]: s for s in c.get("/api/v1/passport/layout").json()}
    assert list(sections)[:2] == ["site", "technology"] and len(sections) == 9
    equipment = sections["equipment"]["tables"][0]
    assert equipment["title"] == "Перечень оборудования"
    assert {"name", "manufacturer", "price_source"} <= {col["key"] for col in equipment["columns"]}
    assert "refs" not in {col["key"] for col in equipment["columns"]}
    assert {f["key"] for f in sections["technology"]["fields"]} >= {"capacity", "product"}


needs_tools = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("soffice")),
    reason="нужны tesseract и LibreOffice (контейнер api)",
)


@pytest.mark.live
@needs_tools
def test_live_sample_project_passport(project, sample_files):
    """Критерий приёмки фазы 4 на реальной модели: паспорт sample_project заполнен, у полей — источники
    с номерами страниц, расхождение мощности БП/ТЭО выявлено."""
    from app.llm.client import set_llm_override

    assert os.getenv("ANTHROPIC_API_KEY") or get_settings().anthropic_api_key, "нужен ANTHROPIC_API_KEY"
    set_llm_override(None)  # реальный клиент
    c, p = project
    for name in EXPECTED_CATEGORIES:
        upload_file(c, p["id"], name, (sample_files / name).read_bytes())
    c.post(f"/api/v1/projects/{p['id']}/passport/extract")
    passport = c.get(f"/api/v1/projects/{p['id']}/passport").json()
    assert passport["status"] == "draft", passport["error"]

    tech, budget = passport["data"]["technology"], passport["data"]["budget"]
    assert tech["capacity"] and budget["capex_total"]
    assert len(passport["data"]["equipment"]["items"]) >= 3
    for fact in (tech["capacity"], budget["capex_total"]):
        assert fact["refs"] and all(passport["sources"][r]["page"] for r in fact["refs"])
    assert any("capacity" in d["field"] for d in passport["discrepancies"]), passport["discrepancies"]
