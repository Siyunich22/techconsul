# Прогресс

**Текущая фаза: 0 — Каркас** (TZ §12)

## Фаза 0 — Каркас

Критерий приёмки: `make up` поднимает всё; `/health` = ok; страница логина открывается.

| Задача | Статус |
|---|---|
| Структура репозитория по TZ §10 | ✅ |
| docker-compose: web, api, worker, postgres(pgvector), redis, minio (+minio-init) | ✅ написан, ⏳ не запускался |
| `.env.example`, `Makefile`, `make.ps1` | ✅ |
| FastAPI: конфиг, БД, `/health` с проверкой зависимостей | ✅ unit-тесты проходят |
| Alembic: env.py, миграция 0001 (vector, pg_trgm) | ✅ написана, ⏳ не применялась |
| Celery app + задача `system.ping` | ✅ |
| Next.js 15 + TS + Tailwind v4 + shadcn/ui + next-intl + TanStack Query | ✅ написан, ⏳ не собирался |
| Страница логина (`/login`), редирект `/` → `/login` | ✅ написана |
| Playwright smoke-тест | ✅ написан, ⏳ не запускался |
| Интеграционные тесты стека (`make test-integration`) | ✅ написаны, ⏳ не запускались |

### Блокеры приёмки
На машине разработки нет Docker, Node.js и Git. Нужно установить Docker Desktop, Node.js 22 LTS и Git, затем:
```
.\make.ps1 up                 # или make up
.\make.ps1 health             # ожидается {"status":"ok",...}
.\make.ps1 test               # ruff+pytest, eslint+tsc
.\make.ps1 test-integration
# открыть http://localhost:3000 → страница «Вход»
```
После первого `npm install` закоммитить `frontend/package-lock.json`.
