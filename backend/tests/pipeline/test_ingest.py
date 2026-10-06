"""Парсеры ingest. Нужны Tesseract и LibreOffice — запускаются в контейнере api (make test)."""

import email.message
import shutil
import zipfile
from pathlib import Path

import pytest

from app.pipeline.classify import classify, classify_by_rules
from app.pipeline.index import chunk_pages, estimate_tokens
from app.pipeline.ingest import IngestError, parse_file
from app.pipeline.ingest.base import Page
from app.template_engine.loader import document_categories, iter_items, parse_template, template_path
from tests.fixtures.sample_project.generate import EXPECTED_CATEGORIES

needs_tools = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("soffice")),
    reason="нужны tesseract и LibreOffice (контейнер api)",
)

CATEGORIES = document_categories(
    parse_template(template_path("tech_assessment_bank_v1").read_text(encoding="utf-8"))
)


def _parse(path: Path, tmp_path: Path, name: str | None = None):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return parse_file(path, name or path.name, work)


@needs_tools
def test_pdf_text_tables_and_pages(sample_files, tmp_path):
    p = _parse(sample_files / "ТЭО_мукомольный_завод_300тсут.pdf", tmp_path)
    assert p.kind == "pdf" and len(p.pages) == 30
    assert "320 тонн" in p.pages[1].text
    equipment = next(t for page in p.pages for t in page.tables if t.rows[0][0] == "Наименование")
    assert any("MDDK" in row[0] for row in equipment.rows)
    assert not any(page.ocr for page in p.pages)


@needs_tools
def test_scan_is_ocred(sample_files, tmp_path):
    p = _parse(sample_files / "ТУ_электроснабжение_скан.jpg", tmp_path)
    assert p.pages[0].ocr
    text = p.pages[0].text
    assert "ТЕХНИЧЕСКИЕ УСЛОВИЯ" in text and "2600" in text and "Кокшетау" in text


@needs_tools
def test_scanned_pdf_page_is_ocred(sample_files, tmp_path):
    import pymupdf

    pdf = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(sample_files / "ТУ_электроснабжение_скан.jpg"))
        doc.save(pdf)
    p = _parse(pdf, tmp_path)
    assert p.pages[0].ocr and "ТУ-2026/0417" in p.pages[0].text.replace(" ", "")


@needs_tools
def test_docx_converted_with_pages_and_preview(sample_files, tmp_path):
    p = _parse(sample_files / "Бизнес-план_Мукомольный_завод.docx", tmp_path)
    assert p.kind == "office" and p.preview_pdf and p.preview_pdf.exists()
    assert "300 тонн" in p.text
    capex = next(t for page in p.pages for t in page.tables if t.rows[0][0] == "Статья")
    assert capex.rows[-1] == ["Итого", "12 500"]


@needs_tools
def test_xlsx_formulas_become_values_with_cell_ranges(sample_files, tmp_path):
    p = _parse(sample_files / "Финмодель_мукомольный_завод.xlsx", tmp_path)
    assert p.meta["recalculated"] is True
    assert p.meta["sheets"] == ["CAPEX", "Производство", "Сырьё", "OPEX", "Cash flow"]
    capex = p.pages[0]
    assert "[7] Итого CAPEX | 12500" in capex.text
    chunks = chunk_pages(p.pages)
    first = next(c for c in chunks if c.sheet == "CAPEX")
    assert first.cell_range == "A1:B7" and "12500" in first.text


def test_zip_with_cp866_names_and_junk(tmp_path):
    archive = tmp_path / "docs.zip"

    class LegacyInfo(zipfile.ZipInfo):
        """Имя в cp866 без флага UTF-8 — так пишут архиваторы Windows."""

        def _encodeFilenameFlags(self):  # noqa: N802
            return self.filename.encode("cp866"), self.flag_bits

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(LegacyInfo("Папка/ТЭО.txt"), "технико-экономическое обоснование")
        zf.writestr("__MACOSX/._x.txt", "мусор")
        zf.writestr("../evil.txt", "path traversal")
    p = _parse(archive, tmp_path)
    assert p.is_container
    assert sorted(c.relative_path for c in p.children) == ["evil.txt", "Папка/ТЭО.txt"]
    assert all(c.path.is_relative_to(tmp_path) for c in p.children)


@needs_tools
def test_rar_or_7z_via_unar(tmp_path):
    src = tmp_path / "inner.zip"  # unar читает и zip — проверяем вызов и раскладку файлов
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("a/КП.txt", "коммерческое предложение")
    from app.pipeline.ingest.archive import parse_unar

    work = tmp_path / "w"
    work.mkdir()
    p = parse_unar(src, work)
    assert [c.relative_path for c in p.children] == ["a/КП.txt"]


