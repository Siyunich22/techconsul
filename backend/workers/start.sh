#!/usr/bin/env bash
# Запуск воркеров по роли — команда запуска сервиса на Railway и в docker compose:
#   bash workers/start.sh worker  — разбор документов (ingest, default) + задачи ИИ (llm) отдельным процессом:
#                                  долгая оценка не занимает слоты загрузки документов
#   bash workers/start.sh index   — эмбеддинги: очереди query (поисковые запросы) и index в одном процессе
#                                  с потоками — модель в памяти одна, запрос не ждёт индексацию пакета
set -euo pipefail

# Платформа может запустить команду мимо ENTRYPOINT образа — сбрасываем права здесь же.
if [ "$(id -u)" = "0" ] && [ -x /usr/local/bin/docker-entrypoint.sh ]; then
    exec /usr/local/bin/docker-entrypoint.sh bash "$0" "$@"
fi

role="${1:-worker}"
celery_worker=(celery -A workers.celery_app worker --loglevel=INFO)

case "$role" in
    worker)
        pids=()
        "${celery_worker[@]}" -Q ingest,default --concurrency="${WORKER_CONCURRENCY:-2}" -n "ingest@%h" &
        pids+=($!)
        "${celery_worker[@]}" -Q llm --concurrency="${LLM_CONCURRENCY:-2}" -n "llm@%h" &
        pids+=($!)
        trap 'kill -TERM "${pids[@]}" 2>/dev/null || true' TERM INT
        # упал один процесс — останавливаем оба: платформа перезапустит контейнер целиком
        set +e
        wait -n
        code=$?
        kill -TERM "${pids[@]}" 2>/dev/null
        wait
        exit "$code"
        ;;
    index)
        exec "${celery_worker[@]}" -Q query,index --pool threads \
            --concurrency="${INDEX_CONCURRENCY:-3}" -n "index@%h"
        ;;
    *)
        echo "Неизвестная роль воркера: $role (worker | index)" >&2
        exit 2
        ;;
esac
