"""One test per Scenario of openspec/changes/sqlite-default-store/specs/local-storage/spec.md.

Every test drives buy-smarter in a subprocess with an isolated HOME/XDG tree (see conftest.py).
"""

import json
import sqlite3
from pathlib import Path

import pytest

from tests.storage.conftest import Store

HEAD_SCRIPT = """
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config()
cfg.set_main_option("script_location", "buysmarter:migrations")
print(ScriptDirectory.from_config(cfg).get_current_head())
"""


def _head(store: Store) -> str:
    return store.run_ok(HEAD_SCRIPT).stdout.strip()


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def _versions(db: Path) -> list[str]:
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("select version_num from alembic_version")]


def _cli(args: str) -> str:
    """Script that runs the real typer app (root callback = process start) with ``args``."""
    return f"""
        import sys
        from buysmarter.cli.main import app
        try:
            app({args!r}.split(), standalone_mode=False)
        except SystemExit as e:
            sys.exit(e.code)
    """


# --- Works without a database server ---------------------------------------------------------


def test_first_run_stores_snapshots_locally_and_history_returns_them(store: Store) -> None:
    # spec: Works without a database server / First run on a clean machine
    # Arrange: no DATABASE_URL, no Postgres, empty data dir
    save = """
        import asyncio
        __PRODUCT__
        from buysmarter.services.collect import save_snapshots
        asyncio.run(save_snapshots([(P, {}, [])], "ирригатор"))
    """
    # Act: process start (CLI), the run persists a snapshot, a later `history` reads it back
    store.run_ok(_cli("history warmup"))
    store.run_ok(save)
    shown = store.run_ok(_cli("history waterpik"))

    # Assert
    assert store.db_path.exists()
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("select min_price from price_snapshots").fetchall() == [(30000,)]
    assert "Waterpik WP-100" in shown.stdout


def test_preference_survives_restart(store: Store) -> None:
    # spec: Works without a database server / Data survives restarts
    # Arrange
    store.run_ok("""
        import asyncio
        from buysmarter.db.schema import ensure_schema
        from buysmarter.memory import prefs
        ensure_schema()
        asyncio.run(prefs.set("city", "Алматы"))
    """)

    # Act: a fresh process
    out = store.run_ok("""
        import asyncio
        from buysmarter.db.schema import ensure_schema
        from buysmarter.memory import prefs
        ensure_schema()
        print(asyncio.run(prefs.get("city")))
    """)

    # Assert
    assert out.stdout.strip() == "Алматы"


# --- Local store schema is prepared automatically ---------------------------------------------


def test_missing_store_file_is_created_with_current_schema_at_start(store: Store) -> None:
    # spec: Local store schema is prepared automatically / Missing store file
    # Arrange
    assert not store.db_path.exists()
    head = _head(store)

    # Act: CLI start, then MCP server start (stdin closed, so it exits right after startup)
    store.run_ok(_cli("history x"))
    cli_tables, cli_versions = _tables(store.db_path), _versions(store.db_path)
    store.db_path.unlink()
    store.run("""
        import runpy
        runpy.run_module("buysmarter.mcp_server", run_name="__main__")
    """)

    # Assert
    assert {"products", "price_snapshots", "user_preferences"} <= cli_tables
    assert cli_versions == [head]
    assert store.db_path.exists(), "MCP start did not create the store"
    assert _versions(store.db_path) == [head]


def test_store_from_older_version_is_upgraded_in_place_keeping_history(store: Store) -> None:
    # spec: Local store schema is prepared automatically / Store from an older version
    # Arrange: a store at the second revision with one product row
    old_rev = "05dbcfa0e44d"
    store.run_ok(f"""
        import sqlite3
        from alembic import command
        from alembic.config import Config
        from pathlib import Path
        from buysmarter.config import get_settings
        cfg = Config()
        cfg.set_main_option("script_location", "buysmarter:migrations")
        Path({str(store.data_dir)!r}).mkdir(parents=True, exist_ok=True)
        command.upgrade(cfg, {old_rev!r})
        with sqlite3.connect({str(store.db_path)!r}) as c:
            c.execute("insert into products (source, source_product_id, title, url) "
                      "values ('kaspi', 'old1', 'Old product', 'https://x')")
    """)
    assert _versions(store.db_path) == [old_rev]

    # Act
    store.run_ok("from buysmarter.db.schema import ensure_schema; ensure_schema()")

    # Assert
    assert _versions(store.db_path) == [_head(store)]
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("select title from products").fetchall() == [("Old product",)]
        cols = {r[1] for r in conn.execute("pragma table_info(price_snapshots)")}
    assert "review_count" in cols


