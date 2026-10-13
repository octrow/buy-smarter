"""Adapter tests: structured parse, retry-once, cache hit, stable key. No network."""

from types import SimpleNamespace

import httpx
import openai
import pytest
from claude_cli import ClaudeCliError, ClaudeResult
from pydantic import BaseModel

from buysmarter.config import Settings
from buysmarter.llm import adapter
from buysmarter.llm.cache import DictCacheStore, cache_key_for, set_store

_REQ = httpx.Request("POST", "https://api.test/v1/chat/completions")


class Product(BaseModel):
    name: str
    price: int


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        deepseek_api_key="test-key",
        default_llm_model="deepseek-chat",
        prompt_version="v1",
    )


def _resp(content: str):
    """Minimal stand-in for an openai ChatCompletion response."""
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _FakeCompletions:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


@pytest.fixture(autouse=True)
def _dict_store():
    set_store(DictCacheStore())


def _patch_client(monkeypatch, responses: list) -> _FakeCompletions:
    completions = _FakeCompletions(responses)
    monkeypatch.setattr(adapter, "AsyncOpenAI", lambda **kw: _FakeClient(completions))
    return completions


async def test_structured_parse_success(monkeypatch) -> None:
    completions = _patch_client(monkeypatch, [_resp('{"name": "irrigator", "price": 40000}')])

    result = await adapter.generate("find one", Product, settings=_settings())

    assert isinstance(result, Product)
    assert result.name == "irrigator"
    assert result.price == 40000
    assert len(completions.calls) == 1


async def test_retry_once_on_invalid_then_success(monkeypatch) -> None:
    completions = _patch_client(
        monkeypatch,
        [_resp('{"wrong": true}'), _resp('{"name": "brush", "price": 3000}')],
    )

    result = await adapter.generate("find one", Product, settings=_settings())

    assert result.name == "brush"
    assert len(completions.calls) == 2  # original + one retry


async def test_cache_hit_skips_provider_call(monkeypatch) -> None:
    # Only one response available: a second provider call would raise IndexError.
    completions = _patch_client(monkeypatch, [_resp('{"name": "x", "price": 5}')])
    settings = _settings()

    first = await adapter.generate("same prompt", Product, settings=settings)
    second = await adapter.generate("same prompt", Product, settings=settings)

    assert first.model_dump() == second.model_dump()
    assert len(completions.calls) == 1  # second call served from cache


def _cli_settings() -> Settings:
    return Settings(_env_file=None, default_llm_model="sonnet", prompt_version="v1")


def _patch_cli(monkeypatch, results: list[str], cost: float = 0.001) -> list[str]:
    """Fake claude_cli.arun_claude; records prompts, returns canned ClaudeResult."""
    prompts: list[str] = []
    remaining = list(results)

    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        prompts.append(prompt)
        return ClaudeResult(text=remaining.pop(0), cost_usd=cost)

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)
    return prompts


def test_resolve_provider_picks_claude_cli_for_sonnet() -> None:
    provider = adapter._resolve_provider("sonnet", _cli_settings())
    assert provider.is_cli
    assert provider.name == "claude-cli"


def test_resolve_provider_falls_back_to_cli_without_api_key(monkeypatch) -> None:
    # No deepseek key set, but the `claude` binary exists.
    monkeypatch.setattr(adapter, "claude_available", lambda: True)
    settings = Settings(_env_file=None, default_llm_model="deepseek-chat")
    provider = adapter._resolve_provider("deepseek-chat", settings)
    assert provider.is_cli


async def test_generate_via_claude_cli_end_to_end(monkeypatch) -> None:
    prompts = _patch_cli(monkeypatch, ['Sure: {"name": "irrigator", "price": 39990}'])

    result = await adapter.generate("find one", Product, settings=_cli_settings())

    assert isinstance(result, Product)
    assert result.name == "irrigator"
    assert result.price == 39990
    assert len(prompts) == 1  # single CLI call, prose-wrapped JSON extracted


async def test_generate_via_claude_cli_retries_once(monkeypatch) -> None:
    prompts = _patch_cli(monkeypatch, ['{"wrong": true}', '{"name": "brush", "price": 3000}'])

    result = await adapter.generate("find one", Product, settings=_cli_settings())

    assert result.name == "brush"
    assert len(prompts) == 2  # original + one retry


