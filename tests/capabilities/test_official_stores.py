"""Official stores: brand sites as extra candidates, a category capability on by default."""

from typing import Any

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import activate, read_query
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import official_stores
from buysmarter.services.official_stores import StoreOffer

_BOOK = BrandBook(tiers={"Askona": "A"}, official_stores={"Askona": "https://s.kz"})
_OFFERS = [
    StoreOffer(name="Матрас Comfort 140x200", url="https://s.kz/1", price=100),
    StoreOffer(name="Матрас Comfort 160x200", url="https://s.kz/2", price=200),
]


def _profile(**settings: CapabilitySetting) -> ResolvedProfile:
    return ResolvedProfile(slug="матрас", brand_book=_BOOK, capabilities=settings)


def _active(profile: ResolvedProfile, query: str = "матрас 160х200"):
    parsed = QueryConstraints(search_phrase=query, product_type="матрас")
    constraints = read_query(REGISTRY, parsed, query=query, use_llm=False)
    return constraints, activate(REGISTRY, profile, constraints, query=query, use_llm=False)


def _store(monkeypatch) -> None:
    async def fetch(client: Any, url: str) -> list[StoreOffer]:
        return _OFFERS

    monkeypatch.setattr(official_stores, "fetch_store_offers", fetch)


def test_it_is_a_named_category_switch_without_a_jev_question():
    cap = REGISTRY["official_stores"]

    assert (cap.title, cap.level, cap.jev_question) == ("Официальные магазины", "category", None)


async def test_the_store_offers_pass_the_shared_listing_check(monkeypatch):
    _store(monkeypatch)
    _, active = _active(_profile())

    found = await active.candidates()

    assert [c.title for c in found] == ["Askona Матрас Comfort 160x200"]


async def test_switched_off_by_learning_or_the_user_it_brings_nothing(monkeypatch):
    _store(monkeypatch)
    learned_off = CapabilitySetting(enabled=False, set_by="learned")
    user_off = CapabilitySetting(set_by=None, user=False)

    for setting in (learned_off, user_off):
        _, active = _active(_profile(official_stores=setting))
        assert await active.candidates() == []


async def test_the_users_on_outranks_learning(monkeypatch):
    _store(monkeypatch)
    on = CapabilitySetting(enabled=False, set_by="learned", user=True)

    _, active = _active(_profile(official_stores=on))

    assert len(await active.candidates()) == 1


async def test_a_category_without_a_brand_book_has_no_offers(monkeypatch):
    _store(monkeypatch)
    _, active = _active(ResolvedProfile(slug="матрас"))

    assert await active.candidates() == []
