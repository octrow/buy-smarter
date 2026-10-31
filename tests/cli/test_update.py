"""``update``: says the version and the channel, saves a channel switch, applies what is new."""

from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.services import update
from buysmarter.services.update import Status, UpdateSettings


def test_update_switches_the_channel_and_applies(monkeypatch):
    monkeypatch.setattr(update, "detect_install", lambda: "checkout")
    seen: list[UpdateSettings] = []

    def check(settings, install):
        seen.append(settings)
        return Status("0.11.1 (abc1234)", "def5678", True, True, "Доступно обновление: def5678.")

    monkeypatch.setattr(update, "check", check)
    monkeypatch.setattr(update, "apply", lambda settings, install: "Обновлено до def5678.")

    result = CliRunner().invoke(main.app, ["update", "--channel", "releases", "--no-auto"])

    assert result.exit_code == 0, result.output
    assert "канал: релизы" in result.output and "автообновление: выкл" in result.output
    assert "Обновлено до def5678." in result.output
    assert seen == [UpdateSettings(channel="releases", auto=False)]
    assert update.load_settings("checkout") == UpdateSettings(channel="releases", auto=False)


def test_update_check_only_does_not_apply(monkeypatch):
    monkeypatch.setattr(update, "detect_install", lambda: "frozen")
    status = Status("0.11.1", "0.12.0", True, False, "Доступна 0.12.0: url")
    monkeypatch.setattr(update, "check", lambda settings, install: status)
    monkeypatch.setattr(update, "apply", lambda *_: (_ for _ in ()).throw(AssertionError))

    result = CliRunner().invoke(main.app, ["update", "--check"])

    assert result.exit_code == 0 and "Доступна 0.12.0" in result.output


def test_update_refuses_the_code_channel_where_it_cannot_follow_it(monkeypatch):
    monkeypatch.setattr(update, "detect_install", lambda: "frozen")
    result = CliRunner().invoke(main.app, ["update", "--channel", "code"])
    assert result.exit_code != 0
