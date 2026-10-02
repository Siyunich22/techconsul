"""Проверка загружаемых файлов (кабинет: резюме, логотип, reference.docx)."""

from fastapi import HTTPException, UploadFile, status

from app.core.storage import safe_filename

CV_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}
LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
DOCX_TYPES = {".docx": CV_TYPES[".docx"]}


def validate_upload(file: UploadFile, allowed: dict[str, str], max_mb: int) -> tuple[str, str]:
    """Возвращает (безопасное имя файла, content-type). Тип определяется по расширению из белого списка."""
    name = safe_filename(file.filename or "")
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Допустимые форматы: {', '.join(sorted(allowed))}",
        )
    size = file.size
    if size is None:
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой")
    if size > max_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Файл больше {max_mb} МБ")
    return name, allowed[ext]
