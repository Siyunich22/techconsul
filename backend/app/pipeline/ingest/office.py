"""Офисные документы (docx, doc, rtf, odt, pptx, ppt): конвертация в PDF через LibreOffice.

PDF-версия даёт реальные номера страниц (для ссылок-источников «документ + страница») и единый
предпросмотр с подсветкой. Если LibreOffice не справился — текст docx/pptx извлекается напрямую.
"""

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.pipeline.ingest.base import IngestError, Page, Parsed, Table, clean_text
from app.pipeline.ingest.pdf import parse_pdf

log = logging.getLogger(__name__)


# Профиль LibreOffice для недоверенных файлов: сеть — через несуществующий прокси (WEBSERVICE, внешние
# ссылки и картинки по URL не дотянутся до внутренних сервисов — SSRF), обновление связей и макросы
# выключены, активное содержимое заблокировано.
_INET = "/org.openoffice.Inet/Settings"
_SCRIPTING = "/org.openoffice.Office.Common/Security/Scripting"
_PROFILE_SETTINGS = [
    (_INET, "ooInetProxyType", "1"),  # ручной прокси
    (_INET, "ooInetHTTPProxyName", "127.0.0.1"),
    (_INET, "ooInetHTTPProxyPort", "9"),  # discard — соединение не установится
    (_INET, "ooInetHTTPSProxyName", "127.0.0.1"),
    (_INET, "ooInetHTTPSProxyPort", "9"),
    (_INET, "ooInetFTPProxyName", "127.0.0.1"),
    (_INET, "ooInetFTPProxyPort", "9"),
    (_INET, "ooInetNoProxy", ""),
    (_SCRIPTING, "MacroSecurityLevel", "3"),
    (_SCRIPTING, "DisableMacrosExecution", "true"),
    (_SCRIPTING, "DisableActiveContent", "true"),
    (_SCRIPTING, "BlockUntrustedRefererLinks", "true"),
]


def _profile_xcu() -> str:
    items = "\n".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse"><value>{value}</value></prop></item>'
        for path, name, value in _PROFILE_SETTINGS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
        f"{items}\n</oor:items>\n"
    )


def _sandbox_env(profile: Path) -> dict[str, str]:
    """Окружение без секретов воркера (ключ API, пароль БД, JWT): конвертер их не получает."""
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(profile),
        "TMPDIR": str(profile),
        "LANG": "C.UTF-8",
        "SAL_USE_VCLPLUGIN": "svp",
    }


def soffice_convert(src: Path, out_dir: Path, target: str) -> Path:
    """soffice --headless --convert-to <target>.

    Отдельный профиль LibreOffice на вызов — параллельные воркеры не мешают друг другу. При превышении
    времени завершается вся группа процессов (soffice запускает дочерний soffice.bin)."""
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_profile_xcu(), encoding="utf-8")
    cmd = [
        "soffice",
        f"-env:UserInstallation=file://{profile.as_posix()}",
        "--headless",
        "--norestore",
        "--convert-to",
        target,
        "--outdir",
        str(out_dir),
        str(src),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_sandbox_env(profile),
            start_new_session=True,
        )
        try:
            _, stderr = proc.communicate(timeout=get_settings().soffice_timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc)
            raise IngestError("Конвертация LibreOffice превысила лимит времени") from exc
        if proc.returncode != 0:
            raise IngestError(f"LibreOffice: {stderr.decode(errors='replace')[:300]}")
    except FileNotFoundError as exc:
        raise IngestError("LibreOffice не установлен") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    ext = target.split(":", 1)[0]
    result = out_dir / f"{src.stem}.{ext}"
    if not result.exists():
        raise IngestError("LibreOffice не создал выходной файл")
    return result


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()


# Экспорт в PDF напрямую из OOXML иногда падает (например, на надстройках Office — webextensions);
# через формат ODF тот же документ конвертируется.
_VIA_ODF = {".docx": "odt", ".doc": "odt", ".rtf": "odt", ".pptx": "odp", ".ppt": "odp"}


def _to_pdf(path: Path, work_dir: Path) -> Path:
    try:
        return soffice_convert(path, work_dir, "pdf")
    except IngestError as exc:
        odf = _VIA_ODF.get(path.suffix.lower())
        if odf is None:
            raise
        log.info("Прямая конвертация %s в PDF не удалась (%s) — через %s", path.name, exc, odf)
        step = work_dir / "via_odf"
        step.mkdir(exist_ok=True)
        return soffice_convert(soffice_convert(path, step, odf), step, "pdf")


def parse_office(path: Path, work_dir: Path) -> Parsed:
    try:
        pdf = _to_pdf(path, work_dir)
    except IngestError as exc:
        log.warning("Конвертация %s в PDF не удалась (%s) — прямое извлечение текста", path.name, exc)
        return _direct(path)
    parsed = parse_pdf(pdf, kind="office")
    parsed.preview_pdf = pdf
    if path.suffix.lower() == ".docx":
        _attach_docx_tables(path, parsed)
    return parsed


def _direct(path: Path) -> Parsed:
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        text = clean_text("\n".join(p.text for p in d.paragraphs))
        tables = [Table(rows=[[clean_text(c.text) for c in row.cells] for row in t.rows]) for t in d.tables]
        return Parsed(
            kind="office", pages=[Page(no=1, text=text, tables=tables)], meta={"pagination": "none"}
        )
    if ext == ".pptx":
        from pptx import Presentation

        pages = []
        for i, slide in enumerate(Presentation(str(path)).slides, start=1):
            parts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame]
            pages.append(Page(no=i, text=clean_text("\n".join(parts))))
        return Parsed(kind="office", pages=pages)
    raise IngestError(f"Не удалось обработать файл {ext}: LibreOffice недоступен")


def _attach_docx_tables(path: Path, parsed: Parsed) -> None:
    """Таблицы docx точнее из самого docx, чем из PDF: привязываем к странице по тексту первой ячейки."""
    import docx

    try:
        d = docx.Document(str(path))
    except Exception:
        return
    for t in d.tables:
        rows = [[clean_text(c.text) for c in row.cells] for row in t.rows]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2:
            continue
        probe = next((c for c in rows[0] if len(c) >= 4), None)
        page = next((p for p in parsed.pages if probe and probe in p.text), None)
        if page is None:
            continue
        page.tables = [tb for tb in page.tables if tb.rows[0] != rows[0]] + [Table(rows=rows)]
