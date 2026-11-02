#!/bin/sh
# Процессы контейнера — от непривилегированного пользователя app: разбор недоверенных файлов
# (LibreOffice, tesseract, unar) не получает root. Тома платформы (Railway, docker) монтируются
# от root — перед сбросом прав отдаём пользователю каталог моделей.
set -e
if [ "$(id -u)" = "0" ]; then
    models="${FASTEMBED_CACHE_PATH:-/models}"
    if [ -d "$models" ]; then
        chown -R app:app "$models" 2>/dev/null || true
    fi
    exec setpriv --reuid=app --regid=app --init-groups --inh-caps=-all --bounding-set=-all "$@"
fi
exec "$@"
