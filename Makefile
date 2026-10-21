.PHONY: install fmt lint typecheck test check up down

install:            ## Install deps and git hooks
	uv sync
	uv run pre-commit install

fmt:                ## Format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test   ## Run before every commit

up:                 ## Start local stack (Stage 1)
	docker compose up -d

down:
	docker compose down
