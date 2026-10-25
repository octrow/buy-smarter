"""What each capability did for a query and who decided it, in words (phase 3, decisions 13, 14).

``capability_states`` reads the registry against the resolved profile once; the GUI's
switches, the "Как оценивали" line of every render and the API field all take their wording
from here, so one source is never named two ways.
"""

from typing import Literal

from pydantic import BaseModel

from buysmarter.capabilities.base import Capability
from buysmarter.knowledge.profile import ResolvedProfile, SetBy
from buysmarter.ranking.models import QueryConstraints

# Who set the automatic value: after "включено"/"выключено" (instrumental).
_SET_BY_IN_FULL: dict[SetBy, str] = {"learned": "обучением", "jev": "Jev", "fallback": "правилом"}
# The same, short, for the "Как оценивали" line.
_SET_BY_SHORT: dict[SetBy, str] = {"learned": "обучение", "jev": "Jev", "fallback": "правило"}
_REQUEST_SOURCE = "запрос"
_BY_USER_ON = "включено вами"
_BY_USER_OFF = "выключено вами"
_NOT_ON = "не включено"
_OFF = "выключено"
_NO_DATA = ", но нет данных"
# The category's switches, in the app's report and «Категории» and in ``buysmarter category``.
TOGGLES_TITLE = "Как оцениваем эту категорию"
CHANGED_NOTE = "Изменения сработают при следующем поиске."


class CapabilityState(BaseModel):
    """One capability for one query: its switch, whether it works, and who decided.

    ``auto`` is the switch without the user's choice, ``on`` the effective one;
    ``active`` is true when the capability really takes part (a switched-on composition
    without an ingredient book does not).
    """

    name: str
    label: str
    level: Literal["category", "request"]
    on: bool
    active: bool
    auto: bool
    set_by: SetBy | None = None
    user: bool | None = None
    default_on: bool = False


def _without_user(profile: ResolvedProfile) -> ResolvedProfile:
    """The profile as automatic writers left it: no user choice, no user-only entries."""
    kept = {
        name: s.model_copy(update={"user": None})
        for name, s in profile.capabilities.items()
        if s.set_by is not None
    }
    return profile.model_copy(update={"capabilities": kept})


def capability_states(
    registry: dict[str, Capability], profile: ResolvedProfile, constraints: QueryConstraints
) -> list[CapabilityState]:
    """Every registry capability's state for this query, in registry order."""
    automatic = _without_user(profile)
    return [_state(cap, profile, automatic, constraints) for cap in registry.values()]


def _state(
    cap: Capability,
    profile: ResolvedProfile,
    automatic: ResolvedProfile,
    constraints: QueryConstraints,
) -> CapabilityState:
    """One capability: the effective setting against the one automatic writers left."""
    setting = cap.setting(profile, constraints)
    auto = cap.setting(automatic, constraints)
    stored = profile.capabilities.get(cap.name)
    user = stored.user if stored is not None else None
    shown = auto or setting
    automatic_on = stored.enabled if stored and stored.set_by else auto is not None
    params = shown.params if shown is not None else (stored.params if stored else {})
    set_by = shown.set_by if shown is not None else (stored.set_by if stored else None)
    return CapabilityState(
        name=cap.name,
        label=cap.label(params),
        level=cap.level,
        on=automatic_on if user is None else user,
        active=setting is not None,
        auto=automatic_on,
        set_by=set_by,
        user=user,
        default_on=cap.default_on,
    )


def toggle_text(state: CapabilityState) -> str:
    """The switch's caption: the name, the state and who set it ("Состав — включено вами")."""
    if state.user is not None:
        how = _BY_USER_ON if state.user else _BY_USER_OFF
    elif state.set_by is None:
        how = _NOT_ON
    else:
        how = f"{'включено' if state.on else 'выключено'} {_SET_BY_IN_FULL[state.set_by]}"
    no_data = state.on and not state.active
    return f"{state.label} — {how}{_NO_DATA if no_data else ''}"


ASSESSED_PREFIX = "Как оценивали: "


def _source_short(state: CapabilityState) -> str:
    """Who decided, short: the user, the learning, Jev, a rule, or the request itself."""
    if state.user is not None:
        return _BY_USER_ON if state.user else _BY_USER_OFF
    if state.set_by == "fallback" and state.level == "request":
        return _REQUEST_SOURCE
    return _SET_BY_SHORT[state.set_by] if state.set_by else ""


def _worth_saying(state: CapabilityState) -> bool:
    """Took part, or the user switched it off; a default-on one only once somebody decided."""
    if state.default_on and state.user is None:
        return not state.on or state.set_by != "fallback"
    return state.active or state.user is False


def _entry(state: CapabilityState) -> str:
    source = _source_short(state)
    if state.default_on and state.user is None and not state.on:
        source = f"{_OFF}: {source}"
    return f"{state.label[:1].lower()}{state.label[1:]} — {source}"


def assessed_entries(states: list[CapabilityState]) -> list[str]:
    """ "состав — обучение" for each capability that took part or that the user switched off."""
    return [_entry(state) for state in states if _worth_saying(state)]


def assessed_line(entries: list[str]) -> str | None:
    """The "Как оценивали: …" line every render shows, or ``None`` when nothing took part."""
    return f"{ASSESSED_PREFIX}{'; '.join(entries)}." if entries else None