def test_eml_body_and_attachment(tmp_path):
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "supplier@example.com", "client@example.kz", "КП на оборудование"
    msg.set_content("Направляем коммерческое предложение.")
    msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="КП.pdf")
    path = tmp_path / "mail.eml"
    path.write_bytes(msg.as_bytes())
    p = _parse(path, tmp_path)
    assert "КП на оборудование" in p.pages[0].text and "коммерческое предложение" in p.pages[0].text
    assert [c.name for c in p.children] == ["КП.pdf"]


def test_dxf_text_and_dwg_metadata(tmp_path):
    import ezdxf

    doc = ezdxf.new()
    doc.layers.add("ТЕХНОЛОГИЯ")
    doc.modelspace().add_text("Вальцовый станок MDDK", dxfattribs={"layer": "ТЕХНОЛОГИЯ"})
    dxf = tmp_path / "plan.dxf"
    doc.saveas(dxf)
    p = _parse(dxf, tmp_path)
    assert "Вальцовый станок MDDK" in p.text and "ТЕХНОЛОГИЯ" in p.meta["layers"]

    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(b"AC1032" + b"\x00" * 100)
    meta = _parse(dwg, tmp_path).meta
    assert meta["version"] == "AutoCAD 2018"


def test_unsupported_and_broken_files(tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(IngestError, match="не поддерживается"):
        _parse(exe, tmp_path)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(IngestError):
        _parse(bad, tmp_path)


def test_text_chunks_respect_size_and_pages():
    para = "Проектные решения соответствуют требованиям СН РК. " * 20
    pages = [Page(no=i, text="\n".join([para] * 3)) for i in range(1, 6)]
    chunks = chunk_pages(pages)
    assert len(chunks) > 3
    assert all(c.token_count <= 1300 for c in chunks)
    assert chunks[0].page == 1 and chunks[-1].page_end == 5
    assert all(c.page <= c.page_end for c in chunks)
    assert estimate_tokens("а" * 320) == 100


@needs_tools
def test_sample_project_classification_accuracy(sample_files, tmp_path):
    """Критерий приёмки фазы 2: категории верны ≥ 90% (здесь — только правила, без LLM)."""
    correct = 0
    for name, expected in EXPECTED_CATEGORIES.items():
        p = _parse(sample_files / name, tmp_path)
        got = classify_by_rules(CATEGORIES, name, "", p.text, p.meta.get("sheets")).category
        correct += got == expected
    assert correct / len(EXPECTED_CATEGORIES) >= 0.9


def test_low_confidence_goes_to_llm(llm):
    llm.answers.append(
        {"category": "экология", "confidence": 0.8, "reason": "заключение экологической экспертизы"}
    )
    result = classify(CATEGORIES, "документ.pdf", "", "Текст без явных признаков категории")
    assert (result.category, result.source) == ("экология", "llm")
    assert llm.calls[0]["role"] == "cheap" and "экология" in llm.calls[0]["system"]


def test_llm_unavailable_falls_back_to_rules(llm):
    result = classify(CATEGORIES, "scan_0001.pdf", "", "")
    assert result.category == "прочее" and result.source == "rules"


def test_llm_unknown_category_is_ignored(llm):
    llm.answers.append({"category": "выдумка", "confidence": 0.9, "reason": "?"})
    assert classify(CATEGORIES, "x.pdf", "", "").source == "rules"


def test_template_items_carry_inputs():
    tpl = parse_template(template_path("tech_assessment_bank_v1").read_text(encoding="utf-8"))
    items = {i.id: i for i in iter_items(tpl)}
    assert "3.6.1" not in items  # раздел 3.6 выключен по умолчанию
    assert "3.6.1" in {i.id for i in iter_items(tpl, ["3.6"])}
    assert items["3.4.1"].section_id == "3.4" and "кп_поставщиков" in items["3.4.1"].inputs
    assert items["3.4.8"].requires_reference == ("ndt",)


def test_highlight_png(sample_files):
    pytest.importorskip("pymupdf")
    from app.pipeline.ingest.pdf import page_png

    png = page_png(sample_files / "ТЭО_мукомольный_завод_300тсут.pdf", 2, highlight="320 тонн")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    with pytest.raises(IngestError):
        page_png(sample_files / "ТЭО_мукомольный_завод_300тсут.pdf", 99)
