"""Общие типы ingest: результат разбора файла — страницы с текстом и таблицами или дочерние файлы."""

from dataclasses import dataclass, field
from pathlib import Path


class IngestError(Exception):
    """Ошибка разбора с понятной пользователю причиной (показывается в списке документов)."""


@dataclass
class Table:
    rows: list[list[str]]
    title: str | None = None
    cell_range: str | None = None  # для xlsx — адрес диапазона (источник)

    def as_dict(self) -> dict:
        return {"title": self.title, "cell_range": self.cell_range, "rows": self.rows}


@dataclass
class Page:
    no: int  # с 1
    text: str
    tables: list[Table] = field(default_factory=list)
    sheet: str | None = None
    ocr: bool = False
    # для листов xlsx: (номер строки Excel, текст строки) — чанки получают адреса ячеек
    rows: list[tuple[int, str]] | None = None
    max_col: str | None = None


@dataclass
class Child:
    """Файл внутри архива или вложение письма — станет отдельным документом."""

    name: str
    relative_path: str
    path: Path


@dataclass
class Parsed:
    kind: str
    pages: list[Page] = field(default_factory=list)
    children: list[Child] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    preview_pdf: Path | None = None  # PDF-версия офисного файла (для предпросмотра с подсветкой)
    preview_png: Path | None = None  # превью чертежа

    @property
    def is_container(self) -> bool:
        return bool(self.children) and not self.pages

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


def clean_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\x00", "").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


MAX_TEXT_BYTES = 50 * 1024**2  # текст/CSV: дальше — не читаем (файл в 500 МБ занял бы гигабайты памяти)


def read_text_prefix(path: Path) -> tuple[bytes, bool]:
    """Начало текстового файла до MAX_TEXT_BYTES по границе строки; второй элемент — файл обрезан."""
    with open(path, "rb") as f:
        raw = f.read(MAX_TEXT_BYTES + 1)
    if len(raw) <= MAX_TEXT_BYTES:
        return raw, False
    return raw[: raw.rfind(b"\n", 0, MAX_TEXT_BYTES) + 1 or MAX_TEXT_BYTES], True


TRUNCATED_WARNING = f"Файл больше {MAX_TEXT_BYTES // 1024**2} МБ — обработано только начало"