async def test_claude_cli_cache_hit_skips_call(monkeypatch) -> None:
    prompts = _patch_cli(monkeypatch, ['{"name": "x", "price": 5}'])
    settings = _cli_settings()

    first = await adapter.generate("same prompt", Product, settings=settings)
    second = await adapter.generate("same prompt", Product, settings=settings)

    assert first.model_dump() == second.model_dump()
    assert len(prompts) == 1  # second served from cache, no CLI call


async def test_generate_threads_cli_tools_and_timeout_to_run_claude(monkeypatch) -> None:
    calls: list[dict] = []

    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        calls.append(kwargs)
        return ClaudeResult(text='{"name": "irrigator", "price": 1}', cost_usd=0.0)

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)

    await adapter.generate(
        "find one",
        Product,
        settings=_cli_settings(),
        cli_tools="WebSearch",
        cli_timeout=600,
    )

    assert calls[0]["tools"] == "WebSearch"
    assert calls[0]["timeout"] == 600


async def test_generate_default_cli_tools_omits_timeout_kwarg(monkeypatch) -> None:
    calls: list[dict] = []

    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        calls.append(kwargs)
        return ClaudeResult(text='{"name": "irrigator", "price": 1}', cost_usd=0.0)

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)

    await adapter.generate("find one", Product, settings=_cli_settings())

    assert calls[0]["tools"] == ""
    assert "timeout" not in calls[0]


async def test_cache_key_stable_and_scoped() -> None:
    a = cache_key_for("deepseek-chat", "v1", "hello   world", Product)
    b = cache_key_for("deepseek-chat", "v1", "hello world", Product)
    assert a == b  # whitespace normalized

    diff_model = cache_key_for("gpt-4o", "v1", "hello world", Product)
    diff_version = cache_key_for("deepseek-chat", "v2", "hello world", Product)
    assert a != diff_model
    assert a != diff_version


async def test_cache_key_cli_tools_backward_compatible_and_scoped() -> None:
    """An empty cli_tools (the default, every pre-existing call site) must
    keep the exact pre-cli_tools key — no cache migration needed. A non-empty
    cli_tools (web-search grounding) must get a distinct key so it never
    shadows/is-shadowed-by a tools-off cache row for the identical prompt."""
    old_signature_key = cache_key_for("deepseek-chat", "v1", "hello world", Product)
    explicit_empty = cache_key_for("deepseek-chat", "v1", "hello world", Product, cli_tools="")
    assert old_signature_key == explicit_empty  # backward-compatible default

    web_search_key = cache_key_for(
        "deepseek-chat", "v1", "hello world", Product, cli_tools="WebSearch"
    )
    assert web_search_key != old_signature_key


async def test_cli_second_invalid_response_raises_llm_error(monkeypatch) -> None:
    """Fail-open callers catch LlmError only; a doubly-bad reply must not leak ValidationError."""
    _patch_cli(monkeypatch, ['{"wrong": true}', "not json at all"])

    with pytest.raises(adapter.LlmError):
        await adapter.generate("find one", Product, settings=_cli_settings())


async def test_openai_second_invalid_response_raises_llm_error(monkeypatch) -> None:
    _patch_client(monkeypatch, [_resp('{"wrong": true}'), _resp("garbage")])

    with pytest.raises(adapter.LlmError):
        await adapter.generate("find one", Product, settings=_settings())


async def test_stale_cached_row_is_treated_as_miss(monkeypatch) -> None:
    """A cached payload that no longer fits the schema must regenerate, not crash."""
    settings = _cli_settings()
    key = cache_key_for("sonnet", "v1", "same prompt", Product)
    await adapter.store_cached(
        adapter.LlmCache(
            provider="claude-cli",
            model="sonnet",
            prompt_version="v1",
            prompt_hash="h",
            prompt_text="p",
            input_hash="i",
            cache_key=key,
            response_text="{}",
            structured_json={"old_field": 1},
        )
    )
    prompts = _patch_cli(monkeypatch, ['{"name": "x", "price": 5}'])

    result = await adapter.generate("same prompt", Product, settings=settings)

    assert result.name == "x"
    assert len(prompts) == 1


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: openai.RateLimitError(
            "slow down", response=httpx.Response(429, request=_REQ), body=None
        ),
        lambda: openai.APITimeoutError(request=_REQ),
        lambda: openai.APIConnectionError(request=_REQ),
    ],
)
async def test_openai_sdk_errors_wrapped_in_llm_error(monkeypatch, exc_factory) -> None:
    exc = exc_factory()
    _patch_client(monkeypatch, [exc])

    with pytest.raises(adapter.LlmError) as info:
        await adapter.generate("find one", Product, settings=_settings())

    assert info.value.__cause__ is exc


