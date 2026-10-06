FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/models

WORKDIR /app

# OCR (rus+kaz+eng), LibreOffice headless (doc/docx/pptx → pdf, пересчёт формул xlsx; PDF отчёта — фаза 8),
# unar (rar/7z), шрифты с кириллицей.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz tesseract-ocr-eng \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        unar \
        fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Контекст сборки — корень репозитория: в образ попадают и backend, и шаблоны ТЗ (templates/).
COPY backend/pyproject.toml ./
RUN mkdir -p app workers && pip install -e ".[dev]"

COPY templates/ /templates/
COPY backend/ .

EXPOSE 8000
# «::» — IPv4 и IPv6 (приватная сеть Railway — IPv6); порт — из $PORT платформы
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
