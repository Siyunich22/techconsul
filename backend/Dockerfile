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

# Непривилегированный пользователь: процессы стартуют от root только чтобы отдать ему тома (entrypoint).
RUN useradd --create-home --uid 10001 --user-group app && mkdir -p /models && chown app:app /models

# Контекст сборки — корень репозитория: в образ попадают и backend, и шаблоны ТЗ (templates/).
# Тестовые зависимости — только в локальной сборке (docker compose передаёт INSTALL_DEV=1).
ARG INSTALL_DEV=0
COPY backend/pyproject.toml ./
RUN mkdir -p app workers     && if [ "$INSTALL_DEV" = "1" ]; then pip install -e ".[dev]"; else pip install -e .; fi

COPY templates/ /templates/
COPY backend/ .
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 8000
# Пустой host — все интерфейсы IPv4 и IPv6: приватная сеть Railway — IPv6, healthcheck и прокси — IPv4.
# («::» в uvicorn слушает только IPv6 — asyncio ставит IPV6_V6ONLY.) Порт — из $PORT платформы.
# keep-alive дольше, чем у прокси Next.js: иначе uvicorn закрывает соединение (5 с по умолчанию) в момент,
# когда прокси отправляет в него следующий запрос, — ECONNRESET у пользователя.
CMD ["sh", "-c", "uvicorn app.main:app --host '' --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
