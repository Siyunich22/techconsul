# Прогресс

**Текущая фаза: 1 — Кабинет и портфель** (TZ §12) — не начата

## Фаза 0 — Каркас ✅ принята 2026-10-02

Критерий приёмки: `make up` поднимает всё; `/health` = ok; страница логина открывается.

| Проверка | Результат |
|---|---|
| `make up` (docker compose up --build --wait) | ✅ postgres, redis, minio, api, worker, web — healthy; minio-init — exit 0 (бакет + versioning) |
| `GET :8000/health` | ✅ `{"status":"ok","checks":{"database":"ok","redis":"ok","storage":"ok"}}` |
| `GET :3000/api/v1/health` (прокси Next → FastAPI) | ✅ 200 ok |
| `GET :3000/login` | ✅ 200, форма «Вход» |
| `alembic current` | ✅ `0001 (head)` — pgvector, pg_trgm |
| `make test` — backend (ruff + pytest) | ✅ 6 passed |
| `make test` — frontend (eslint + next typegen + tsc) | ✅ |
| `make test-integration` (БД+pgvector, Redis, MinIO, Celery round-trip) | ✅ 4 passed |
| `make e2e` (Playwright, Chromium) | ✅ 2 passed |

### Окружение разработки
Python 3.12, Node.js 24.19, Git 2.55, WSL2, Docker Desktop (server 29.8.1), Chromium для Playwright.
На Windows вместо `make` — `.\make.ps1 <цель>`.

### Отклонения от ТЗ
- Локальный MinIO — `bitnamilegacy/minio` (официальные образы сняты), см. D-011.
