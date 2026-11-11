"""National Bank rates: parsing the feed, the per-unit rate, the day's cache."""

import asyncio
from datetime import date

import httpx

from buysmarter.services import fx

_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rates>
  <date>11.11.2026</date>
  <item><title>USD</title><description>512.40</description><quant>1</quant></item>
  <item><title>CNY</title><description>71.05</description><quant>1</quant></item>
  <item><title>AMD</title><description>12.43</description><quant>10</quant></item>
  <item><title>BAD</title><description>n/a</description><quant>1</quant></item>
</rates>"""


def test_the_feed_gives_tenge_per_one_unit():
    rates = fx.parse_rates(_FEED)

    assert rates.day == date(2026, 11, 11)
    assert rates.kzt == {"USD": 512.40, "CNY": 71.05, "AMD": 1.243}


def _client(calls: list[str], status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, text=_FEED)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _store(monkeypatch, saved: dict) -> None:
    async def get():
        return saved.get(fx.CACHE_KEY)

    async def put(value):
        saved[fx.CACHE_KEY] = value

    monkeypatch.setattr(fx, "_cached", get)
    monkeypatch.setattr(fx, "_cache", put)


def test_the_days_rates_are_fetched_once_then_read_from_the_store(monkeypatch):
    saved: dict = {}
    _store(monkeypatch, saved)
    calls: list[str] = []
    today = date(2026, 11, 11)

    first = asyncio.run(fx.current_rates(today, _client(calls)))
    second = asyncio.run(fx.current_rates(today, _client(calls)))

    assert calls == ["https://nationalbank.kz/rss/get_rates.cfm?fdate=11.11.2026"]
    assert first == second and first is not None and first.kzt["USD"] == 512.40


def test_a_dead_feed_falls_back_to_the_last_saved_rates(monkeypatch):
    saved = {fx.CACHE_KEY: {"day": "2026-11-09", "kzt": {"USD": 500.0}}}
    _store(monkeypatch, saved)

    rates = asyncio.run(fx.current_rates(date(2026, 11, 11), _client([], status=503)))

    assert rates == fx.Rates(day=date(2026, 11, 9), kzt={"USD": 500.0})


def test_no_feed_and_nothing_saved_gives_none(monkeypatch):
    _store(monkeypatch, {})

    assert asyncio.run(fx.current_rates(date(2026, 11, 11), _client([], status=503))) is None
