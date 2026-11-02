# Деплой на Railway (тестовый стенд)

Репозиторий: https://github.com/Siyunich22/techconsul — монорепо, все сервисы деплоятся из него.

## Сервисы

| Сервис Railway | Источник | Ресурсы |
|---|---|---|
| `postgres` | Docker image `pgvector/pgvector:pg16` + том `/var/lib/postgresql/data` | 0,5–1 ГБ RAM |
| `redis` | Database → Redis | 256 МБ |
| `minio` *(или внешний S3)* | Docker image `bitnamilegacy/minio:2025.5.24` + том `/bitnami/minio/data`, `RAILWAY_RUN_UID=0` | 512 МБ |
| `api` (на стенде — `techconsul`) | GitHub, root directory — корень репо | 1 ГБ |
| `worker` | тот же репо | 2 ГБ (OCR, LibreOffice) |
| `worker-index` | тот же репо + том `/models` | **3–4 ГБ** (модель e5-large) |
| `web` | тот же репо, root directory — **`/frontend`** | 512 МБ |

Сборка и запуск задаются **в настройках сервиса** (Settings → Build / Deploy), файлов `railway.json` в репозитории нет (D-061: Config as Code Railway отключает с 2026-12-01).

| Сервис | Dockerfile path | Watch paths | Start command | Pre-deploy | Healthcheck |
|---|---|---|---|---|---|
| `api` | `backend/Dockerfile` | `/backend/**`, `/templates/**` | — (CMD образа) | `alembic upgrade head` | `/health`, 120 с |
| `worker` | `backend/Dockerfile` | `/backend/**`, `/templates/**` | `sh -c 'celery -A workers.celery_app worker -Q ingest,default --loglevel=INFO --concurrency=${WORKER_CONCURRENCY:-2} -n ingest@%h'` | — | — |
| `worker-index` | `backend/Dockerfile` | `/backend/**` | `celery -A workers.celery_app worker -Q index --loglevel=INFO --concurrency=1 -n index@%h` | — | — |
| `web` | `Dockerfile` (от `/frontend`) | `/frontend/**` | — | — | `/login`, 120 с |

⚠ «Railpack could not determine how to build the app» в логе сборки — у сервиса не задан Dockerfile path, включился автосборщик.

`api`, `worker`, `worker-index` собираются из одного `backend/Dockerfile` (контекст — корень репо, в образ попадают шаблоны ТЗ из `templates/`). Отличаются только командой запуска.

## Порядок

1. **New Project → Deploy from GitHub repo** → `Siyunich22/techconsul`.
2. **База:** `+ New → Template → pgvector` (или Docker image `pgvector/pgvector:pg16` с томом на `/var/lib/postgresql/data` и переменными `POSTGRES_USER/PASSWORD/DB`). Расширения `vector` и `pg_trgm` и все таблицы создаст `alembic upgrade head` перед первым деплоем `api`.
3. **Redis:** `+ New → Database → Redis`.
4. **Хранилище:** сервис `minio` из Docker-образа (переменные `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, том) — либо внешний S3/R2. Бакет `api` создаст сам при старте.
5. Сервисы `api`, `worker`, `worker-index`, `web` из репозитория — настройки сборки и запуска из таблицы выше. Для `worker-index` — том на `/models` (модель 2,2 ГБ скачивается один раз).
6. Переменные (ниже) → Deploy. Публичный домен — только для `web` (Settings → Networking → Generate Domain); `api` наружу не открываем: браузер ходит в `web`, а он проксирует `/api/*` во внутреннюю сеть.
7. Администратор платформы: в сервисе `api` → **Shell** (или `railway ssh`):
   `python -m app.cli create-admin admin@example.kz '<пароль>' 'ФИО'`.
   Демо-данные (необязательно): `ADMIN_PASSWORD='<пароль>' DEMO_PASSWORD='<пароль>' python -m app.seed` — вне dev пароли по умолчанию запрещены, seed без них не запустится.
   API не стартует при `APP_ENV` ≠ `dev`, если `JWT_SECRET` короче 32 символов или равен значению по умолчанию.

## Переменные

Общие для `api`, `worker`, `worker-index` (удобно вынести в Shared Variables):

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | `${{postgres.DATABASE_URL}}` — `postgresql://` приводится к `postgresql+psycopg://` автоматически |
| `REDIS_URL` | `${{redis.REDIS_URL}}` |
| `S3_ENDPOINT_URL` | `http://${{minio.RAILWAY_PRIVATE_DOMAIN}}:9000` (или адрес внешнего S3) |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `${{minio.MINIO_ROOT_USER}}` / `${{minio.MINIO_ROOT_PASSWORD}}` |
| `S3_BUCKET` | `techocenka` |
| `JWT_SECRET` | случайная строка ≥ 32 символов |
| `COOKIE_SECURE` | `true` |
| `PUBLIC_URL` | `https://${{web.RAILWAY_PUBLIC_DOMAIN}}` |
| `APP_ENV` | `staging` |
| `ANTHROPIC_API_KEY` | ключ API (`sk-ant-…`); без него паспорт и оценка не работают, классификация документов — только правилами |
| `ANTHROPIC_WORKSPACE_ID` | `wrkspc_…` — только если ключ не привязан к workspace (ошибка «anthropic-workspace-id») |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | почтовый сервис (для сброса пароля; приглашения работают и без почты — ссылка показывается в интерфейсе) |
| `FASTEMBED_CACHE_PATH` | `/models` |

Только `api`: `PORT=8000`.
Только `worker`: `WORKER_CONCURRENCY=2` (больше — больше RAM).

`web`:

| Переменная | Значение |
|---|---|
| `API_INTERNAL_URL` | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000` — **используется при сборке** (прокси `/api/*` зашивается в сборку Next), поэтому после смены нужен redeploy |
| `PORT` | `3000` |

## Как проходит обработка на стенде

браузер → `web` (`/api/*` прокси) → `api` → файл частями в S3 → задача в Redis →
`worker` (разбор, OCR, LibreOffice, классификация) → `worker-index` (чанки, эмбеддинги → Postgres/pgvector) → статус «Проиндексирован».
Поиск: `api` отправляет текст запроса в `worker-index` (очередь `index`) — модель держит только он, `api` остаётся лёгким.

## Проверка после деплоя

- `https://<web-домен>/login` открывается; `https://<web-домен>/api/v1/health` → `{"status":"ok", ...}`.
- Регистрация → проект → вкладка «Документы» → загрузить файл → статус доходит до «Проиндексирован» (первый документ — дольше: `worker-index` скачивает модель).

## Ограничения тестового стенда

- Эмбеддинги на CPU: ~1,3–2 с на фрагмент (D-040) — большие пакеты индексируются долго.
- `bitnamilegacy/minio` заморожен (D-011) — для продакшена внешний S3.
- Готовы фазы 0–3: паспорт проекта, оценка ИИ, риски и отчёт — следующие фазы.
