# Tasks

## 1. Migrations and portable schema

- [x] 1.1 Move `alembic/` to `src/buysmarter/migrations/`; root `alembic.ini` points there; Dockerfile/compose/Helm updated; verify `uv build` and `unzip -l dist/*.whl | grep migrations/versions`, and `uv run alembic heads` from the repo root
- [x] 1.2 `env.py`: `fileConfig` only with an ini file, URL from Settings only if unset, sync path for `sqlite://`; verify `alembic upgrade head` on a tmp SQLite and on Postgres
- [x] 1.3 JSON-with-JSONB-variant in `db/models.py`, `llm/models.py` and the initial migration, plus `sa.func.now()` defaults; verify the `alembic upgrade head --sql` diff on Postgres is empty
- [x] 1.4 `UTCDateTime` TypeDecorator + Python-side `now(UTC)` defaults; `(captured_at desc, id desc)` ordering; tests: tz-aware UTC round-trip, and two same-second snapshots come back in stable order

## 2. Local store

- [x] 2.0 Test isolation: `DATABASE_URL` → tmp SQLite at the top of `tests/conftest.py`, session `ensure_schema()`, per-test truncate; `tests/db` without skip; drop `db/base.py` and `db/repo.py` from coverage `omit`; verify that `pytest tests/db` passes with no Postgres and that the real data dir is untouched (assert path absent)
- [x] 2.1 Add `aiosqlite` and `platformdirs`; default URL under `user_data_dir`; tests: default resolves under the data dir, env overrides
- [x] 2.2 `db/schema.py::ensure_schema()` (sync, SQLite-only, `BEGIN IMMEDIATE`, `transactional_ddl`); tests: fresh file → head; older revision → upgraded with rows kept; two threads on one fresh file → no error and one `alembic_version` row; Postgres URL → no-op
- [x] 2.3 SQLite connect event: WAL, foreign_keys, busy_timeout, `lower` = `str.lower`; grep shows `lower` is only reached via `ilike`; test: "Ирригатор" found by "ирригатор"
- [x] 2.4 Call `ensure_schema()` in the CLI root callback and before `mcp.run()`; dialect-aware `db_reachable`; tests: a CLI command on an empty data dir creates the store, and the MCP startup path calls `ensure_schema`

## 3. Legacy data

- [x] 3.1 Remove the JSON fallback from `memory/prefs.py`; rewrite `tests/memory/test_prefs.py` and `tests/mcp_server/test_tools.py` prefs cases on the tmp DB
- [x] 3.2 One-time `prefs.json` import (missing keys only, `$BUYSMARTER_PREFS_FILE`, `.imported`/`.invalid`, rename failure non-fatal); tests for each case, and a second run is a no-op
- [x] 3.3 Remove the JSON fallback from `knowledge/store.py` and import `knowledge/*.json` once (missing slugs only, dir → `.imported`); rewrite `_isolate_knowledge_store`; tests: imported category is used by `get_stored_books`, existing slug kept

## 4. Server extra

- [x] 4.1 Lazy server imports in `telemetry.py` (fastapi, instrumentor, Prometheus reader, prometheus_client); verify `python -c "import buysmarter.services.recommend"` in a venv without the extra
- [x] 4.2 `[server]` extra (fastapi, uvicorn, asyncpg, redis, taskiq*, fastapi instrumentation, prometheus exporter, prometheus-client); `api/main.py` and `worker.py` exit with "install buysmarter[server]"; `server` marker + `importorskip` conftest for server tests; test the exit message
- [x] 4.3 Dockerfile and compose install `.[server]`; verify `docker compose config` and the image build

## 5. Doctor, texts, CI

- [x] 5.1 Replace the `Postgres` doctor row with `Storage` (backend + path, writable); update `cli/main.py` "Postgres unavailable" texts and the `mcp_server/server.py` docstrings; tests for OK and unwritable
- [x] 5.2 `ci.yml` docker job and `deploy/kind/smoke.sh` grep `^Storage +ok`; verify both jobs are green
- [x] 5.3 CI split: a `server` job (extra + Postgres; ruff, mypy, complexipy, full pytest at 90%) and a `core` job (no extra; `pytest -m "not server"`, import smoke); verify both green
- [x] 5.4 End-to-end on a clean `uv tool install` from the built wheel (no Postgres, empty data dir): `recommend`, then `history` shows the snapshot, and `doctor` shows `Storage ok` with the path
- [x] 5.5 CHANGELOG: SQLite default, `[server]` extra, legacy import and the rollback line
