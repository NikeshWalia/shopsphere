# ShopSphere - developer and CI entry points.
#
#   make help          list every target
#   make up            run the whole stack in Docker
#   make test          run everything
#
# Every command in the README maps to a target here, so there is exactly one
# definition of "how to run the tests" shared by developers and by CI.

.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON  ?= python
VENV    ?= .venv
ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
else
  BIN := $(VENV)/bin
endif
PY      := $(BIN)/python
PYTEST  := $(PY) -m pytest
COMPOSE ?= docker compose

#: Browser workers. See the comment on `test-parallel` for why this is capped.
UI_WORKERS ?= 4

ALLURE_RESULTS := allure-results
ALLURE_REPORT  := allure-report

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: venv
venv: ## Create the virtualenv
	$(PYTHON) -m venv $(VENV)

.PHONY: install
install: ## Install application, test and dev dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements.txt
	$(PY) -m pip install -r requirements-test.txt
	$(PY) -m pip install -r requirements-dev.txt

.PHONY: install-ui
install-ui: ## Install Playwright and its browsers
	$(PY) -m pip install -r requirements-ui.txt
	$(PY) -m playwright install --with-deps chromium firefox webkit

.PHONY: install-perf
install-perf: ## Install Locust
	$(PY) -m pip install -r requirements-perf.txt

.PHONY: install-all
install-all: install install-ui install-perf ## Install everything

.PHONY: env
env: ## Create .env from the template (never overwrites an existing one)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	@test -f .env && echo ".env is present"

.PHONY: frontend-install
frontend-install: ## Install frontend dependencies
	cd frontend && npm ci --no-audit --no-fund

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
.PHONY: up
up: ## Build and start the whole stack
	$(COMPOSE) up --build -d
	@echo "Waiting for the API to become ready..."
	@$(PY) scripts/wait_for_stack.py
	@echo ""
	@echo "  Storefront   http://localhost:3000"
	@echo "  API docs     http://localhost:8000/docs"
	@echo "  Payment mock http://localhost:9100/docs"

.PHONY: down
down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete the database volume
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail all service logs
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: rebuild
rebuild: ## Rebuild images from scratch
	$(COMPOSE) build --no-cache

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply database migrations
	cd backend && PYTHONPATH=. ../$(PY) -m alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration:  make migration m="add wishlist"
	cd backend && PYTHONPATH=. ../$(PY) -m alembic revision --autogenerate -m "$(m)"

.PHONY: downgrade
downgrade: ## Roll back one migration
	cd backend && PYTHONPATH=. ../$(PY) -m alembic downgrade -1

.PHONY: seed
seed: ## Seed the database (idempotent)
	cd backend && PYTHONPATH=. ../$(PY) -m app.seed.seed

.PHONY: reseed
reseed: ## Wipe and reseed the database
	cd backend && PYTHONPATH=. ../$(PY) -m app.seed.seed --reset

.PHONY: db-summary
db-summary: ## Print current row counts
	cd backend && PYTHONPATH=. ../$(PY) -m app.seed.seed --summary

# ---------------------------------------------------------------------------
# Running services without Docker
# ---------------------------------------------------------------------------
.PHONY: backend
backend: ## Run the API with autoreload
	cd backend && PYTHONPATH=. ../$(PY) -m uvicorn app.main:app --reload --port 8000

.PHONY: payment-mock
payment-mock: ## Run the mock payment provider
	cd payment-mock && ../$(PY) -m uvicorn app.main:app --reload --port 9100

.PHONY: frontend
frontend: ## Run the Vite dev server
	cd frontend && npm run dev

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
.PHONY: test
test: ## Run every suite except performance
	$(PYTEST)

.PHONY: test-unit
test-unit: ## Unit tests (no running stack required)
	$(PYTEST) backend/tests -v

.PHONY: test-api
test-api: ## API tests
	$(PYTEST) tests/api -v

.PHONY: test-db
test-db: ## Database tests
	$(PYTEST) tests/database -v

.PHONY: test-integration
test-integration: ## Integration tests
	$(PYTEST) tests/integration -v

.PHONY: test-contract
test-contract: ## Contract tests
	$(PYTEST) tests/contract -v

.PHONY: test-security
test-security: ## Security tests
	$(PYTEST) tests/security -v

