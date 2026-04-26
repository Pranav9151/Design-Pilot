.PHONY: help install venv db-up db-down db-reset migrate seed run test test-unit test-integration lint format typecheck clean

# ══════════════════════════════════════════════════════════════════════
# DesignPilot MECH — developer commands
# ══════════════════════════════════════════════════════════════════════

PYTHON ?= python3.12
VENV := .venv
ACTIVATE := . $(VENV)/bin/activate

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────

venv:  ## Create Python virtualenv
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) && pip install --upgrade pip

install: venv  ## Install app + dev dependencies
	$(ACTIVATE) && pip install -e ".[dev]"

# ── Database (docker-compose) ─────────────────────────────────────────

db-up:  ## Start local Postgres + Redis via docker-compose
	docker compose up -d postgres redis
	@echo "Waiting for Postgres..."
	@until docker compose exec -T postgres pg_isready -U designpilot > /dev/null 2>&1; do sleep 0.5; done
	@echo "Postgres is ready."

db-down:  ## Stop local services
	docker compose down

db-reset: db-down  ## Wipe and recreate the local database
	docker compose down -v
	$(MAKE) db-up
	$(MAKE) migrate
	$(MAKE) seed

# ── Migrations & seed ─────────────────────────────────────────────────

migrate:  ## Apply Alembic migrations (upgrade to head)
	$(ACTIVATE) && alembic upgrade head

migrate-down:  ## Downgrade one migration
	$(ACTIVATE) && alembic downgrade -1

migrate-new:  ## Create a new empty migration: make migrate-new name="add_x"
	$(ACTIVATE) && alembic revision -m "$(name)"

seed:  ## Seed reference data (materials)
	$(ACTIVATE) && python -m scripts.seed_materials

# ── Run ───────────────────────────────────────────────────────────────

run:  ## Run the FastAPI app locally (hot-reload)
	$(ACTIVATE) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Tests ─────────────────────────────────────────────────────────────

test:  ## Run the full test suite
	$(ACTIVATE) && pytest

test-unit:  ## Run unit tests only (no DB required)
	$(ACTIVATE) && pytest tests/unit/

test-integration:  ## Run integration tests (requires Postgres + migration)
	$(ACTIVATE) && pytest tests/integration/

test-fast:  ## Run unit tests with minimal output
	$(ACTIVATE) && pytest tests/unit/ -q

test-cov:  ## Run tests with coverage report
	$(ACTIVATE) && pytest --cov=app --cov-report=term-missing --cov-report=html

# ── Quality ───────────────────────────────────────────────────────────

lint:  ## Run ruff linter
	$(ACTIVATE) && ruff check app/ tests/ scripts/

format:  ## Auto-format code with ruff
	$(ACTIVATE) && ruff format app/ tests/ scripts/
	$(ACTIVATE) && ruff check --fix app/ tests/ scripts/

typecheck:  ## Run mypy
	$(ACTIVATE) && mypy app/

check: lint typecheck test  ## Run all checks (CI equivalent)

# ── Clean ─────────────────────────────────────────────────────────────

clean:  ## Remove caches and build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage
