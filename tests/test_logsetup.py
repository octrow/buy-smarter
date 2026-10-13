"""setup_logging: INFO lands in the rotating file, console handler stays WARNING."""

import logging
import logging.handlers

import pytest

from buysmarter.logsetup import _run_id, new_run_id, setup_logging


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("BUYSMARTER_LOG_LEVEL", "BUYSMARTER_CONSOLE_LOG_LEVEL", "BUYSMARTER_LOG_FILE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_root_handlers():
    """Undo setup_logging()'s handler additions so tests don't leak into each other."""
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
    root.setLevel(before_level)


@pytest.mark.parametrize("subdir", ["a", "nested/b"])
def test_info_record_lands_in_file_not_stderr(tmp_path, capsys, subdir) -> None:
    log_path = tmp_path / subdir / "buysmarter.log"

    setup_logging(log_path)
    logging.getLogger("buysmarter.test").info("hello from test %s", subdir)

    captured = capsys.readouterr()
    assert "hello from test" not in captured.err
    assert "hello from test" not in captured.out
    assert log_path.exists()
    assert f"hello from test {subdir}" in log_path.read_text(encoding="utf-8")


def test_console_handler_stays_at_warning(tmp_path) -> None:
    setup_logging(tmp_path / "buysmarter.log")

    root = logging.getLogger()
    console_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "_buysmarter_logsetup", False)
    ]
    assert console_handlers
    assert all(h.level == logging.WARNING for h in console_handlers)


def test_file_handler_is_info_level_and_rotating(tmp_path) -> None:
    setup_logging(tmp_path / "buysmarter.log")

    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.level == logging.INFO
    assert fh.maxBytes == 5 * 1024 * 1024
    assert fh.backupCount == 5


def test_repeated_setup_logging_does_not_duplicate_handlers(tmp_path) -> None:
    setup_logging(tmp_path / "one.log")
    setup_logging(tmp_path / "two.log")

    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename.endswith("two.log")


def test_run_id_injected_into_records(tmp_path) -> None:
    _run_id.set("-")
    log_path = tmp_path / "b.log"
    setup_logging(log_path)
    logging.getLogger("buysmarter.test").info("before")
    rid = new_run_id()
    logging.getLogger("buysmarter.test").info("after")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(rid) == 8
    assert "[-]" in lines[0]
    assert f"[{rid}]" in lines[1]


def test_env_overrides_levels_and_file(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "env.log"
    monkeypatch.setenv("BUYSMARTER_LOG_LEVEL", "debug")
    monkeypatch.setenv("BUYSMARTER_CONSOLE_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("BUYSMARTER_LOG_FILE", str(log_path))

    setup_logging()

    handlers = {type(h).__name__: h for h in logging.getLogger().handlers}
    assert handlers["RotatingFileHandler"].level == logging.DEBUG
    assert handlers["RotatingFileHandler"].baseFilename == str(log_path)
    assert handlers["StreamHandler"].level == logging.ERROR


def test_bad_env_level_falls_back_with_warning(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("BUYSMARTER_LOG_LEVEL", "LOUD")

    with caplog.at_level(logging.WARNING):
        setup_logging(tmp_path / "x.log")

    fh = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert fh.level == logging.INFO
    assert "BUYSMARTER_LOG_LEVEL" in caplog.text
