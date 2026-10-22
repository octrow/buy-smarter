"""The phase-4 hooks: ``sources``, ``candidates`` and the ``drops_barred`` attribute."""

from typing import ClassVar

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.capabilities.flow import activate, pick_sources
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints

_ON = CapabilitySetting(set_by="fallback")


class _DropsOlxWhenOff(Capability):
    name = "drops_olx"

    def setting(self, profile: ResolvedProfile, constraints: QueryConstraints) -> None:
        return None  # never active: ``sources`` still runs

    def sources(self, ctx: CapabilityContext, selected: list[str]) -> list[str]:
        return [s for s in selected if s != "olx"]


class _Rejects(Capability):
    name = "rejects"
    drops_barred: ClassVar[bool] = True

    def setting(self, profile: ResolvedProfile, constraints: QueryConstraints) -> CapabilitySetting:
        return _ON

    def reject_listing(self, ctx: CapabilityContext, text: str) -> str | None:
        return "other size" if "195" in text else None


class _Offers(Capability):
    name = "offers"

    def setting(self, profile: ResolvedProfile, constraints: QueryConstraints) -> CapabilitySetting:
        return _ON

    async def candidates(self, ctx: CapabilityContext) -> list[Candidate]:
        assert ctx.reject_listing is not None
        titles = ("Шина 205/55 R16", "Шина 195/65 R15")
        return [
            Candidate(source="store", source_product_id=t, title=t, price=1)
            for t in titles
            if ctx.reject_listing(t) is None
        ]


def _registry(*caps: Capability) -> dict[str, Capability]:
    return {cap.name: cap for cap in caps}


def test_sources_runs_on_every_capability_switched_on_or_not():
    registry = _registry(_DropsOlxWhenOff())

    picked = pick_sources(registry, ResolvedProfile(slug="x"), QueryConstraints(), ["kaspi", "olx"])

    assert picked == ["kaspi"]


async def test_candidates_see_every_active_capability_listing_check():
    active = activate(
        _registry(_Rejects(), _Offers()),
        ResolvedProfile(slug="x"),
        QueryConstraints(),
        query="",
        use_llm=False,
    )

    extra = await active.candidates()

    assert [c.title for c in extra] == ["Шина 205/55 R16"]


def test_drops_barred_is_on_when_an_active_capability_asks_for_it():
    profile, constraints = ResolvedProfile(slug="x"), QueryConstraints()

    on = activate(_registry(_Rejects()), profile, constraints, query="", use_llm=False)
    off = activate(_registry(_Offers()), profile, constraints, query="", use_llm=False)

    assert (on.drops_barred, off.drops_barred) == (True, False)
