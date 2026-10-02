"""Диспетчер форматов: расширение → парсер."""

from pathlib import Path

from app.pipeline.ingest.base import IngestError, Page, Parsed, clean_text

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".dwg": "image/vnd.dwg",
    ".dxf": "image/vnd.dxf",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
    ".txt": "text/plain",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
}

SUPPORTED = set(MIME)


def mime_for(filename: str) -> str:
    return MIME.get(Path(filename).suffix.lower(), "application/octet-stream")


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED


def parse_file(path: Path, filename: str, work_dir: Path) -> Parsed:
    """Разбор файла. work_dir — временный каталог для конвертаций и распакованных вложений."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from app.pipeline.ingest.pdf import parse_pdf

        return parse_pdf(path)
    if ext in (".docx", ".doc", ".rtf", ".odt", ".pptx", ".ppt"):
        from app.pipeline.ingest.office import parse_office

        return parse_office(path, work_dir)
    if ext in (".xlsx", ".xlsm"):
        from app.pipeline.ingest.xlsx import parse_xlsx

        return parse_xlsx(path, work_dir)
    if ext == ".xls":
        from app.pipeline.ingest.xlsx import parse_xls

        return parse_xls(path)
    if ext == ".csv":
        from app.pipeline.ingest.xlsx import parse_csv

        return parse_csv(path)
    if ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"):
        from app.pipeline.ingest.ocr import parse_image

        return parse_image(path)
    if ext == ".dxf":
        from app.pipeline.ingest.cad import parse_dxf

        return parse_dxf(path, work_dir)
    if ext == ".dwg":
        from app.pipeline.ingest.cad import parse_dwg

        return parse_dwg(path)
    if ext == ".eml":
        from app.pipeline.ingest.email_msg import parse_eml

        return parse_eml(path, work_dir)
    if ext == ".msg":
        from app.pipeline.ingest.email_msg import parse_msg

        return parse_msg(path, work_dir)
    if ext == ".zip":
        from app.pipeline.ingest.archive import parse_zip

        return parse_zip(path, work_dir)
    if ext in (".rar", ".7z"):
        from app.pipeline.ingest.archive import parse_unar

        return parse_unar(path, work_dir)
    if ext == ".txt":
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return Parsed(kind="text", pages=[Page(no=1, text=clean_text(raw.decode(enc)))])
            except UnicodeDecodeError:
                continue
        return Parsed(kind="text", pages=[Page(no=1, text=clean_text(raw.decode("latin-1")))])
    raise IngestError(f"Формат {ext or 'без расширения'} не поддерживается")


__all__ = ["IngestError", "Parsed", "is_supported", "mime_for", "parse_file"]
