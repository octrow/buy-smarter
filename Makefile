.PHONY: up down observe check test

up:
	docker compose up -d postgres redis

down:
	docker compose --profile observability down

# The whole stack plus Prometheus, Grafana and Jaeger, with traces on.
observe:
	OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 docker compose --profile observability up -d --build

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest

test:
	uv run pytest
