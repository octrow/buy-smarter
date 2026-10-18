# Tasks

## 1. Portable schema

- [ ] 1.1 Switch the six `JSONB` model columns to `JSON().with_variant(JSONB(), "postgresql")`; verify `uv run mypy src` and the model tests pass
- [ ] 1.2 Make `8e237249c427_initial_schema.py` portable (JSON variant, `sa.func.now()`); verify `alembic upgrade head --sql` on Postgres matches the pre-change output (diff is empty)
- [ ] 1.3 Set `render_as_batch=True` for SQLite in `alembic/env.py`; verify `alembic upgrade head` then `downgrade base` on a tmp SQLite file both succeed

## 2. Local store by default

- [ ] 2.1 Add `aiosqlite`; set the default `database_url` to the per-user data dir SQLite file; test that with no env the URL resolves under the data dir and that `DATABASE_URL` overrides it
- [ ] 2.2 Add WAL + foreign_keys pragmas on SQLite connect and a dialect-aware `db_reachable`; test both against a tmp SQLite
- [ ] 2.3 Run `upgrade head` automatically on first storage use; tests: a missing file is created at head, and a store stamped at an older revision is upgraded with its rows kept
- [ ] 2.4 Normalize DateTime reads to UTC for SQLite in `repo.py`; test that a history round-trip returns tz-aware UTC values

## 3. Preferences

- [ ] 3.1 Remove the JSON fallback from `memory/prefs.py`; verify prefs tests pass against the SQLite store
- [ ] 3.2 Import a legacy `prefs.json` once (only keys absent from the store, then rename to `.imported`); tests: keys import, existing keys are kept, a second start is a no-op

## 4. Server extra

- [ ] 4.1 Make the FastAPI parts of `telemetry.py` lazy; verify `python -c "import buysmarter.services.recommend"` works in an env without fastapi
- [ ] 4.2 Move fastapi, uvicorn, asyncpg, redis, taskiq*, the fastapi/otlp instrumentation and fakeredis-dependent tests behind `[server]`; give `api/main.py` and `worker.py` an `ImportError` → "install buysmarter[server]" exit; test the message
- [ ] 4.3 Dockerfile and compose install `.[server]`; verify `docker compose config` and the image build succeed

## 5. Doctor and CI

- [ ] 5.1 Replace the `Postgres` doctor check with a storage check (backend + path, writable); test the OK and the unwritable cases
- [ ] 5.2 CI: the default job runs on SQLite without the extra, and a second job installs `[server]` and runs the server tests against Postgres; verify both jobs are green and coverage stays ≥ 90%
- [ ] 5.3 End-to-end on a clean `uv tool install` (no Postgres): `recommend`, then `history` shows the snapshot, and `doctor` shows the store path
