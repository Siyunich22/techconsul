"""OCR через Tesseract (rus+kaz+eng) для сканов и изображений."""

from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.pipeline.ingest.base import Page, Parsed, clean_text


def ocr_image(img: Image.Image) -> str:
    img = ImageOps.exif_transpose(img).convert("L")
    # мелкие сканы распознаются хуже — увеличиваем до ~2000 px по длинной стороне
    longest = max(img.size)
    if longest < 1800:
        scale = 2000 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    return clean_text(pytesseract.image_to_string(img, lang=get_settings().ocr_languages, config="--psm 3"))


def parse_image(path: Path) -> Parsed:
    with Image.open(path) as img:
        frames = getattr(img, "n_frames", 1)  # многостраничный TIFF
        pages = []
        for i in range(frames):
            img.seek(i)
            pages.append(Page(no=i + 1, text=ocr_image(img.copy()), ocr=True))
        meta = {"width": img.width, "height": img.height, "format": img.format}
    return Parsed(kind="image", pages=pages, meta=meta)
