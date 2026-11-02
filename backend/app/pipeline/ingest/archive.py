"""Архивы: zip (с исправлением кириллических имён cp866), rar и 7z (unar). Защита от zip-бомб."""

import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from app.core.config import get_settings
from app.pipeline.ingest.base import Child, IngestError, Parsed

SKIP_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


def _zip_name(info: zipfile.ZipInfo) -> str:
    """Windows-архиваторы пишут имена в cp866 без флага UTF-8; zipfile читает их как cp437."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _safe_rel(name: str) -> str | None:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    if not parts or any(p in SKIP_NAMES or p.startswith("._") for p in parts):
        return None
    return "/".join(parts)


def parse_zip(path: Path, out_dir: Path) -> Parsed:
    s = get_settings()
    limit = s.max_archive_unpacked_gb * 1024**3
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestError("Повреждённый zip-архив") from exc
    children: list[Child] = []
    total = 0
    with zf:
        entries = [i for i in zf.infolist() if not i.is_dir()]
        if len(entries) > s.max_archive_entries:
            raise IngestError(f"В архиве больше {s.max_archive_entries} файлов")
        for info in entries:
            if info.flag_bits & 0x1:
                raise IngestError("Архив защищён паролем")
            rel = _safe_rel(_zip_name(info))
            if rel is None:
                continue
            total += info.file_size
            if total > limit:
                raise IngestError(f"Распакованный размер архива больше {s.max_archive_unpacked_gb} ГБ")
            target = out_dir / f"{len(children):05d}_{PurePosixPath(rel).name}"
            with zf.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            children.append(Child(name=PurePosixPath(rel).name, relative_path=rel, path=target))
    return Parsed(kind="archive", children=children, meta={"entries": len(children)})


def _check_unar_listing(path: Path) -> None:
    """Оглавление до распаковки (lsar): ссылки запрещены, число файлов и объём — по заявленным размерам.

    Проверка после распаковки опаздывает: бомба успевает заполнить диск, а ссылка «d → /» с
    последующим «d/…» пишет за пределы каталога распаковки.
    """
    s = get_settings()
    try:
        out = subprocess.run(["lsar", "-j", str(path)], capture_output=True, timeout=300, check=True).stdout
        entries = json.loads(out).get("lsarContents", [])
    except FileNotFoundError as exc:
        raise IngestError("unar не установлен") from exc
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise IngestError("Не удалось прочитать оглавление архива") from exc
    if any(e.get("XADIsLink") or e.get("XADIsHardLink") for e in entries):
        raise IngestError("Архив содержит символические ссылки — распаковка запрещена")
    if len(entries) > s.max_archive_entries:
        raise IngestError(f"В архиве больше {s.max_archive_entries} файлов")
    if sum(int(e.get("XADFileSize") or 0) for e in entries) > s.max_archive_unpacked_gb * 1024**3:
        raise IngestError(f"Распакованный размер архива больше {s.max_archive_unpacked_gb} ГБ")


def parse_unar(path: Path, out_dir: Path) -> Parsed:
    """rar, 7z и прочие форматы через unar (свободная реализация, читает RAR)."""
    s = get_settings()
    _check_unar_listing(path)
    dest = out_dir / "unar"
    dest.mkdir()
    try:
        subprocess.run(
            ["unar", "-q", "-no-directory", "-o", str(dest), "-p", "", str(path)],
            capture_output=True,
            timeout=900,
            check=True,
        )
    except FileNotFoundError as exc:
        raise IngestError("unar не установлен") from exc
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.decode(errors="replace") + exc.stdout.decode(errors="replace")
        if "password" in msg.lower():
            raise IngestError("Архив защищён паролем") from exc
        raise IngestError(f"Не удалось распаковать архив: {msg[:300]}") from exc
    # Симлинки из архива (rar/7z их восстанавливают) не читаем: ссылка на /proc/self/environ или
    # файлы воркера выдала бы секреты как «вложенный документ». Берём только обычные файлы внутри dest.
    root = dest.resolve()
    files = [
        p
        for p in sorted(dest.rglob("*"))
        if not p.is_symlink() and p.is_file() and p.resolve().is_relative_to(root)
    ]
    if len(files) > s.max_archive_entries:
        raise IngestError(f"В архиве больше {s.max_archive_entries} файлов")
    if sum(f.stat().st_size for f in files) > s.max_archive_unpacked_gb * 1024**3:
        raise IngestError(f"Распакованный размер архива больше {s.max_archive_unpacked_gb} ГБ")
    children = []
    for f in files:
        rel = _safe_rel(f.relative_to(dest).as_posix())
        if rel:
            children.append(Child(name=f.name, relative_path=rel, path=f))
    return Parsed(kind="archive", children=children, meta={"entries": len(children)})
