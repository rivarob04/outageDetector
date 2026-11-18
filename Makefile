.PHONY: help install dev-backend dev-frontend test test-backend test-frontend lint fmt build up down logs e2e verify-catalog

COMPOSE = docker compose -f deploy/docker-compose.yml
UV = cd backend && uv

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install:  ## Install backend + frontend deps
	$(UV) sync --extra dev
	cd frontend && npm ci

dev-backend:  ## Run the API with reload on :8080 (data in ./data)
	$(UV) run env PULSE_DATA_DIR=../data PULSE_LOG_JSON=false pulse serve --reload

dev-frontend:  ## Run Vite dev server on :5173
	cd frontend && npm run dev

test: test-backend test-frontend  ## Run all tests

test-backend:
	$(UV) run pytest -q

test-frontend:
	cd frontend && npm test

lint:  ## Lint + typecheck everything
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy pulse
	cd frontend && npx eslint . && npx tsc -b --noEmit

fmt:  ## Format code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

e2e:  ## Playwright smoke test (needs built UI)
	cd frontend && npx playwright test

build:  ## Build the Docker image
	$(COMPOSE) build

up:  ## Start the stack
	$(COMPOSE) up -d --build

down:  ## Stop the stack
	$(COMPOSE) down

logs:  ## Tail logs
	$(COMPOSE) logs -f pulse

verify-catalog:  ## Re-check every catalog endpoint (needs internet)
	$(UV) run pulse catalog verify
