# ТехОценка — команды разработки. На Windows без make: .\make.ps1 <цель>
COMPOSE ?= docker compose
# backend-тестам нужен Postgres (тестовая БД <db>_test создаётся автоматически)
API_RUN = $(COMPOSE) run --rm api
WEB_RUN = $(COMPOSE) run --rm --no-deps web

.PHONY: help env up down logs ps migrate seed test test-backend test-frontend test-integration e2e lint fmt health

help:
	@echo "up | down | logs | ps | migrate | seed | test | test-integration | e2e | lint | fmt | health"

env:
	@test -f .env || cp .env.example .env

up: env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) exec api alembic upgrade head

seed:
	$(COMPOSE) exec api python -m app.seed

# CI: backend (ruff + pytest на тестовой БД) + frontend (lint + typecheck).
test: env test-backend test-frontend

test-backend:
	$(API_RUN) sh -c "ruff check . && pytest"

test-frontend:
	$(WEB_RUN) sh -c "npm run lint && npm run typecheck"

# Проверка живого окружения: Postgres+pgvector, Redis, MinIO, Celery.
test-integration:
	$(COMPOSE) exec -e RUN_INTEGRATION=1 api pytest tests/integration

e2e:
	cd frontend && npx playwright test

lint:
	$(API_RUN) ruff check .
	$(WEB_RUN) npm run lint

fmt:
	$(API_RUN) sh -c "ruff format . && ruff check --fix ."

health:
	curl -fsS http://localhost:8000/health
