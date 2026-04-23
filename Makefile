PNPM ?= pnpm
PYTHON ?= python3
VENV ?= .venv
ACTIVATE = . $(VENV)/bin/activate

.PHONY: help install-web install-server dev-web dev-api dev-worker dev-beat up down test-api lint-web typecheck-web

help:
	@echo "Available targets:"
	@echo "  install-web      Install frontend dependencies"
	@echo "  install-server   Create virtualenv and install backend dependencies"
	@echo "  dev-web          Run Next.js frontend"
	@echo "  dev-api          Run FastAPI application"
	@echo "  dev-worker       Run Celery worker"
	@echo "  dev-beat         Run Celery Beat"
	@echo "  up               Start local infrastructure with Docker Compose"
	@echo "  down             Stop local infrastructure"
	@echo "  test-api         Run backend tests"
	@echo "  lint-web         Run frontend lint"
	@echo "  typecheck-web    Run frontend typecheck"

install-web:
	cd web && $(PNPM) install

install-server:
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) && pip install --upgrade pip && pip install -r ./server/requirements-dev.txt

dev-web:
	cd web && $(PNPM) dev

dev-api:
	$(ACTIVATE) && uvicorn app.main:create_app --factory --reload --app-dir server

dev-worker:
	$(ACTIVATE) && celery -A app.core.celery_app.celery_app worker --workdir server --loglevel=info

dev-beat:
	$(ACTIVATE) && celery -A app.core.celery_app.celery_app beat --workdir server --loglevel=info

up:
	docker compose up -d

down:
	docker compose down

test-api:
	$(ACTIVATE) && pytest server/tests -q

lint-web:
	cd web && $(PNPM) lint

typecheck-web:
	cd web && $(PNPM) typecheck
