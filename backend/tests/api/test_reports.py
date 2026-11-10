"""Фаза 8: черновик отчёта DOCX/PDF по ГОСТ 7.32 — структура 1:1 с шаблоном, источники, оформление."""

import io
import re
import shutil
import uuid

import pytest
from docx import Document
from docx.shared import Mm
from sqlalchemy import select

from app.models import AuditLog, Project
from app.services import assessment as svc
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура

needs_soffice = pytest.mark.skipif(not shutil.which("soffice"), reason="нужен LibreOffice")


def _texts(doc) -> list[str]:
    return [p.text for p in doc.paragraphs]


@needs_soffice
def test_draft_report_docx_and_pdf(ready_project, llm, db, new_client):  # noqa: F811
    c, p = ready_project
    base = f"/api/v1/projects/{p['id']}"
    assert c.post(f"{base}/reports").status_code == 409  # оценки ещё нет
    llm.agent_handler = agent_handler(fail={"3.3.2"})
    assert c.post(f"{base}/runs").status_code == 202
    ex = c.post(
        "/api/v1/org/experts", json={"full_name": "Петров Пётр Петрович", "specialization": ["технолог"]}
    )
    c.put(f"{base}/members", json=[{"expert_id": ex.json()["id"], "role": "lead", "assigned_items": ["3.4"]}])

    created = c.post(f"{base}/reports")
    assert created.status_code == 202, created.text
    reports = c.get(f"{base}/reports").json()
    r = reports[0]
    assert r["version"] == "0.1" and r["status"] == "draft" and r["render_status"] == "ready", r["error"]
    assert r["has_docx"] and r["has_pdf"] and r["filename"].startswith("Оценка_техпроработанности_")

    resp = c.get(f"{base}/reports/{r['id']}/download", params={"fmt": "docx"})
    assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
    doc = Document(io.BytesIO(resp.content))
    text = "\n".join(_texts(doc))

    # структура 1:1 с шаблоном: все разделы, подразделы и пункты — заголовками с номерами шаблона
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    plan = svc._plan(db.get(Project, uuid.UUID(p["id"])))
    for item in plan:
        assert any(h.startswith(f"{item.id} ") for h in headings), item.id
    for sec in ("1 ", "2 ", "3 ", "4 ", "5 ", "6 "):
        assert any(h.startswith(sec) for h in headings), sec
    assert not any(h.startswith("3.6 ") for h in headings)  # необязательный раздел выключен
    assert {"ПРИЛОЖЕНИЕ А", "ПРИЛОЖЕНИЕ Б", "ПРИЛОЖЕНИЕ В"} <= {
        h for h in headings if h.startswith("ПРИЛОЖЕНИЕ")
    }

    # титульный лист, исполнители, содержание; черновик
    for marker in (
        "УТВЕРЖДАЮ",
        "ОТЧЁТ",
        "СПИСОК ИСПОЛНИТЕЛЕЙ",
        "СОДЕРЖАНИЕ",
        "Петров Пётр Петрович",
        "ЧЕРНОВИК",
    ):
        assert marker in text, marker
    header_xml = doc.sections[0].header._element.xml
    assert "PowerPlusWaterMarkObject" in header_xml and "ЧЕРНОВИК" in header_xml

    # пункт с ошибкой — явно помечен; источники — [n] и список в приложении А
    assert "Пункт не сформирован" in text
    assert "[S" not in text and re.search(r"\[\d+\]", text)
    after_a = text.rsplit("ПРИЛОЖЕНИЕ А", 1)[1]  # первое вхождение — строка содержания
    assert re.search(r"^\d+\. ТЭО_завод\.txt \(документ Заказчика\), с\. 1\.$", after_a, re.M)
    assert "дата обращения" in after_a and "https://example.org/mills" in after_a

    # таблицы: подпись «Таблица N – …» над таблицей; матрица покрытия — по всем пунктам
    captions = [t for t in _texts(doc) if t.startswith("Таблица ")]
    assert captions and all(re.match(r"Таблица \d+ – ", t) for t in captions)
    coverage = next(t for t in doc.tables if t.rows[0].cells[0].text == "Пункт")
    assert len(coverage.rows) == len(plan) + 1

    # оформление: A4, поля 30/15/20/20 мм, шрифт организации, межстрочный 1,5
    s = doc.sections[0]
    assert (round(s.left_margin / Mm(1)), round(s.right_margin / Mm(1))) == (30, 15)
    assert (round(s.top_margin / Mm(1)), round(s.bottom_margin / Mm(1))) == (20, 20)
    normal = doc.styles["Normal"]
    assert normal.font.name == "Times New Roman" and normal.font.size.pt == 12
    assert normal.paragraph_format.line_spacing == 1.5

    # содержание с номерами страниц (второй проход по закладкам PDF)
    toc = [p.text for p in doc.paragraphs if p.style.name.startswith("TOC")]
    assert len(toc) >= len(plan) and all(re.search(r"\t\d+$", t) for t in toc), toc[:5]

    pdf = c.get(f"{base}/reports/{r['id']}/download", params={"fmt": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert db.scalar(select(AuditLog).where(AuditLog.action == "report.downloaded")) is not None

    # новая сборка — новая версия, старая не перезаписывается
    assert c.post(f"{base}/reports").status_code == 202
    versions = [x["version"] for x in c.get(f"{base}/reports").json()]
    assert versions == ["0.2", "0.1"]

    # изоляция организаций
    from tests.conftest import unique_bin

    other = new_client()
    other.post(
        "/api/v1/auth/register",
        json={
            "organization": {"name": "Чужая", "bin": unique_bin()},
            "user": {
                "full_name": "X",
                "email": f"{uuid.uuid4().hex[:8]}@example.kz",
                "password": "secret-pass-1",
            },
        },
    )
    assert other.get(f"{base}/reports").status_code == 404
    assert other.get(f"{base}/reports/{r['id']}/download").status_code == 404
