"""`buysmarter llm set-key|clear-key|status`: the key goes to the keyring and is never printed."""

import keyring
import pytest
from keyring.backends.fail import Keyring as NoBackend
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.llm import adapter, keys
from buysmarter.services import doctor

SECRET = "sk-proj-SECRETKEY123456"


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for field in keys.KEY_FIELDS.values():
        monkeypatch.setenv(field.upper(), "")


def _run(*args: str, input: str | None = None):
    return CliRunner().invoke(main.app, ["llm", *args], input=input)


def _no_key_chars(output: str) -> bool:
    return "SECRETKEY" not in output and "sk-proj" not in output


def test_set_key_prompts_hidden_and_stores_it_in_the_keyring(fake_keyring):
    result = _run("set-key", "openai", input=f"{SECRET}\n")
    assert result.exit_code == 0, result.output
    assert fake_keyring.secrets[("buysmarter", "openai")] == SECRET
    assert _no_key_chars(result.output)
    assert "openai" in result.output


def test_status_shows_provider_and_source_but_no_key_characters():
    keys.set_key("openai", SECRET)
    result = _run("status")
    assert result.exit_code == 0
    assert "openai (keyring)" in result.output
    assert _no_key_chars(result.output)


def test_status_without_any_route_says_how_to_add_one():
    result = _run("status")
    assert result.exit_code == 0
    assert "none" in result.output and "llm set-key" in result.output


def test_clear_key_removes_it():
    keys.set_key("openai", SECRET)
    result = _run("clear-key", "openai")
    assert result.exit_code == 0
    assert keys.get_key("openai") is None
    assert "LLM: none" in result.output


def test_set_key_says_when_an_env_key_still_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1234567890")
    result = _run("set-key", "openai", input=f"{SECRET}\n")
    assert "LLM: openai (environment)" in result.output


def test_an_unknown_provider_is_a_usage_error():
    result = _run("set-key", "mistral", input="x\n")
    assert result.exit_code == 2
    assert "openai" in result.output  # lists the valid ones


def test_set_key_without_a_keyring_fails_naming_the_env_var():
    keyring.set_keyring(NoBackend())
    result = _run("set-key", "gemini", input="AIza-test-key-123456\n")
    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.output


@pytest.mark.parametrize(
    ("setup", "ok", "detail"),
    [
        (
            lambda mp: mp.setenv("DEEPSEEK_API_KEY", "sk-env-123456789"),
            True,
            "deepseek (environment)",
        ),
        (lambda mp: keys.set_key("anthropic", SECRET), True, "anthropic (keyring)"),
        (lambda mp: mp.setattr(adapter, "claude_available", lambda: True), True, "claude CLI"),
        (lambda mp: None, False, ""),
    ],
)
def test_doctor_llm_row_shows_the_route_and_its_source(monkeypatch, setup, ok, detail):
    setup(monkeypatch)
    row = doctor.llm_check()
    assert (row.name, row.ok, row.detail) == ("LLM", ok, detail)
    assert "SECRETKEY" not in f"{row.detail} {row.hint}"
    if not ok:
        assert "llm set-key" in row.hint
