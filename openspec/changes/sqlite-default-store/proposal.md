# Proposal

## Why

The desktop app (ADR 0001) is installed by people who have no Postgres or Redis. Today, price history and the DB-backed paths simply switch off without Postgres ("history and preferences off" in `doctor`), yet v1 needs history. ADR 0002 makes SQLite the default store and moves the multi-user server stack into an optional `[server]` extra.

## What Changes

- The default `DATABASE_URL` becomes a local SQLite file in the user data dir. Postgres still works when `DATABASE_URL` points at it.
- The schema is created or upgraded automatically on first use. No `make up` and no manual `alembic upgrade`.
- Models and migrations become dialect-portable: JSON with a JSONB variant on Postgres, and a portable `now()` default.
- Preferences live in the same store. **BREAKING (internal):** the JSON-file fallback is removed, and an existing `prefs.json` is imported once.
- **BREAKING (packaging):** `fastapi`, `uvicorn`, `asyncpg`, `redis`, `taskiq*` and the FastAPI/OTLP instrumentation move to the `[server]` extra. CLI, MCP and the core import and run without them.
- `doctor` reports the store (backend and location) instead of "Postgres reachable".

## Capabilities

### New Capabilities

- `local-storage`: where buy-smarter keeps price history, snapshots and preferences, how the store comes into being, and how a server deployment overrides it.

### Modified Capabilities

(none: no specs exist yet)

## Impact

- Code: `config.py`, `db/base.py`, `db/models.py`, `alembic/env.py`, `alembic/versions/8e237249c427_initial_schema.py`, `memory/prefs.py`, `telemetry.py`, `cli/main.py` (doctor).
- Dependencies: adds `aiosqlite`; `pyproject.toml` gains the `[server]` extra. The Dockerfile and compose install `.[server]`.
- CI: one job runs the suite on SQLite (default) and one runs the server tests with `[server]`.