def test_two_processes_starting_together_leave_one_schema_version(store: Store) -> None:
    # spec: Local store schema is prepared automatically / Two processes start together
    # Arrange + Act: two threads race ensure_schema() on one fresh file
    out = store.run_ok("""
        import threading
        from buysmarter.db.schema import ensure_schema
        barrier, errors = threading.Barrier(2), []
        def start():
            barrier.wait()
            try:
                ensure_schema()
            except BaseException as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=start) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        print(errors)
    """)

    # Assert
    assert out.stdout.strip() == "[]"
    assert _versions(store.db_path) == [_head(store)]


def test_installed_package_creates_store_without_a_source_checkout(store: Store) -> None:
    # spec: Local store schema is prepared automatically / Installed package
    # Arrange: cwd is an empty dir (no alembic.ini, no alembic/ folder); migrations must come
    # from the package itself
    # Act
    out = store.run_ok("""
        import os
        from importlib import resources
        from buysmarter.db.schema import ensure_schema
        assert os.listdir(".") == [], os.listdir(".")
        versions = resources.files("buysmarter.migrations") / "versions"
        print(sorted(p.name for p in versions.iterdir() if p.name.endswith(".py")))
        ensure_schema()
    """)

    # Assert
    assert "initial_schema" in out.stdout
    assert _versions(store.db_path) == [_head(store)]


# --- Server deployments use Postgres with explicit migrations --------------------------------


@pytest.mark.skip(
    reason="needs a live PostgreSQL (DATABASE_URL=postgresql+asyncpg://...) and the [server] extra"
)
def test_postgres_configured_behaves_like_the_local_store(store: Store) -> None:
    # spec: Server deployments use Postgres with explicit migrations / Postgres configured
    # Arrange: DATABASE_URL -> migrated Postgres. Act: save a snapshot, a preference and
    # knowledge, then read history/prefs/knowledge back. Assert: same results as local store.
    raise NotImplementedError


def test_postgres_url_start_does_not_run_migrations(store: Store) -> None:
    # spec: Server deployments use Postgres with explicit migrations / No auto-migration on Postgres
    # Arrange: an unreachable Postgres URL; any connection attempt or alembic call would blow up
    url = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"

    # Act
    out = store.run_ok(
        """
        from alembic import command
        def boom(*a, **k):
            raise AssertionError("alembic was invoked against Postgres")
        command.upgrade = boom
        command.stamp = boom
        from buysmarter.db.schema import ensure_schema
        ensure_schema()
        print("no-op")
        """,
        extra_env={"DATABASE_URL": url},
    )

    # Assert
    assert out.stdout.strip() == "no-op"
    assert not store.db_path.exists()


# --- Case-insensitive search in any script ----------------------------------------------------


def test_history_search_finds_cyrillic_title_by_lowercase_term(store: Store) -> None:
    # spec: Case-insensitive search in any script / Cyrillic capital letter
    # Arrange
    out = store.run_ok("""
        import asyncio
        from market_sources.base import RawProduct
        from buysmarter.db import repo
        from buysmarter.db.base import async_session
        from buysmarter.db.schema import ensure_schema
        ensure_schema()

        async def go():
            raw = RawProduct(source="kaspi", product_id="c1", title="Ирригатор PECHAM",
                             url="https://kaspi.kz/shop/p/c1", price=1000)
            async with async_session() as s:
                p = await repo.upsert_product(s, raw)
                await repo.add_price_snapshot(s, p.id, 1000, 1)
            async with async_session() as s:
                return [x.title for x in await repo.find_products(s, "ирригатор")]

        print(asyncio.run(go()))
    """)

    # Act + Assert
    assert out.stdout.strip() == "['Ирригатор PECHAM']"


