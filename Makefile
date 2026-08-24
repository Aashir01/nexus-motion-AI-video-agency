.DEFAULT_GOAL := help
VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install everything
	python3 -m venv $(VENV)
	$(PIP) install -q -U pip
	$(PIP) install -q -r requirements-dev.txt
	cd frontend && npm install
	@echo "\n  done — copy .env.example to .env, then: make dev\n"

dev: ## Run the API with reload (in-process worker, sqlite, no keys needed)
	$(VENV)/bin/uvicorn nexus.api.app:app --reload --port 8000

worker: ## Run a production worker
	$(PY) -m nexus.worker.main

ui: ## Run the dashboard dev server
	cd frontend && npm run dev

doctor: ## Check ffmpeg, database, queue and configured providers
	$(PY) -m nexus.cli doctor

demo: ## Produce a 2-minute episode offline, no API keys required
	$(PY) -m nexus.cli produce \
	  --brief "A harbour-town drama about Maya Rios, a fixer who has run out of favours." \
	  --premise "One night to return a favour before it stops being one." \
	  --minutes 2 --profile offline --resolution 480p

test: ## Run the fast test suite
	$(PY) -m pytest -q -m "not slow"

test-all: ## Run everything, including the end-to-end render
	$(PY) -m pytest -q

lint: ## Lint
	$(VENV)/bin/ruff check nexus tests

fmt: ## Auto-fix lint issues
	$(VENV)/bin/ruff check --fix nexus tests

migrate: ## Apply database migrations
	$(VENV)/bin/alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add x"
	$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

build: ## Build the container image (includes the dashboard and ffmpeg)
	docker build -t nexus-motion:latest .

up: build ## Start the full stack
	docker compose up -d
	@echo "\n  api      http://localhost:8000/docs"
	@echo "  studio   http://localhost:8000/app\n"

down: ## Stop the stack
	docker compose down

logs: ## Tail the stack
	docker compose logs -f api worker

clean: ## Remove local artefacts
	rm -rf storage/ nexus.db .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help setup dev worker ui doctor demo test test-all lint fmt migrate migration build up down logs clean
