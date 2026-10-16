.PHONY: up down check test

up:
	docker compose up -d postgres redis

down:
	docker compose down

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest

test:
	uv run pytest
