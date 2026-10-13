"""``_collect_kaspi`` fetches per-product details concurrently, keeping order."""

import asyncio

from market_sources.base import RawOffer, RawProduct

from buysmarter.services.recommend import _KASPI_DETAIL_CONCURRENCY, _collect_kaspi

_PRODUCT_COUNT = 6


class SlowKaspiSource:
    """Specs calls sleep longer for earlier products, so completion order is reversed."""

    name = "kaspi"

    def __init__(self, products: list[RawProduct]) -> None:
        self._products = products
        self.in_flight = 0
        self.max_in_flight = 0

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        return self._products

    async def specs(self, product_id: str) -> dict[str, str]:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01 * (_PRODUCT_COUNT - int(product_id)))
        self.in_flight -= 1
        return {"Модель": f"model-{product_id}"}

    async def offers(self, product_id: str) -> list[RawOffer]:
        return []

    async def reviews(self, product_id: str) -> list:  # pragma: no cover - unused
        return []


async def test_collect_kaspi_fetches_specs_concurrently_and_preserves_order() -> None:
    products = [
        RawProduct(source="kaspi", product_id=str(i), title=f"товар {i}", price=1000 + i)
        for i in range(_PRODUCT_COUNT)
    ]
    source = SlowKaspiSource(products)

    candidates, rows = await _collect_kaspi(source, "товар", None, None)

    assert 1 < source.max_in_flight <= _KASPI_DETAIL_CONCURRENCY
    assert [c.source_product_id for c in candidates] == [p.product_id for p in products]
    assert [row[0].product_id for row in rows] == [p.product_id for p in products]
    assert [row[1]["Модель"] for row in rows] == [f"model-{i}" for i in range(_PRODUCT_COUNT)]
