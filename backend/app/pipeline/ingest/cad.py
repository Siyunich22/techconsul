"""Чертежи: DXF — текст надписей, слои, PNG-превью (ezdxf); DWG — только метаданные (TZ §4.5 А)."""

import logging
from pathlib import Path

from app.pipeline.ingest.base import IngestError, Page, Parsed, clean_text

log = logging.getLogger(__name__)

DWG_VERSIONS = {
    "AC1009": "AutoCAD R11/R12",
    "AC1012": "AutoCAD R13",
    "AC1014": "AutoCAD R14",
    "AC1015": "AutoCAD 2000",
    "AC1018": "AutoCAD 2004",
    "AC1021": "AutoCAD 2007",
    "AC1024": "AutoCAD 2010",
    "AC1027": "AutoCAD 2013",
    "AC1032": "AutoCAD 2018",
}


def parse_dwg(path: Path) -> Parsed:
    code = path.read_bytes()[:6].decode("ascii", errors="replace")
    meta = {
        "format": "DWG",
        "version_code": code,
        "version": DWG_VERSIONS.get(code, "неизвестная версия"),
        "note": "DWG: извлекаются только метаданные; для анализа приложите PDF или DXF чертежа",
    }
    text = f"Чертёж DWG «{path.name}». Версия формата: {meta['version']} ({code})."
    return Parsed(kind="cad", pages=[Page(no=1, text=text)], meta=meta)


def parse_dxf(path: Path, work_dir: Path) -> Parsed:
    import ezdxf
    from ezdxf import recover

    try:
        doc, _auditor = recover.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as exc:
        raise IngestError(f"Не удалось прочитать DXF: {exc}") from exc
    msp = doc.modelspace()
    texts: list[str] = []
    for e in msp.query("TEXT MTEXT ATTRIB INSERT DIMENSION"):
        kind = e.dxftype()
        if kind == "TEXT":
            texts.append(e.dxf.text)
        elif kind == "MTEXT":
            texts.append(e.plain_text())
        elif kind == "INSERT":
            texts.extend(a.dxf.text for a in e.attribs)
        elif kind == "DIMENSION" and e.dxf.get("text"):
            texts.append(e.dxf.text)
    layers = sorted(layer.dxf.name for layer in doc.layers)
    body = "\n".join(t for t in (clean_text(x) for x in texts) if t)
    text = f"Чертёж DXF «{path.name}». Слои: {', '.join(layers)}.\n\nНадписи:\n{body}"
    parsed = Parsed(
        kind="cad",
        pages=[Page(no=1, text=clean_text(text))],
        meta={"format": "DXF", "dxfversion": doc.dxfversion, "layers": layers},
    )
    parsed.preview_png = _render_png(doc, work_dir / f"{path.stem}.png")
    return parsed


def _render_png(doc, target: Path) -> Path | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        fig = plt.figure(figsize=(12, 8))
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
        fig.savefig(target, dpi=100, facecolor="white")
        plt.close(fig)
        return target
    except Exception:  # превью — не критично
        log.warning("Не удалось построить превью DXF", exc_info=True)
        return None