async def test_run_cli_falls_back_to_free_combo(monkeypatch) -> None:
    models: list[str] = []

    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        models.append(model)
        if len(models) == 1:
            raise ClaudeCliError("claude timed out after 300s")
        return ClaudeResult(text="ok", cost_usd=0.0)

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)
    monkeypatch.delenv("BUYSMARTER_CLI_FALLBACK_COMBO", raising=False)
    assert await adapter._run_cli("p", "sonnet") == ("ok", 0.0, "sub-first")
    assert models == ["sonnet", "sub-first"]


async def test_run_cli_raises_llm_error_when_fallback_also_fails(monkeypatch) -> None:
    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        raise ClaudeCliError("gateway down")

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)
    monkeypatch.setenv("BUYSMARTER_CLI_FALLBACK_COMBO", "free-stack")
    with pytest.raises(adapter.LlmError, match="combo=free-stack"):
        await adapter._run_cli("p", "haiku")


def _patch_cli_models(monkeypatch, script: list) -> list[str]:
    """Fake arun_claude driven by a script of texts/exceptions; records the model of each call."""
    models: list[str] = []
    remaining = list(script)

    async def _fake_run(prompt: str, *, model: str = "sonnet", **kwargs):
        models.append(model)
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return ClaudeResult(text=item, cost_usd=0.0)

    monkeypatch.setattr(adapter, "arun_claude", _fake_run)
    monkeypatch.delenv("BUYSMARTER_CLI_FALLBACK_COMBO", raising=False)
    return models


async def test_validation_retry_goes_straight_to_the_combo_that_answered(monkeypatch) -> None:
    models = _patch_cli_models(
        monkeypatch,
        [
            ClaudeCliError("claude timed out after 300s"),
            '{"wrong": 1}',
            '{"name": "a", "price": 1}',
        ],
    )
    result = await adapter.generate("find one", Product, settings=_cli_settings())
    assert result.name == "a"
    assert models == ["sonnet", "sub-first", "sub-first"]  # no second walk of the wedged sonnet


async def test_fallback_answer_is_not_cached_under_the_primary_model(monkeypatch) -> None:
    models = _patch_cli_models(
        monkeypatch,
        [
            ClaudeCliError("claude timed out after 300s"),
            '{"name": "degraded", "price": 1}',
            '{"name": "primary", "price": 2}',
        ],
    )
    settings = _cli_settings()
    await adapter.generate("same", Product, settings=settings)
    second = await adapter.generate("same", Product, settings=settings)
    assert second.name == "primary"
    assert models == ["sonnet", "sub-first", "sonnet"]


async def test_not_logged_in_skips_the_fallback(monkeypatch) -> None:
    from claude_cli import NotLoggedInError

    models = _patch_cli_models(monkeypatch, [NotLoggedInError("Not logged in")])
    with pytest.raises(adapter.LlmError):
        await adapter._run_cli("p", "sonnet")
    assert models == ["sonnet"]  # same login behind every combo: a retry cannot help


@pytest.mark.parametrize(
    ("model", "fallback"),
    [("haiku", "sub-first"), ("sonnet", "sub-first"), ("opus", "sub-first")],
)
async def test_fallback_is_a_different_combo_per_model(monkeypatch, model, fallback) -> None:
    models = _patch_cli_models(monkeypatch, [ClaudeCliError("claude timed out"), "ok"])
    assert await adapter._run_cli("p", model) == ("ok", 0.0, fallback)
    assert models == [model, fallback]


def test_combo_name_reaches_the_cli_as_is() -> None:
    assert adapter._cli_model("sub-first") == "sub-first"
    assert adapter._cli_model("deepseek-chat") == "sonnet"