# --- Timestamps are UTC and ordered -----------------------------------------------------------


def test_same_second_snapshots_list_newest_first_with_utc_timestamps(store: Store) -> None:
    # spec: Timestamps are UTC and ordered / Same-second snapshots
    # Arrange + Act: five products, two back-to-back snapshots each
    out = store.run_ok("""
        import asyncio, json
        __PRODUCT__
        from buysmarter.db import repo
        from buysmarter.db.base import async_session
        from buysmarter.db.schema import ensure_schema
        ensure_schema()

        async def go():
            result = []
            for n in range(5):
                raw = P.model_copy(update={"product_id": f"k{n}"})
                async with async_session() as s:
                    p = await repo.upsert_product(s, raw)
                    first = await repo.add_price_snapshot(s, p.id, 100, 1)
                    second = await repo.add_price_snapshot(s, p.id, 200, 1)
                async with async_session() as s:
                    snaps = await repo.list_snapshots(s, p.id)
                    latest = await repo.latest_snapshot(s, p.id)
                result.append({
                    "order": [x.id for x in snaps], "expected": [second.id, first.id],
                    "latest": latest.id, "offsets": [str(x.captured_at.utcoffset()) for x in snaps],
                })
            return result

        print(json.dumps(asyncio.run(go())))
    """)

    # Assert
    for row in json.loads(out.stdout):
        assert row["order"] == row["expected"]
        assert row["latest"] == row["expected"][0]
        assert row["offsets"] == ["0:00:00", "0:00:00"]


# --- Core installs without server dependencies ------------------------------------------------


def test_core_runs_with_server_dependencies_unavailable(store: Store) -> None:
    # spec: Core installs without server dependencies / Plain install
    # Arrange: fastapi, uvicorn, asyncpg, redis, taskiq*, prometheus* cannot be imported
    code = """
        __BLOCK__
        import buysmarter.cli.main, buysmarter.mcp_server.server, buysmarter.services.recommend
        from typer.testing import CliRunner
        runner = CliRunner()
        codes = [runner.invoke(buysmarter.cli.main.app, a).exit_code
                 for a in (["history", "x"], ["doctor"])]
        print(codes)
    """

    # Act
    out = store.run_ok(code)

    # Assert
    assert out.stdout.strip() == "[0, 0]"


def test_server_entry_points_without_extra_exit_naming_the_extra(store: Store) -> None:
    # spec: Core installs without server dependencies / Server entry point without the extra
    # Arrange + Act: import each entry point with the server packages blocked
    results = {
        module: store.run(f"__BLOCK__\nimport {module}")
        for module in ("buysmarter.api.main", "buysmarter.worker")
    }

    # Assert
    for module, proc in results.items():
        assert proc.returncode != 0, module
        assert "buysmarter[server]" in proc.stderr, (module, proc.stderr[-500:])
        assert "ModuleNotFoundError" not in proc.stderr, module
        assert "Traceback" not in proc.stderr, module


# --- Existing local data is carried over ------------------------------------------------------

READ_PREFS = """
    import asyncio, json
    from buysmarter.db.schema import ensure_schema
    from buysmarter.memory import prefs
    ensure_schema()
    print(json.dumps(asyncio.run(prefs.get_all()), ensure_ascii=False))
"""


def test_legacy_prefs_file_is_imported_once_without_overwriting(store: Store) -> None:
    # spec: Existing local data is carried over / Upgrade with a preferences file
    # Arrange: store already holds budget; the legacy file has city (new) and budget (clash)
    store.run_ok("""
        import asyncio
        from buysmarter.db.schema import ensure_schema
        from buysmarter.memory import prefs
        ensure_schema()
        asyncio.run(prefs.set("budget", 9000))
    """)
    store.prefs_file.parent.mkdir(parents=True)
    store.prefs_file.write_text(json.dumps({"city": "Алматы", "budget": 5000}), encoding="utf-8")

    # Act: first start imports; second start must not import again
    store.run_ok(_cli("history x"))
    first = json.loads(store.run_ok(READ_PREFS).stdout)
    imported = store.prefs_file.with_name("prefs.json.imported")
    after_first = (store.prefs_file.exists(), imported.exists())
    store.prefs_file.write_text(json.dumps({"city": "Астана"}), encoding="utf-8")
    store.run_ok(_cli("history x"))
    second = json.loads(store.run_ok(READ_PREFS).stdout)

    # Assert
    assert first == {"city": "Алматы", "budget": 9000}
    assert after_first == (False, True)
    assert second == first  # file recreated after the one-time import is not re-imported


