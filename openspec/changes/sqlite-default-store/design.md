# Design

## Context

See proposal.md for the motivation. Today `db/base.py` builds one engine from `settings.database_url` (default `postgresql+asyncpg://…`). `db_reachable()` decides whether the DB-backed paths run. `memory/prefs.py` falls back to `~/.config/buysmarter/prefs.json`. The models use `JSONB` in six columns. The initial migration hard-codes `postgresql.JSONB` and `server_default=sa.text('now()')`, and SQLite rejects both. The core (`services/collect.py`, `services/recommend.py`, `llm/adapter.py`) imports `buysmarter.telemetry`, which imports `fastapi` and its OTel instrumentor at module top.

## Goals / Non-Goals

**Goals:** one schema and one `repo.py` for both dialects; zero-setup local store; a core importable without the server stack.

**Non-Goals:** migrating data between SQLite and Postgres; concurrent multi-process writers on SQLite beyond what WAL gives; changing the API/worker behaviour.

## Decisions

- **Portable column types.** Use `JSON().with_variant(JSONB(), "postgresql")` in the models and in the initial migration. Postgres DDL stays byte-identical, so already-migrated Postgres databases need no new revision. Alternative: a new revision that alters the types. Rejected: Postgres would not change at all, only SQLite needs the edit.
- **Portable timestamp default.** `server_default=sa.func.now()` renders as `now()` on Postgres and as `CURRENT_TIMESTAMP` on SQLite. Same DDL-identity argument.
- **Default URL.** `sqlite+aiosqlite:///<user data dir>/buysmarter.db`, resolved from the XDG data dir (`~/.local/share/buysmarter`) and the platform equivalents on Windows/macOS through the stdlib (`platformdirs` is already a transitive dependency, check before adding it). The env `DATABASE_URL` overrides it.
- **Auto-migrate.** On the first storage use per process, run alembic `upgrade head` programmatically against the configured URL (guarded by a module-level flag). `render_as_batch=True` is set in `env.py` when the dialect is SQLite, so the later `drop_column` revisions work. Alternative: `metadata.create_all`. Rejected: an older store would never get upgraded.
- **SQLite pragmas.** `journal_mode=WAL` and `foreign_keys=ON` via a connect event. WAL lets the GUI's worker thread and an MCP process read while a write happens.
- **Reachability probe.** `db_reachable()` passes `timeout` only for asyncpg. aiosqlite takes `timeout` too, but the meaning differs (lock wait), so branch on the dialect explicitly.
- **Prefs.** Drop the JSON fallback. On the first store use, import `prefs.json` keys that are absent from the table, then rename the file to `prefs.json.imported`. Rename rather than delete, to avoid data loss.
- **Telemetry split.** Move the FastAPI pieces (`FastAPIInstrumentor`, the `/metrics` route helper) into a function that imports them lazily, called only from `api/main.py`. The core keeps the OTel SDK and the httpx/sqlalchemy instrumentors, which stay in base deps (small, pure Python).
- **Server entry points.** `api/main.py` and `worker.py` catch `ImportError` at the top and exit with "install buysmarter[server]".

## Risks / Trade-offs

- [Risk] Editing an applied migration file → Postgres DDL is unchanged by construction. Verify with `alembic upgrade head --sql` diffed before and after on the Postgres dialect.
- [Risk] SQLite JSON has no JSONB operators → `rg 'astext|\[.*\]\.as' src` before the change. `repo.py` currently only uses `avg`/`min`/`count`.
- [Risk] Timezone-aware `DateTime` on SQLite comes back naive → normalize to UTC in `repo.py` reads where history compares dates. A test covers it.
- [Risk] The test suite assumes Postgres in places → the default suite runs on a tmp SQLite. The Postgres run stays as a separate CI job with the server extra.

## Migration Plan

Users upgrade, the store is created on first run, and `prefs.json` is imported. Existing Postgres users already set `DATABASE_URL` (compose does), so nothing changes for them. Rollback is to reinstall the previous version: its JSON prefs file is still there as `prefs.json.imported`, so renaming it back restores the old behaviour.
