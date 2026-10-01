# Архитектура

## Сервисы (docker-compose)

| Сервис | Образ / код | Назначение | Порт |
|---|---|---|---|
| `web` | `frontend/` (Next.js 15) | UI; проксирует `/api/*` → `api` | 3000 |
| `api` | `backend/` (FastAPI) | REST API `/api/v1`, `/health`; при старте — `alembic upgrade head` | 8000 |
| `worker` | `backend/` (Celery) | длинные пайплайны: ingest, анализ, сборка DOCX | — |
| `postgres` | `pgvector/pgvector:pg16` | данные + эмбеддинги (pgvector) + полнотекст (tsvector) | 5432 |
| `redis` | `redis:7-alpine` | брокер/бэкенд Celery, SSE-события прогресса | 6379 |
| `minio` | `minio/minio` | файлы проектов и версии отчётов (версионирование включено) | 9000 / 9001 |
| `minio-init` | `minio/mc` | одноразово: бакет + versioning | — |

```
Браузер ──► web:3000 ──(/api/* rewrite)──► api:8000 ──► postgres / redis / minio
                                              │
                                              └─(Celery через redis)──► worker ──► Anthropic API
```

## Backend (`backend/app`)

- `core/` — конфиг (pydantic-settings), БД (SQLAlchemy 2, naming convention для Alembic), health-проверки.
- `api/` — роутеры FastAPI.
- `models/`, `schemas/` — ORM и Pydantic-схемы.
- `services/` — бизнес-логика; все запросы фильтруются по `org_id`.
- `pipeline/` — ingest → classify → index → passport → analyze → risks → conclusions → validator → render.
- `llm/` — единственная точка вызова Anthropic (`client.py`), промпты в `prompts/*.md`, учёт стоимости.
- `template_engine/` — загрузка и проверка YAML-шаблона ТЗ банка (источник истины о структуре отчёта).
- `workers/celery_app.py` — Celery (acks_late, prefetch=1 — задачи идемпотентны).

## Тестирование

- `make test` — ruff + pytest (unit) и eslint + tsc; сервисы не нужны.
- `make test-integration` — проверка живого стека (БД+pgvector, Redis, MinIO, Celery round-trip).
- `make e2e` — Playwright против поднятого окружения.