def test_legacy_knowledge_files_are_imported_and_used_by_next_run(store: Store) -> None:
    # spec: Existing local data is carried over / Upgrade with learned categories
    # Arrange: a legacy knowledge file for a category the store lacks
    from buysmarter.knowledge.brands import BrandBook
    from buysmarter.knowledge.models_book import ModelBook, ModelEntry

    book = ModelBook(entries=[ModelEntry(canonical="Acme X1", brand="Acme", codes=["X1"])])
    store.knowledge_dir.mkdir(parents=True)
    (store.knowledge_dir / "тестовая-категория.json").write_text(
        json.dumps(
            {
                "brand_book": BrandBook().model_dump(mode="json"),
                "model_book": book.model_dump(mode="json"),
                "category_type": "spec_driven",
                "generated_by": "legacy",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Act: start (import), then a later process reads the books like the next recommendation
    store.run_ok(_cli("history x"))
    out = store.run_ok("""
        import asyncio
        from buysmarter.db.schema import ensure_schema
        from buysmarter.knowledge.store import get_stored_books
        ensure_schema()
        brand, model = asyncio.run(get_stored_books("тестовая категория"))
        print(brand is not None, [e.canonical for e in model.entries] if model else None)
    """)

    # Assert
    assert out.stdout.strip() == "True ['Acme X1']"
    assert not (store.knowledge_dir / "тестовая-категория.json").exists()


def test_corrupt_prefs_file_is_set_aside_with_a_warning_and_app_starts(store: Store) -> None:
    # spec: Existing local data is carried over / Corrupt preferences file
    # Arrange
    store.prefs_file.parent.mkdir(parents=True)
    store.prefs_file.write_text("{not json", encoding="utf-8")
    code = """
        import logging, sys
        records = []
        class H(logging.Handler):
            def emit(self, record):
                records.append(record)
        logging.getLogger("buysmarter").addHandler(H())
        from buysmarter.cli.main import app
        app(["history", "x"], standalone_mode=False)
        print("WARNINGS", sum(r.levelno >= logging.WARNING for r in records))
    """

    # Act
    out = store.run_ok(code)

    # Assert
    assert not store.prefs_file.exists()
    assert store.prefs_file.with_name("prefs.json.invalid").read_text() == "{not json"
    assert int(out.stdout.rsplit("WARNINGS", 1)[1]) >= 1


# --- Doctor reports the store -----------------------------------------------------------------


def test_doctor_shows_storage_row_with_local_store_path_as_ok(store: Store) -> None:
    # spec: Doctor reports the store / Default local store
    # Arrange: defaults only
    # Act
    out = store.run_ok(_cli("doctor"))

    # Assert
    row = next((ln for ln in out.stdout.splitlines() if ln.startswith("Storage")), None)
    assert row is not None, out.stdout
    assert "ok" in row and "missing" not in row
    assert str(store.db_path) in row


def test_doctor_marks_unwritable_storage_as_failing_with_what_is_off(store: Store) -> None:
    # spec: Doctor reports the store / Unwritable location
    # Arrange: the data dir's parent is a regular file, so nothing can be created under it
    blocker = store.root / "blocker"
    blocker.write_text("file, not a dir")
    url = f"sqlite+aiosqlite:///{blocker}/sub/buysmarter.db"

    # Act
    proc = store.run(_cli("doctor"), extra_env={"DATABASE_URL": url})

    # Assert
    assert proc.returncode == 0, proc.stderr[-1500:]
    row = next((ln for ln in proc.stdout.splitlines() if ln.startswith("Storage")), None)
    assert row is not None, proc.stdout
    assert "missing" in row
    for words in ("history", "preferences", "learned categories"):
        assert words in row