.PHONY: test-ui
test-ui: ## UI tests (Chromium)
	$(PYTEST) tests/ui -v

.PHONY: test-ui-all-browsers
test-ui-all-browsers: ## UI tests on Chromium, Firefox and WebKit
	BROWSER=chromium $(PYTEST) tests/ui -q
	BROWSER=firefox  $(PYTEST) tests/ui -q
	BROWSER=webkit   $(PYTEST) tests/ui -q

.PHONY: test-e2e
test-e2e: ## End-to-end browser journeys only
	$(PYTEST) -m e2e -v

.PHONY: test-smoke
test-smoke: ## Critical-path subset, used as a pipeline gate
	$(PYTEST) -m smoke -v

.PHONY: test-parallel
test-parallel: ## Parallel where it helps, sequential where it does not
	@# Three groups, for three different reasons.
	@#
	@# 1. Everything except UI: fully parallel. These are I/O-bound on
	@#    PostgreSQL and scale well across cores.
	@# 2. UI: capped at UI_WORKERS. The bottleneck is the single-worker Vite dev
	@#    server, not the test machine - past ~4 browsers it saturates and the
	@#    suite gets slower AND less reliable, which is the worst of both.
	@# 3. `serial` tests: sequential. They either assert whole-catalogue
	@#    invariants (two reads that must see the same moment) or they ARE the
	@#    concurrency under test, and xdist would add a second uncontrolled
	@#    source of it.
	$(PYTEST) backend/tests tests --ignore=tests/ui -n auto -m "not serial"
	$(PYTEST) tests/ui -n $(UI_WORKERS)
	$(PYTEST) -m serial

.PHONY: test-fast
test-fast: ## Everything except the deliberately slow tests
	$(PYTEST) -m "not slow and not serial" -n auto

.PHONY: test-cov
test-cov: ## Unit tests with a coverage report
	$(PYTEST) backend/tests --cov --cov-report=term-missing --cov-report=html

.PHONY: benchmark
benchmark: ## Measure sequential vs parallel execution time honestly
	$(PY) scripts/benchmark_parallel.py

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
.PHONY: test-allure
test-allure: ## Run every suite and collect Allure results
	rm -rf $(ALLURE_RESULTS)
	-$(PYTEST) --alluredir=$(ALLURE_RESULTS)

.PHONY: allure-report
allure-report: ## Generate a static Allure report
	allure generate $(ALLURE_RESULTS) --clean -o $(ALLURE_REPORT)
	@echo "Report written to $(ALLURE_REPORT)/index.html"

.PHONY: allure-serve
allure-serve: ## Open the Allure report in a browser
	allure serve $(ALLURE_RESULTS)

.PHONY: report
report: test-allure allure-report ## Run everything and build the report

# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
.PHONY: perf
perf: ## Locust with the web UI (http://localhost:8089)
	$(PY) -m locust -f tests/performance/locust/locustfile.py --host http://localhost:8000

.PHONY: perf-headless
perf-headless: ## Short headless load run
	$(PY) -m locust -f tests/performance/locust/locustfile.py \
	  --host http://localhost:8000 --headless -u 20 -r 5 -t 60s \
	  --html artifacts/locust-report.html --only-summary

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
.PHONY: lint
lint: ## Ruff + Black check + MyPy
	$(PY) -m ruff check .
	$(PY) -m black --check .
	$(PY) -m mypy backend/app || true

.PHONY: format
format: ## Auto-format and auto-fix
	$(PY) -m ruff check . --fix
	$(PY) -m black .

.PHONY: typecheck
typecheck: ## MyPy on the backend
	$(PY) -m mypy backend/app

.PHONY: frontend-lint
frontend-lint: ## ESLint + tsc on the frontend
	cd frontend && npm run lint && npm run typecheck

.PHONY: frontend-build
frontend-build: ## Production build of the frontend
	cd frontend && npm run build

.PHONY: hooks
hooks: ## Install pre-commit hooks
	$(PY) -m pre_commit install

.PHONY: check
check: lint test-unit ## What to run before pushing

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
.PHONY: clean-artifacts
clean-artifacts: ## Delete reports, traces, videos and caches
	rm -rf $(ALLURE_RESULTS) $(ALLURE_REPORT) artifacts htmlcov .coverage \
	       .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} + 2>/dev/null || true
