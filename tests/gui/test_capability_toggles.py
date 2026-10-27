"""«Как оцениваем эту категорию»: the user's switches write the request's own profile."""

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.provenance import capability_states
from buysmarter.gui.capability_toggles import RESET_TEXT
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.knowledge.store import load_knowledge, update_knowledge

_RUN = SearchRun("крем от акне", True, {"kaspi": "done"})
_WAIT_MS = 3000
_BOOK = IngredientBook(substances=[ActiveSubstance(canonical="адапален")]).model_dump(mode="json")
_CATEGORY = "крем от акне"
_PARENT = "косметика"


def _result(base, *, user=None, found=None):
    constraints = base.constraints.model_copy(
        update={"product_type": _CATEGORY, "search_phrase": _CATEGORY}
    )
    learned = CapabilitySetting(params={"ingredient_book": _BOOK}, user=user)
    profile = ResolvedProfile(slug="x", capabilities={"composition": learned})
    states = capability_states(REGISTRY, profile, constraints)
    return base.model_copy(
        update={"constraints": constraints, "capabilities": states, "profile_found": found}
    )


def _screen(qtbot, runner, result):
    screen = ReportScreen(runner)
    qtbot.addWidget(screen)
    screen.show_result(result, _RUN)
    return screen


def _stored(qtbot, runner, slug):
    job = runner.submit(load_knowledge(slug))
    with qtbot.waitSignal(job.finished, timeout=_WAIT_MS) as done:
        done.connect(job.failed)
    return done.args[0]


def _wait_for_note(qtbot, screen):
    qtbot.waitUntil(
        lambda: screen.toggles.rerun_button.isVisibleTo(screen.toggles), timeout=_WAIT_MS
    )


def test_only_category_level_capabilities_are_listed_with_their_source(qtbot, runner, result):
    screen = _screen(qtbot, runner, _result(result))

    assert set(screen.toggles.checkboxes) == {
        "benchmark",
        "composition",
        "used_market",
        "official_stores",
        "style",
    }
    market = screen.toggles.checkboxes["used_market"]
    assert market.text() == "Рынок б/у — включено правилом"
    assert market.isChecked()
    assert screen.toggles.checkboxes["official_stores"].text() == (
        "Официальные магазины — включено правилом"
    )
    box = screen.toggles.checkboxes["composition"]
    assert box.text() == "Состав — включено обучением"
    assert box.isChecked()
    assert box.accessibleName() == box.text()
    assert not screen.toggles.checkboxes["benchmark"].isChecked()


def test_the_group_is_on_every_report(qtbot, runner, result):
    screen = _screen(qtbot, runner, result)  # a report with no knowledge flag at all

    assert screen.toggles is not None
    assert screen.toggles.title() == "Как оцениваем эту категорию"


def test_reset_is_disabled_without_a_user_choice(qtbot, runner, result):
    screen = _screen(qtbot, runner, _result(result))

    assert screen.toggles.resets["composition"].text() == RESET_TEXT
    assert not screen.toggles.resets["composition"].isEnabled()


def test_switching_off_writes_the_users_choice_and_offers_a_rerun(qtbot, runner, result):
    screen = _screen(qtbot, runner, _result(result))
    assert screen.toggles.rerun_button.isHidden()

    screen.toggles.checkboxes["composition"].click()
    _wait_for_note(qtbot, screen)

    box = screen.toggles.checkboxes["composition"]
    assert not box.isChecked()
    assert box.text() == "Состав — выключено вами"
    assert screen.toggles.resets["composition"].isEnabled()
    stored = _stored(qtbot, runner, _CATEGORY)
    assert stored["capabilities"]["composition"]["user"] is False
    with qtbot.waitSignal(screen.rerun_requested, timeout=_WAIT_MS):
        screen.toggles.rerun_button.click()


def test_reset_returns_to_the_automatic_value(qtbot, runner, result):
    screen = _screen(qtbot, runner, _result(result, user=False))
    reset = screen.toggles.resets["composition"]
    assert reset.isEnabled()
    reset.click()
    _wait_for_note(qtbot, screen)

    box = screen.toggles.checkboxes["composition"]
    assert box.isChecked()
    assert box.text() == "Состав — включено обучением"
    assert not reset.isEnabled()


@pytest.mark.parametrize("found", [_PARENT])
def test_a_related_profile_is_neither_the_parent_nor_touched(qtbot, runner, result, found):
    parent_record = {"capabilities": {"composition": {"enabled": True, "set_by": "learned"}}}
    job = runner.submit(update_knowledge(found, parent_record, "test"))
    with qtbot.waitSignal(job.finished, timeout=_WAIT_MS):
        pass
    before = _stored(qtbot, runner, found)
    screen = _screen(qtbot, runner, _result(result, found=found))

    screen.toggles.checkboxes["composition"].click()
    _wait_for_note(qtbot, screen)

    own = _stored(qtbot, runner, _CATEGORY)
    assert "parent" not in own  # as `category set`: other creams must not read its books (В4)
    assert own["capabilities"]["composition"]["user"] is False
    assert _stored(qtbot, runner, found) == before


def test_without_a_core_runner_the_switches_are_disabled(qtbot, result):
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show_result(_result(result), _RUN)

    assert not screen.toggles.checkboxes["composition"].isEnabled()
