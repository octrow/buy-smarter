"""``history`` and ``refresh`` in the CLI, in the words of the app's «История цен» (#30, #31)."""

from typer.testing import CliRunner

from buysmarter.cli import history as history_cli
from buysmarter.cli import main
from buysmarter.cli.history import NO_TRACKED_CATEGORIES, NOTHING_TRACKED, sparkline
from buysmarter.services.price_tracking import (
    CategoryRefresh,
    PriceChange,
    ProductHistory,
    SnapshotRow,
)

runner = CliRunner()
_SNAPS = [
    SnapshotRow(captured_at="2026-10-25T10:00", min_price=3700, offer_count=2),
    SnapshotRow(captured_at="2026-10-24T10:00", min_price=4000, offer_count=1),
]
_ENTRY = ProductHistory(title="Кроссовки IFORA", source="kaspi", snapshots=_SNAPS)


def _found(entries):
    async def find(term):
        return entries

    return find


def _out(*args: str) -> str:
    result = runner.invoke(main.app, list(args))
    assert result.exit_code == 0, result.output
    return " ".join(result.stdout.split())


def test_history_without_a_query_lists_recent_products_with_their_change(monkeypatch) -> None:
    monkeypatch.setattr(history_cli, "run_product_history", _found([_ENTRY]))

    out = _out("history")

    assert "Недавние товары" in out and "Кроссовки IFORA" in out and "▼ 8%" in out


def test_history_of_a_product_shows_its_snapshots_and_trend_in_russian(monkeypatch) -> None:
    monkeypatch.setattr(history_cli, "run_product_history", _found([_ENTRY]))

    out = _out("history", "IFORA")

    assert "Дата" in out and "Мин. цена" in out and "Предложений" in out
    assert "3 700 ₸" in out and sparkline([4000, 3700]) in out


def test_nothing_tracked_is_said_as_the_app(monkeypatch) -> None:
    monkeypatch.setattr(history_cli, "run_product_history", _found([]))
    assert NOTHING_TRACKED in _out("history")

    async def none():
        return []

    monkeypatch.setattr(history_cli, "run_refresh_tracked", none)
    assert NO_TRACKED_CATEGORIES in _out("refresh")


def test_refresh_shows_each_drop_with_how_much_cheaper(monkeypatch) -> None:
    drop = PriceChange(title="Кроссовки IFORA", old_price=4000, new_price=3700, delta=-300)

    async def refreshed():
        return [CategoryRefresh(category="кроссовки", product_count=12, changed=1, drops=[drop])]

    monkeypatch.setattr(history_cli, "run_refresh_tracked", refreshed)

    out = _out("refresh")

    assert "кроссовки: товаров 12, цена изменилась у 1" in out
    assert "Снижение" in out and "300 ₸" in out and "-300" not in out


def test_sparkline_runs_from_low_to_high() -> None:
    assert sparkline([1, 2, 3]) == "▁▄█"
    assert sparkline([5]) == ""
