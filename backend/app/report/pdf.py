"""PDF отчёта через LibreOffice и номера страниц для содержания.

LibreOffice при конвертации не обновляет поле TOC, поэтому два прохода: DOCX → PDF, по закладкам PDF
(заголовки 1–3 уровня) берутся страницы, содержание заполняется номерами, DOCX → PDF ещё раз.
Номера — только цифры в уже существующих строках, разбивка на страницы не меняется.
"""

import re
import tempfile
from pathlib import Path

import fitz

from app.pipeline.ingest.office import soffice_convert


def docx_to_pdf(docx: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="report_") as tmp:
        src = Path(tmp) / "report.docx"
        src.write_bytes(docx)
        return soffice_convert(src, Path(tmp), "pdf").read_bytes()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def heading_pages(pdf: bytes, entries: list[str]) -> dict[int, int]:
    """Номер записи содержания → страница по закладкам PDF; совпадение по тексту, по порядку документа."""
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        outline = [(_norm(title), page) for _, title, page in doc.get_toc(simple=True)]
    pages: dict[int, int] = {}
    pos = 0
    for n, text in enumerate(entries):
        want = _norm(text)
        for j in range(pos, len(outline)):
            if outline[j][0] == want:
                pages[n] = outline[j][1]
                pos = j + 1
                break
    return pages


def page_count(pdf: bytes) -> int:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count
