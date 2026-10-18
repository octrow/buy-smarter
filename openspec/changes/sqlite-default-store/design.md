# Design

## Context

See proposal.md for the motivation. Today:
- `db/base.py` builds a module-level engine from `settings.database_url` (default `postgresql+asyncpg://…`, `NullPool`).
- `memory/prefs.py` and `knowledge/store.py` are DB-primary with JSON-file fallbacks under `~/.config/buysmarter/`.
- `JSONB` sits in the six `db/models.py` columns and in `llm/models.py`.
- The initial migration hard-codes `postgresql.JSONB` and `server_default=sa.text('now()')`.
- `alembic/` sits at the repo root and is not in the wheel (`packages = ["src/buysmarter"]`). `alembic/env.py` calls `asyncio.run(...)` and `fileConfig()`, and overwrites the URL from Settings.
- `repo.py:169` uses `ilike`.
- `telemetry.py` imports fastapi, the FastAPI instrumentor, the Prometheus reader and `prometheus_client` at module top, and the core imports `telemetry`.
- CI greps `^Postgres +ok` in `ci.yml` and `deploy/kind/smoke.sh`.

## Goals / Non-Goals

**Goals:** one schema and one `repo.py` for both dialects; a zero-setup local store that works from an installed wheel; a core importable without the server stack; tests that never touch the developer's real store.

**Non-Goals:** moving data between SQLite and Postgres; concurrent writers beyond what WAL gives; changing how the API/worker behave or how they are deployed.

## Decisions

- **ORM stays SQLAlchemy 2.0.** SQLx (Rust), TypeORM and Drizzle (TypeScript) would mean rewriting the core in another language. Prisma's Python client is archived.
- **Migrations in the package.** Move `alembic/` to `src/buysmarter/migrations/` (`env.py`, `script.py.mako`, `versions/`). `ensure_schema()` builds `alembic.config.Config()` in code with `script_location="buysmarter:migrations"`, so no ini file is needed. The root `alembic.ini` stays for developer CLI use and for Docker/Helm, with `script_location = src/buysmarter/migrations`. Check: the wheel lists `buysmarter/migrations/versions/*.py`.
- **`env.py` hygiene.** Call `fileConfig` only when `config.config_file_name` is set. Take the URL from Settings only when the Config has none. Use a sync connection when the URL is sync (`sqlite://`), and the existing async path otherwise.
- **`ensure_schema()` is sync and SQLite-only.** Location: `db/schema.py`. It rewrites `sqlite+aiosqlite://` to `sqlite://` (stdlib sqlite3, no event loop) and runs `upgrade head`. For any other dialect it is a no-op. It is called at the entry points before a loop exists: in the typer root callback (CLI), before `mcp.run()` (MCP), and in GUI startup before the core thread starts. Alternative: a lazy hook on first session. Rejected, because nested `asyncio.run` fails inside the running CLI, MCP and GUI loops.
- **Migration lock.** On the migration connection, `isolation_level=None` plus a `begin` event that emits `BEGIN IMMEDIATE` (the SQLAlchemy pysqlite recipe), and `transactional_ddl=True` in `context.configure` for SQLite. A second process waits on the busy timeout, then sees head.
- **Portable types.** `JSON().with_variant(JSONB(), "postgresql")` in the models, in `llm/models.py` and in the initial migration. Defaults use `server_default=sa.func.now()`, which renders as `now()` on Postgres and as `CURRENT_TIMESTAMP` on SQLite. Postgres DDL stays identical, so existing Postgres databases need no new revision (checked by diffing `upgrade head --sql`).
- **UTC.** A `UTCDateTime` TypeDecorator (`impl=DateTime(timezone=True)`) is used for every timestamp column. On write it converts to UTC; on read it attaches UTC to naive values. Python-side `default=lambda: datetime.now(UTC)` sits next to `server_default` (microsecond precision on SQLite, no DDL change). `list_snapshots`/`latest_snapshot` order by `(captured_at desc, id desc)`.
- **Cyrillic case folding.** A SQLite `connect` event registers `create_function("lower", 1, str.lower, deterministic=True)`, which overrides the ASCII built-in, so `ilike` works unchanged. The same event sets `journal_mode=WAL`, `foreign_keys=ON` and `busy_timeout`.
- **Default URL.** `sqlite+aiosqlite:///{platformdirs.user_data_dir("buysmarter")}/buysmarter.db`. The parent dir is created on first use, and env `DATABASE_URL` overrides. `platformdirs` becomes a direct dependency.
- **Reachability.** `db_reachable()` passes `connect_args={"timeout": …}` only for asyncpg. The `NullPool` docstring is updated (aiosqlite opens a fresh connection per session, which is fine).
- **Legacy import.** A one-time import runs after `ensure_schema()` for both prefs and knowledge. It honours `$BUYSMARTER_PREFS_FILE` / `$BUYSMARTER_KNOWLEDGE_DIR` and imports only missing keys/slugs. On success the file is renamed to `*.imported`; corrupt or non-object JSON is renamed to `*.invalid` with a warning. A failed rename is logged and not fatal, and a re-run is safe because only missing keys are imported. The fallback code paths are deleted.
- **Telemetry split.** Imports of fastapi, `FastAPIInstrumentor`, `PrometheusMetricReader` and `prometheus_client` move into the functions that need them (`instrument_app`, `_install_providers`, `instrument_worker`). `opentelemetry-instrumentation-fastapi`, `opentelemetry-exporter-prometheus` and `prometheus-client` go to `[server]`. The OTel SDK and the httpx/sqlalchemy instrumentors stay in base deps.
- **Server entry points.** `api/main.py` and `worker.py` wrap their server imports and exit with "install buysmarter[server]".
- **Tests.** At the very top of `tests/conftest.py`, next to `BUYSMARTER_BROKER`, set `DATABASE_URL=sqlite+aiosqlite:///<session tmp>/test.db` before any buysmarter import. A session fixture calls `ensure_schema()`, and a per-test fixture truncates tables. `_isolate_knowledge_store` is rewritten to use the tmp DB instead of disabling it. `tests/db` runs on SQLite with no skip. Server tests carry `pytest.mark.server` (their conftest does `pytest.importorskip("fastapi")`). `db/base.py` and `db/repo.py` leave the coverage `omit` list.
- **CI.** The `server` job runs `uv sync --locked --extra server` with a Postgres service, then alembic upgrade, ruff, mypy, complexipy and the full pytest with `--cov-fail-under=90`. The `core` job runs `uv sync --locked` with no extra, then `pytest -m "not server"` (no threshold) and `python -c "import buysmarter.cli.main, buysmarter.mcp_server.server"`.

## Risks / Trade-offs

- [Risk] Editing an applied migration file → Postgres DDL is unchanged by construction; verified by the `--sql` diff (task 1.3).
- [Risk] Overriding `lower` changes behaviour for non-search uses → the code only calls `lower` through `ilike`; a grep check in task 2.3 confirms this.
- [Risk] The truncate-per-test fixture slows the suite → SQLite on tmpfs is fast; measure it and fall back to transaction rollback if the suite gets more than 10% slower.
- [Risk] Moving `alembic/` breaks Docker/Helm → those paths are updated in the same change and verified by the docker and kind CI jobs.

## Migration Plan

Users upgrade, the store is created at first start, and the JSON prefs/knowledge are imported and renamed to `.imported`. Postgres deployments already set `DATABASE_URL` and run the migrate step, and only the alembic path changes for them. Rollback is to reinstall the previous version and rename `*.imported` back. This goes in one CHANGELOG line.
