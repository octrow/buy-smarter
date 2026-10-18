# SQLite is the default store; Postgres + Redis server stays as the `[server]` extra

The desktop app cannot ship Postgres or Redis, and v1 needs price history and preferences locally. So SQLite (`sqlite+aiosqlite`) becomes the default `DATABASE_URL` for all in-process entry points (CLI, MCP, GUI). The schema stays one: the six `JSONB` columns become `JSON().with_variant(JSONB, "postgresql")`, and alembic runs with `render_as_batch=True` for SQLite. The multi-user server path (`api/`, `jobs.py`, `worker.py`, Redis, asyncpg, the compose Postgres/Redis services) stays in the tree behind an optional `[server]` extra. Its tests keep running in CI, and the desktop build installs without the extra.

## Considered Options

- **Git tag, then delete the server code**: CI and the 90% coverage gate would stop carrying it. Rejected: the code should stay ready to use.
- **Separate `server` branch**: drifts from `main`, and the merge back gets painful.
- **A history-only SQLite store next to Postgres**: two data paths that duplicate `repo.py`.
