"""Which browser Ozon runs on: Chrome once a session is saved, else bundled Chromium, else off."""

import pytest
from market_sources.ozon.browser import ozon_state_path

from buysmarter.services import collect, ozon_browser
from buysmarter.services.ozon_browser import CHROME, CHROMIUM, search_browser, setup_browser

_real_launches = ozon_browser.launches  # captured before the autouse stub replaces it


@pytest.fixture
def installed(monkeypatch):
    def install(*browsers: str) -> None:
        monkeypatch.setattr(ozon_browser, "launches", lambda browser: browser in browsers)

    return install


@pytest.mark.parametrize(
    ("browsers", "session", "expected"),
    [
        ((CHROME,), True, CHROME),
        ((CHROME, CHROMIUM), True, CHROME),
        ((CHROME,), False, None),  # anonymous headless Chrome fails Variti: needs ozon-setup
        ((CHROME, CHROMIUM), False, CHROMIUM),
        ((CHROMIUM,), True, CHROMIUM),
        ((CHROMIUM,), False, CHROMIUM),
        ((), True, None),
        ((), False, None),
    ],
)
def test_search_browser_prefers_chrome_with_a_saved_session(installed, browsers, session, expected):
    installed(*browsers)
    if session:
        ozon_state_path().write_text("{}")
    assert search_browser() == expected


@pytest.mark.parametrize(
    ("browsers", "expected"),
    [((CHROME, CHROMIUM), CHROME), ((CHROMIUM,), CHROMIUM), ((), None)],
)
def test_setup_opens_chrome_when_installed(installed, browsers, expected):
    installed(*browsers)
    assert setup_browser() == expected


def test_a_session_saved_mid_process_switches_searches_to_chrome(installed):
    installed(CHROME)
    assert search_browser() is None
    ozon_state_path().write_text("{}")  # what the GUI's Ozon check does
    assert search_browser() == CHROME


async def test_the_probe_runs_once_per_browser_even_inside_an_event_loop(monkeypatch):
    probes = []

    def probe(channel):
        probes.append(channel)
        return channel == CHROME

    monkeypatch.setattr(ozon_browser, "_probe", probe)
    _real_launches.cache_clear()
    try:
        assert _real_launches(CHROME) is True
        assert _real_launches(CHROME) is True
        assert _real_launches(CHROMIUM) is False
    finally:
        _real_launches.cache_clear()
    assert probes == ["chrome", None]


class _RecordingBrowser:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_ozon_searches_launch_the_selected_channel(monkeypatch):
    monkeypatch.setattr(collect, "OzonBrowser", _RecordingBrowser)
    monkeypatch.setattr(collect, "search_browser", lambda: CHROME)
    active = collect.resolve_sources(["ozon"], None, None, None)
    assert active["ozon"]._browser.kwargs["channel"] == "chrome"


def test_bundled_chromium_launches_with_no_channel(monkeypatch):
    monkeypatch.setattr(collect, "OzonBrowser", _RecordingBrowser)
    monkeypatch.setattr(collect, "search_browser", lambda: CHROMIUM)
    active = collect.resolve_sources(["ozon"], None, None, None)
    assert active["ozon"]._browser.kwargs["channel"] is None


def test_without_a_browser_ozon_is_left_out_and_the_rest_still_run():
    active = collect.resolve_sources(["kaspi", "ozon", "olx"], None, None, None)
    assert sorted(active) == ["kaspi", "olx"]


def test_an_injected_ozon_source_needs_no_browser():
    injected = object()
    assert collect.resolve_sources(["ozon"], None, injected, None) == {"ozon": injected}


class _Ozon:
    def __init__(self, titles: list[str] | None = None) -> None:
        self.titles, self.closed = titles, False

    async def search(self, query: str, budget: int | None = None):
        from market_sources.base import RawProduct
        from market_sources.ozon.browser import OzonSessionExpired

        if self.titles is None:
            raise OzonSessionExpired(403, "https://ozon.kz/api")
        return [RawProduct(source="ozon", product_id=t, title=t, price=1000) for t in self.titles]

    async def aclose(self) -> None:
        self.closed = True


async def test_an_expired_session_is_set_aside_and_the_search_runs_anonymously(monkeypatch):
    state = ozon_state_path()
    state.write_text("{}")
    anonymous = _Ozon(["Наушники Sony"])
    monkeypatch.setattr(collect, "_ozon_source", lambda base_url: anonymous)

    candidates, _rows = await collect._collect_ozon(_Ozon(), "наушники", None, None)

    assert [c.title for c in candidates] == ["Наушники Sony"]
    assert not state.exists() and state.with_name(f"{state.name}.expired").exists()
    assert anonymous.closed
