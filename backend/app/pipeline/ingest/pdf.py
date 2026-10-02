"""PDF: текст постранично (PyMuPDF), таблицы (find_tables), OCR страниц-сканов."""

import io
import logging
from pathlib import Path

import pymupdf
from PIL import Image

from app.core.config import get_settings
from app.pipeline.ingest.base import IngestError, Page, Parsed, Table, clean_text
from app.pipeline.ingest.ocr import ocr_image

log = logging.getLogger(__name__)


def parse_pdf(path: Path, kind: str = "pdf") -> Parsed:
    s = get_settings()
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise IngestError(f"Не удалось открыть PDF: {exc}") from exc
    if doc.needs_pass:
        raise IngestError("PDF защищён паролем")
    pages: list[Page] = []
    with doc:
        for i, page in enumerate(doc):
            text = clean_text(page.get_text("text", sort=True))
            ocr = False
            if len(text) < s.ocr_min_text_chars and page.get_images():
                pix = page.get_pixmap(dpi=s.ocr_dpi)
                text = ocr_image(Image.open(io.BytesIO(pix.tobytes("png"))))
                ocr = True
            pages.append(Page(no=i + 1, text=text, tables=[] if ocr else _tables(page), ocr=ocr))
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    return Parsed(kind=kind, pages=pages, meta=meta)


def _tables(page) -> list[Table]:
    try:
        found = page.find_tables()
    except Exception:  # find_tables падает на экзотической разметке — таблицы не критичны, текст уже есть
        log.debug("find_tables failed", exc_info=True)
        return []
    tables = []
    for t in found.tables:
        rows = [[clean_text(c or "") for c in row] for row in t.extract()]
        rows = [r for r in rows if any(r)]
        if len(rows) >= 2:
            tables.append(Table(rows=rows))
    return tables


def page_png(path: Path, page_no: int, highlight: str | None = None, dpi: int = 110) -> bytes:
    """Рендер страницы для предпросмотра; найденные вхождения фрагмента подсвечиваются."""
    with pymupdf.open(path) as doc:
        if not 1 <= page_no <= doc.page_count:
            raise IngestError("Страница вне диапазона документа")
        page = doc[page_no - 1]
        if highlight:
            for quad in _find(page, highlight):
                annot = page.add_highlight_annot(quad)
                annot.set_colors(stroke=(1, 0.85, 0.1))
                annot.update()
        return page.get_pixmap(dpi=dpi).tobytes("png")


def _find(page, needle: str):
    needle = " ".join(needle.split())
    hits = page.search_for(needle, quads=True)
    if not hits and len(needle) > 60:  # длинная цитата могла разорваться переносом — ищем начало
        hits = page.search_for(needle[:60], quads=True)
    return hits
