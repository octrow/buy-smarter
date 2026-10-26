"""Price history screen: snapshots newest first; refresh with progress, cancel and reload."""

import asyncio
import threading

from market_sources.base import RawProduct
from PySide6.QtWidgets import QGroupBox, QLabel, QTableWidget

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.gui.screens.history import HistoryScreen
from buysmarter.services.collect import _safe
from buysmarter.services.price_tracking import CategoryRefresh
from tests.gui.conftest import WAIT_MS


async def _seed(prices: list[int]) -> None:
    raw = RawProduct(
        source="kaspi", product_id="h1", title="Waterpik WP-660", url="https://kaspi.kz/p/h1"
    )
    async with async_session() as session:
        product = await repo.upsert_product(session, raw)
        for offers, price in enumerate(prices, start=1):
            await repo.add_price_snapshot(session, product.id, min_price=price, offer_count=offers)
        await session.commit()


def _shown_rows(screen: HistoryScreen) -> tuple[list[str], list[list[str]]]:
    page = screen.results.widget()
    titles = [g.title() for g in page.findChildren(QGroupBox)]
    table = page.findChild(QTableWidget)
    rows = [[table.item(r, c).text() for c in (1, 2)] for r in range(table.rowCount())]
    return titles, rows


def test_history_lists_seeded_snapshots_newest_first(qtbot, runner):
    asyncio.run(_seed([40000, 37000, 35500]))
    screen = HistoryScreen(runner)
    qtbot.addWidget(screen)
    screen.term.setText("waterpik")
    screen.look_up()
    qtbot.waitUntil(lambda: screen.results.widget() is not None, timeout=WAIT_MS)
    titles, rows = _shown_rows(screen)
    assert titles == ["Waterpik WP-660  [Kaspi]"]
    assert rows == [["35\xa0500 ₸", "3"], ["37\xa0000 ₸", "2"], ["40\xa0000 ₸", "1"]]


def test_unknown_product_says_there_is_no_history(qtbot, runner):
    screen = HistoryScreen(runner)
    qtbot.addWidget(screen)
    screen.term.setText("nothing")
    screen.look_up()
    qtbot.waitUntil(screen.find_button.isEnabled, timeout=WAIT_MS)
    texts = [label.text() for label in screen.results.widget().findChildren(QLabel)]
    assert any(text.startswith("Нет истории для «nothing»") for text in texts)


def test_refresh_shows_progress_then_the_summary_and_reloads_the_history(qtbot, runner):
    looked_up, stages = [], []

    async def find(term):
        looked_up.append(term)
        return []

    async def refresh():
        async def ok():
            return []

        await _safe("kaspi", ok())
        return [CategoryRefresh(category="ирригатор", product_count=12, changed=2)]

    screen = HistoryScreen(runner, find=find, refresh=refresh)
    qtbot.addWidget(screen)
    screen._progress.changed.connect(lambda *args: stages.append(args))
    screen.term.setText("waterpik")
    screen.start_refresh()
    qtbot.waitUntil(lambda: screen.refresh_button.isEnabled(), timeout=WAIT_MS)
    assert screen.status.text() == "ирригатор: товаров 12, цена изменилась у 2"
    assert ("kaspi", "running", "collect[kaspi]") in stages
    qtbot.waitUntil(lambda: looked_up == ["waterpik"], timeout=WAIT_MS)


def test_refresh_can_be_cancelled(qtbot, runner):
    started = threading.Event()

    async def slow():
        started.set()
        await asyncio.sleep(60)

    screen = HistoryScreen(runner, refresh=slow)
    qtbot.addWidget(screen)
    screen.start_refresh()
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    screen.cancel_refresh()
    qtbot.waitUntil(screen.refresh_button.isEnabled, timeout=WAIT_MS)
    assert screen.status.text() == "Обновление отменено."


def test_no_tracked_categories_points_to_a_first_search(qtbot, runner):
    async def none():
        return []

    screen = HistoryScreen(runner, refresh=none)
    qtbot.addWidget(screen)
    screen.start_refresh()
    qtbot.waitUntil(screen.refresh_button.isEnabled, timeout=WAIT_MS)
    assert screen.status.text().startswith("Отслеживаемых категорий пока нет")


def test_a_reload_asked_during_a_look_up_runs_after_it(qtbot, runner):
    release, terms = threading.Event(), []

    async def find(term):
        terms.append(term)
        if len(terms) == 1:
            await asyncio.to_thread(release.wait, WAIT_MS / 1000)
        return []

    screen = HistoryScreen(runner, find=find)
    qtbot.addWidget(screen)
    screen.term.setText("waterpik")
    screen.look_up()
    screen.look_up()  # e.g. a refresh finishing while the first look-up runs
    release.set()
    qtbot.waitUntil(lambda: len(terms) == 2, timeout=WAIT_MS)


def test_an_empty_search_lists_recent_products_with_their_last_change(qtbot, runner):
    from buysmarter.services.price_tracking import ProductHistory, SnapshotRow

    async def find(term):
        snaps = [
            SnapshotRow(captured_at="t2", min_price=3700, offer_count=1),
            SnapshotRow(captured_at="t1", min_price=4000, offer_count=1),
        ]
        return [ProductHistory(title="Кроссовки IFORA", source="kaspi", snapshots=snaps)]

    screen = HistoryScreen(runner, find=find)
    qtbot.addWidget(screen)
    screen.look_up()
    qtbot.waitUntil(lambda: screen.results.widget() is not None, timeout=WAIT_MS)
    row = [screen.recent.item(0, c).text() for c in range(4)]
    assert row == ["Кроссовки IFORA", "Kaspi", "3\xa0700 ₸", "▼ 8%"]


def test_refresh_summary_lists_the_biggest_drops():
    from buysmarter.gui.screens.history import refresh_summary
    from buysmarter.services.price_tracking import PriceChange

    drop = PriceChange(title="Кроссовки IFORA", old_price=4000, new_price=3700, delta=-300)
    text = refresh_summary(
        [CategoryRefresh(category="кроссовки", product_count=12, changed=1, drops=[drop])]
    )
    assert text.splitlines()[1] == "   ▼ Кроссовки IFORA: 4\xa0000 ₸ → 3\xa0700 ₸, дешевле на 300 ₸"
