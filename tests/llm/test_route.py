"""LLM route: env key > stored key for the chosen provider > claude CLI > no LLM."""

import pytest

from buysmarter.config import Settings
from buysmarter.llm import adapter, keys
from buysmarter.llm.adapter import DEFAULT_MODEL_BY_PROVIDER, resolve_route


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, default_llm_model="sonnet", **kw)


@pytest.mark.parametrize(
    ("env", "stored", "cli", "expected"),
    [
        ({}, None, False, None),
        ({}, None, True, ("claude-cli", "cli")),
        ({}, ("openai", "sk-stored"), False, ("openai", "keyring")),
        ({}, ("openai", "sk-stored"), True, ("openai", "keyring")),
        ({"openai_api_key": "sk-env"}, None, False, ("openai", "env")),
        ({"openai_api_key": "sk-env"}, None, True, ("openai", "env")),
        ({"openai_api_key": "sk-env"}, ("openai", "sk-stored"), True, ("openai", "env")),
        # the chosen provider's env key wins over another provider's
        ({"openai_api_key": "e", "gemini_api_key": "g"}, ("gemini", "s"), False, ("gemini", "env")),
        # any env key beats a stored key for another provider
        ({"deepseek_api_key": "d"}, ("openai", "s"), False, ("deepseek", "env")),
    ],
)
def test_route_order(env, stored, cli, expected):
    route = resolve_route(_settings(**env), stored, cli)
    assert ((route.provider.name, route.source) if route else None) == expected


def test_the_env_key_or_the_stored_key_is_what_the_route_carries():
    assert resolve_route(_settings(openai_api_key="sk-env"), None, False).api_key == "sk-env"
    assert resolve_route(_settings(), ("anthropic", "sk-ant"), False).api_key == "sk-ant"


def test_among_env_keys_the_default_models_provider_comes_first():
    settings = Settings(
        _env_file=None, default_llm_model="deepseek-chat", openai_api_key="o", deepseek_api_key="d"
    )
    assert resolve_route(settings, None, False).provider.name == "deepseek"


def test_the_route_never_shows_its_key():
    route = resolve_route(_settings(), ("openai", "sk-secret-123"), False)
    assert "sk-secret-123" not in repr(route)


@pytest.mark.parametrize(
    ("requested", "default", "expected"),
    [
        ("sonnet", "sonnet", DEFAULT_MODEL_BY_PROVIDER["openai"]),  # a CLI alias
        ("free-stack", "sonnet", DEFAULT_MODEL_BY_PROVIDER["openai"]),  # a combo
        ("gpt-4.1-mini", "sonnet", "gpt-4.1-mini"),  # already this provider's model
        ("sonnet", "gpt-4.1", "gpt-4.1"),  # the configured default is this provider's
        ("deepseek-chat", "sonnet", DEFAULT_MODEL_BY_PROVIDER["openai"]),  # another provider's
    ],
)
def test_a_key_route_picks_a_model_of_its_own_provider(requested, default, expected):
    settings = Settings(_env_file=None, default_llm_model=default)
    route = resolve_route(settings, ("openai", "sk"), False)
    assert adapter.route_model(route, requested, settings) == expected


def test_the_cli_route_keeps_the_requested_model():
    settings = _settings()
    route = resolve_route(settings, None, True)
    assert adapter.route_model(route, "free-stack", settings) == "free-stack"


def test_every_provider_has_a_default_model_of_its_own():
    for name, model in DEFAULT_MODEL_BY_PROVIDER.items():
        assert adapter.provider_of(model).name == name
    assert set(DEFAULT_MODEL_BY_PROVIDER) == set(keys.KEY_FIELDS)


def test_current_route_reads_the_keyring_and_the_cli(monkeypatch):
    monkeypatch.setattr(adapter, "claude_available", lambda: False)
    assert adapter.current_route(_settings()) is None
    keys.set_key("gemini", "AIza-test")
    route = adapter.current_route(_settings())
    assert (route.provider.name, route.source, route.api_key) == ("gemini", "keyring", "AIza-test")
