# Proposal

## Why

The desktop app (ADR 0001) is installed by people who have no Postgres or Redis. Today, price history and the DB-backed paths simply switch off without Postgres ("history and preferences off" in `doctor`), yet v1 needs history. ADR 0002 makes SQLite the default store and moves the multi-user server stack into an optional `[server]` extra. SQLAlchemy stays the ORM. SQLx, TypeORM and Drizzle are not Python, and Prisma's Python client is archived (2025-04).

## What Changes

- The default `DATABASE_URL` becomes a local SQLite file in the per-user data dir (`platformdirs`). Postgres still works when `DATABASE_URL` points at it.
- Migrations move into the package (`buysmarter.migrations`), so an installed wheel carries them.
- A synchronous `ensure_schema()` runs at each process entry point (CLI, MCP, GUI) before any event loop, and **for SQLite only**. Postgres keeps its explicit `alembic upgrade head` step (compose `migrate`, Helm migrate job).
- Models and migrations become dialect-portable: JSON with a JSONB variant on Postgres; `now()` defaults that work on both dialects; UTC-aware timestamps on both.
- Case-insensitive search works on Cyrillic text in SQLite.
- Preferences and category knowledge live in the same store. **BREAKING (internal):** both JSON-file fallbacks are removed; an existing `prefs.json` and `knowledge/*.json` are imported once.
- **BREAKING (packaging):** `fastapi`, `uvicorn`, `asyncpg`, `redis`, `taskiq*`, the FastAPI instrumentation, the Prometheus exporter and `prometheus-client` move to the `[server]` extra. CLI, MCP and the core import and run without them.
- `doctor` reports `Storage` (backend and location) instead of `Postgres`. The smoke checks and the user-facing texts follow.
- CI split: the server job (with `[server]` and Postgres) runs lint, mypy and the full suite with the 90% gate. The default job runs without the extra and executes the non-server tests plus an import smoke test.

## Capabilities

### New Capabilities

- `local-storage`: where buy-smarter keeps price history, snapshots, preferences and category knowledge, how the local store comes into being, and how a server deployment overrides it.

### Modified Capabilities

(none: no specs exist yet)

## Impact

- Code: `config.py`, `db/base.py`, `db/models.py`, `db/repo.py`, `llm/models.py`, `alembic/` → `src/buysmarter/migrations/`, `alembic.ini`, `memory/prefs.py`, `knowledge/store.py`, `telemetry.py`, `api/main.py`, `worker.py`, `cli/main.py`, `mcp_server/server.py`.
- Dependencies: adds `aiosqlite` and `platformdirs` (direct); adds the `[server]` extra.
- Ops: Dockerfile, compose and Helm point alembic at the new migrations path and install `.[server]`. `ci.yml` and `deploy/kind/smoke.sh` grep `^Storage +ok`.
- Tests: `tests/conftest.py` isolates the DB on a tmp SQLite; `tests/db` runs without Postgres; the fallback-based prefs/knowledge/MCP tests are rewritten; a `server` marker is added; `db/base.py` and `db/repo.py` leave the coverage `omit` list.

Graph probes: none (no `graphify-out/`); dependencies traced by grep during the plan-griller pass.

## Grill

Grilled by: plan-griller agent | questions: 12 | plan changes: migrations packaged in the wheel; sync `ensure_schema` at entry points (SQLite only); knowledge JSON import; Cyrillic `lower()`; test DB isolation; smoke greps; CI job split; `BEGIN IMMEDIATE` migration lock; UTC TypeDecorator; prefs import edge cases; server telemetry deps.
