"""A size limit narrows the Kaspi search by its own size facet, both pages."""

from market_sources.base import RawProduct

from buysmarter.services.collect import _collect_kaspi
from buysmarter.services.size_search import kaspi_size_filters, size_limits

_HEIGHT = [
    (":D*Height:менее 75 см", "менее 75 см"),
    (":D*Height:82 см", "82 см"),
    (":D*Height:более 90 см", "более 90 см"),
]


def _card(pid: str) -> RawProduct:
    return RawProduct(source="kaspi", product_id=pid, title=f"Посудомоечная машина {pid}")


class FacetSource:
    """Kaspi-like: the main search returns full-size cards and a height facet."""

    name = "kaspi"

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def search(self, query, city=None, page=0, filters=""):
        self.calls.append((page, filters))
        if not filters:
            return [_card("big")]
        return [_card(f"cube{page}")]

    def facets(self, query, city=None):
        return {"Высота": _HEIGHT, "Ширина": [(":D*Width:45 см", "45 см")]}

    async def specs(self, product_id):
        return {}

    async def offers(self, product_id):
        return []


def test_size_limits_reads_the_side_and_converts_to_cm():
    assert size_limits(["Wi-Fi", "высотой до 50 см", "глубина не более 440 мм"]) == (
        ("высот", 50.0),
        ("глубин", 44.0),
    )


def test_facet_rows_within_the_limit_are_kept():
    facets = {"Высота": _HEIGHT}
    assert kaspi_size_filters(facets, (("высот", 50.0),)) == ":D*Height:менее 75 см"
    assert kaspi_size_filters(facets, (("высот", 85.0),)) == ":D*Height:менее 75 см:D*Height:82 см"


def test_a_facet_that_keeps_every_row_or_no_facet_gives_no_filter():
    assert kaspi_size_filters({"Высота": _HEIGHT}, (("высот", 200.0),)) == ""
    assert kaspi_size_filters({}, (("высот", 50.0),)) == ""


async def test_sized_cards_lead_the_kaspi_pool():
    source = FacetSource()
    candidates, _ = await _collect_kaspi(
        source, "посудомоечная машина", None, None, None, (("высот", 50.0),)
    )

    assert [c.source_product_id for c in candidates] == ["cube0", "cube1", "big"]
    assert source.calls == [(0, ""), (0, ":D*Height:менее 75 см"), (1, ":D*Height:менее 75 см")]


async def test_without_a_size_limit_one_search_runs():
    source = FacetSource()
    await _collect_kaspi(source, "посудомоечная машина", None, None, None)
    assert source.calls == [(0, "")]
