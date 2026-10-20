"""Single-entry multi-provider LLM adapter with structured output + caching.

The route (``resolve_route``) is: an API key from the environment, then the key the user stored
in the OS keyring for the chosen provider (``llm/keys.py``), then the local ``claude`` CLI run
through OmniRoute, then no LLM (``LlmConfigError``; callers fall back to their non-LLM path).
API providers (OpenAI / DeepSeek / Gemini / Anthropic) go through the ``openai`` SDK's
``AsyncOpenAI`` client pointed at each provider's OpenAI-compatible endpoint. Responses are
validated against a Pydantic model (retry once on failure) and cached by default.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from claude_cli import MODELS as CLAUDE_CLI_MODELS
from claude_cli import (
    ClaudeCliError,
    NotLoggedInError,
    arun_claude,
    claude_available,
    extract_json,
    normalize_prompt,
)
from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from buysmarter import telemetry
from buysmarter.config import Settings, get_settings
from buysmarter.llm import keys
from buysmarter.llm.cache import cache_key_for, get_cached, hash_text, store_cached
from buysmarter.llm.models import LlmCache

logger = logging.getLogger(__name__)

# OmniRoute combo retried once when the primary `omniroute run claude` call fails. It
# must differ from the alias's own combo (gateway mappings: haiku→free-stack,
# sonnet→free-stack, opus→hard), or the retry re-walks the same wedged targets.
# The env var, when set, overrides it for every model.
_FALLBACK_COMBO_ENV = "BUYSMARTER_CLI_FALLBACK_COMBO"
_FALLBACK_COMBOS = {"haiku": "sub-first", "sonnet": "sub-first", "opus": "sub-first"}
_DEFAULT_FALLBACK_COMBO = "sub-first"
# OmniRoute combos a caller may name as the model; they reach `--model` as-is.
CLI_COMBOS = frozenset({"sub-first", "free-stack", "hard"})

# Model policy (per docs/PLAN.md "LLM-слой") — every call site must pick its
# model consciously, no accidental defaults:
#   haiku  -> extraction / classification (query parsing, cheap structured
#             pulls where the schema is small and the task is mechanical)
#   sonnet -> summarization / normalization (spec normalization, review
#             summaries, general-purpose structured generation)
#   opus   -> deep analysis (brand/counterfeit judgment calls, anything that
#             needs real reasoning over noisy/ambiguous input)
# haiku and sonnet both resolve to the OmniRoute free-stack combo, so cheap call
# sites name "free-stack" directly; classifications go to Jev first (llm/jev.py).


class LlmError(Exception):
    """Base error for the LLM layer."""


class LlmConfigError(LlmError):
    """Raised when a provider is unknown or its API key is missing."""


@dataclass(frozen=True)
class Provider:
    """A registered LLM provider (OpenAI-compatible API, or the local CLI)."""

    name: str
    base_url: str | None  # None → OpenAI native default endpoint
    key_field: str
    supports_json_mode: bool
    is_cli: bool = False  # True → local `claude` CLI, not an HTTP API


# Model-name prefix → provider config. First matching prefix wins.
PROVIDERS: dict[str, Provider] = {
    "gpt": Provider("openai", None, "openai_api_key", True),
    "o1": Provider("openai", None, "openai_api_key", True),
    "o3": Provider("openai", None, "openai_api_key", True),
    "o4": Provider("openai", None, "openai_api_key", True),
    "deepseek": Provider("deepseek", "https://api.deepseek.com", "deepseek_api_key", True),
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini_api_key",
        True,
    ),
    # Anthropic's OpenAI-compat endpoint does not support response_format
    # json_object; we instruct JSON in the system prompt instead.
    "claude": Provider("anthropic", "https://api.anthropic.com/v1/", "anthropic_api_key", False),
}

# The primary provider: the local `claude` CLI (subscription, no API key).
CLAUDE_CLI = Provider("claude-cli", None, "", False, is_cli=True)


# The model a key route uses when the requested one is not that provider's (e.g. "sonnet").
DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-5.6-terra",
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-3.8-flash",
    "deepseek": "deepseek-chat",
}
_BY_NAME = {p.name: p for p in PROVIDERS.values()}
# OpenAI reasoning models (GPT-5, o-series) reject any temperature but the default.
_DEFAULT_TEMPERATURE_ONLY = ("gpt-5", "o1", "o3", "o4")


@dataclass(frozen=True)
class Route:
    """Where LLM calls go: a provider, where its key came from, and the key (never shown)."""

    provider: Provider
    source: Literal["env", "keyring", "cli"]
    api_key: str | None = field(default=None, repr=False)


def provider_of(model: str) -> Provider | None:
    """The API provider whose model-name prefix ``model`` has; None for CLI aliases/combos."""
    return next((p for prefix, p in PROVIDERS.items() if model.startswith(prefix)), None)


def _env_order(settings: Settings, chosen: str | None) -> list[str]:
    default = provider_of(settings.default_llm_model)
    first = [name for name in (chosen, default and default.name) if name]
    return list(dict.fromkeys([*first, *keys.KEY_FIELDS]))


def resolve_route(settings: Settings, stored: tuple[str, str] | None, cli: bool) -> Route | None:
    """Env key (the chosen provider's first) > ``stored`` (chosen provider, key) > CLI > None."""
    for name in _env_order(settings, stored[0] if stored else None):
        if env_key := getattr(settings, keys.KEY_FIELDS[name]):
            return Route(_BY_NAME[name], "env", env_key)
    if stored:
        return Route(_BY_NAME[stored[0]], "keyring", stored[1])
    return Route(CLAUDE_CLI, "cli") if cli else None


def current_route(settings: Settings) -> Route | None:
    """The route on this machine now: env, the OS keyring, the installed CLI."""
    return resolve_route(settings, keys.stored(), claude_available())


def route_model(route: Route, model: str, settings: Settings) -> str:
    """The model to ask ``route`` for: ``model`` if it is the route's, else a default of it."""
    if route.provider.is_cli:
        return model
    for candidate in (model, settings.default_llm_model):
        if provider_of(candidate) == route.provider:
            return candidate
    return DEFAULT_MODEL_BY_PROVIDER[route.provider.name]


def _resolve_provider(model: str, settings: Settings, cli_tools: str = "") -> tuple[Route, str]:
    """The route for this machine and the model to ask it for; raises without any route.

    ``cli_tools`` (web search) exist only on the CLI, so such a call prefers the CLI when installed.
    """
    route = current_route(settings)
    if route is None:
        raise LlmConfigError(
            "No LLM configured: set an API key (buysmarter llm set-key, or an env var "
            "such as OPENAI_API_KEY) or install the claude CLI"
        )
    if cli_tools and not route.provider.is_cli:
        if claude_available():
            route = Route(CLAUDE_CLI, "cli")
        else:
            logger.warning(
                "%s has no %s tools; answering without them", route.provider.name, cli_tools
            )
    return route, route_model(route, model, settings)


def _make_client(route: Route) -> AsyncOpenAI:
    if not route.api_key:
        raise LlmConfigError(f"No API key for {route.provider.name}")
    return AsyncOpenAI(api_key=route.api_key, base_url=route.provider.base_url)


def _system_prompt(response_model: type[BaseModel], supports_json_mode: bool) -> str:
    schema = json.dumps(response_model.model_json_schema())
    prompt = (
        "You are a precise data extraction assistant. Respond with a single JSON "
        f"object that conforms exactly to this JSON schema:\n{schema}"
    )
    if not supports_json_mode:
        prompt += "\nOutput only the JSON object, with no markdown fences or commentary."
    return prompt


async def _chat(
    client: AsyncOpenAI,
    model: str,
    temperature: float,
    messages: list[dict[str, str]],
    provider: Provider,
) -> ChatCompletion:
    kwargs: dict[str, Any] = {}
    if not model.startswith(_DEFAULT_TEMPERATURE_ONLY):
        kwargs["temperature"] = temperature
    if provider.supports_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        # **kwargs hides `stream`, so the overloads resolve to Any; no stream here.
        resp: ChatCompletion = await client.chat.completions.create(
            model=model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            **kwargs,
        )
        return resp
    except APIError as exc:
        raise LlmError(f"{provider.name} API call failed: {exc}") from exc


def _text_of(resp: ChatCompletion) -> str:
    return resp.choices[0].message.content or ""


def _build_row(
    key: str,
    provider: Provider,
    model: str,
    prompt_version: str,
    prompt: str,
    full_prompt: str,
    response_model: type[BaseModel],
    text: str,
    obj: BaseModel,
    resp: ChatCompletion,
    latency_ms: int,
) -> LlmCache:
    usage = getattr(resp, "usage", None)
    return LlmCache(
        provider=provider.name,
        model=model,
        prompt_version=prompt_version,
        prompt_hash=hash_text(normalize_prompt(prompt)),
        prompt_text=full_prompt,
        input_hash=hash_text(response_model.__name__),
        cache_key=key,
        response_text=text,
        structured_json=obj.model_dump(mode="json"),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        cost_usd=None,
        latency_ms=latency_ms,
    )


async def generate(
    prompt: str,
    response_model: type[BaseModel],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    use_cache: bool = True,
    settings: Settings | None = None,
    cli_tools: str = "",
    cli_timeout: int | None = None,
) -> BaseModel:
    """Generate a validated ``response_model`` instance for ``prompt``.

    Checks the cache first (unless ``use_cache`` is False). On a miss, calls the
    provider selected by the model-name prefix, validates the JSON response, and
    retries once feeding the validation error back before giving up.

    ``cli_tools``/``cli_timeout`` are CLI-only (web-search grounding, see
    ``knowledge.generate``): threaded through to ``claude_cli.arun_claude`` as
    its ``tools``/``timeout`` kwargs. API providers ignore both — there is no
    equivalent tool-use path wired up for the OpenAI-compatible branch.
    """
    settings = settings or get_settings()
    if model is None:
        model = settings.default_llm_model
        logger.info("llm model not set explicitly, defaulting to %s", model)
    route, model = _resolve_provider(model, settings, cli_tools)
    key = cache_key_for(model, settings.prompt_version, prompt, response_model, cli_tools=cli_tools)

    if use_cache:
        cached = await get_cached(key)
        if cached is not None:
            try:
                hit = response_model.model_validate(cached)
            except ValidationError as exc:
                # Schema drifted under an unchanged prompt_version: regenerate.
                logger.warning(
                    "llm cache row %s no longer fits schema (%r); regenerating", key[:8], exc
                )
            else:
                logger.info("llm cache hit: %s %s", model, key[:8])
                telemetry.LLM_CALLS.add(1, {"model": model, "cache": "hit"})
                return hit

    telemetry.LLM_CALLS.add(1, {"model": model, "cache": "miss"})
    if route.provider.is_cli:
        return await _generate_cli(
            prompt, response_model, model, key, use_cache, settings, cli_tools, cli_timeout
        )
    return await _generate_openai(
        prompt, response_model, model, route, temperature, key, use_cache, settings
    )


async def _generate_openai(
    prompt: str,
    response_model: type[BaseModel],
    model: str,
    route: Route,
    temperature: float,
    key: str,
    use_cache: bool,
    settings: Settings,
) -> BaseModel:
    provider = route.provider
    client = _make_client(route)
    system = _system_prompt(response_model, provider.supports_json_mode)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    start = time.monotonic()
    sent_messages = messages
    resp = await _chat(client, model, temperature, sent_messages, provider)
    text = _text_of(resp)
    try:
        obj: BaseModel = response_model.model_validate_json(text)
    except ValidationError as exc:
        sent_messages = _retry_messages(messages, text, exc)
        resp = await _chat(client, model, temperature, sent_messages, provider)
        text = _text_of(resp)
        try:
            obj = response_model.model_validate_json(text)
        except ValidationError as exc2:
            raise LlmError(f"LLM response failed schema validation after retry: {exc2}") from exc2
    latency_ms = int((time.monotonic() - start) * 1000)

    if use_cache:
        full_prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in sent_messages)
        row = _build_row(
            key,
            provider,
            model,
            settings.prompt_version,
            prompt,
            full_prompt,
            response_model,
            text,
            obj,
            resp,
            latency_ms,
        )
        await store_cached(row)
        logger.info("llm cache store: %s %s cost=%s", model, key[:8], row.cost_usd)
    return obj


def _retry_messages(
    messages: list[dict[str, str]], bad: str, exc: ValidationError
) -> list[dict[str, str]]:
    correction = (
        f"Your previous response failed schema validation:\n{exc}\n\n"
        f"Previous response:\n{bad}\n\n"
        "Return corrected JSON that matches the required schema exactly."
    )
    return [
        *messages,
        {"role": "assistant", "content": bad},
        {"role": "user", "content": correction},
    ]


def _cli_model(model: str) -> str:
    """Resolve the model name passed to the CLI (combo names as-is, API aliases → sonnet)."""
    return model if model in CLAUDE_CLI_MODELS or model in CLI_COMBOS else "sonnet"


def _cli_retry_prompt(full_prompt: str, bad: str, exc: Exception) -> str:
    return (
        f"{full_prompt}\n\nYour previous response could not be parsed/validated:\n{exc}\n\n"
        f"Previous response:\n{bad}\n\n"
        "Return only corrected JSON that matches the required schema exactly."
    )


async def _run_cli(
    prompt: str, model: str, tools: str = "", timeout: int | None = None
) -> tuple[str, float, str]:
    """One ``claude -p`` call via the shared library. Returns (text, cost_usd, model).

    ``model`` in the result is the one that answered: the caller's, or the fallback
    combo. ``ClaudeCliError`` covers the library's whole error hierarchy (including
    ``NotLoggedInError``/``UsageLimitError``); every failure is re-raised as
    :class:`LlmError` so callers keep one exception type to catch.
    """
    kwargs: dict[str, Any] = {"tools": tools}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        result = await arun_claude(prompt, model=model, **kwargs)
        answered = model
    except NotLoggedInError as exc:
        # Every combo runs under the same claude login, so a fallback cannot help.
        raise LlmError(f"claude CLI not logged in (model={model}): {exc}") from exc
    except ClaudeCliError as exc:
        # The gateway's model→combo mapping can wedge (sonnet sliding to a 200s+ Gemini
        # stream); one retry pinned to the free combo by name (docs/OMNIROUTE.md in
        # pylibs/claude-cli: `--model <combo>` reaches the gateway as-is).
        fallback = os.environ.get(_FALLBACK_COMBO_ENV) or _FALLBACK_COMBOS.get(
            model, _DEFAULT_FALLBACK_COMBO
        )
        logger.warning(
            "claude CLI call failed (model=%s): %s: %s; retrying via combo %r",
            model,
            type(exc).__name__,
            exc,
            fallback,
        )
        try:
            result = await arun_claude(prompt, model=fallback, **kwargs)
            answered = fallback
        except (ClaudeCliError, OSError) as fb_exc:
            raise LlmError(
                f"claude CLI call failed (model={model}, then combo={fallback}, "
                f"timeout={timeout or 'default'}s): {type(fb_exc).__name__}: {fb_exc}"
            ) from fb_exc
    except OSError as exc:
        raise LlmError(
            f"claude CLI call failed (model={model}, timeout={timeout or 'default'}s): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return result.text, result.cost_usd or 0.0, answered


def _cli_row(
    key: str,
    model: str,
    settings: Settings,
    prompt: str,
    full_prompt: str,
    response_model: type[BaseModel],
    text: str,
    obj: BaseModel,
    cost: float,
    latency_ms: int,
) -> LlmCache:
    return LlmCache(
        provider="claude-cli",
        model=model,
        prompt_version=settings.prompt_version,
        prompt_hash=hash_text(normalize_prompt(prompt)),
        prompt_text=full_prompt,
        input_hash=hash_text(response_model.__name__),
        cache_key=key,
        response_text=text,
        structured_json=obj.model_dump(mode="json"),
        prompt_tokens=None,
        completion_tokens=None,
        cost_usd=cost,
        latency_ms=latency_ms,
    )


async def _generate_cli(
    prompt: str,
    response_model: type[BaseModel],
    model: str,
    key: str,
    use_cache: bool,
    settings: Settings,
    cli_tools: str = "",
    cli_timeout: int | None = None,
) -> BaseModel:
    system = _system_prompt(response_model, supports_json_mode=False)
    full = f"{system}\n\n{prompt}"
    sent_prompt = full
    cli_model = _cli_model(model)
    start = time.monotonic()
    text, cost, answered = await _run_cli(full, cli_model, cli_tools, cli_timeout)
    try:
        obj: BaseModel = response_model.model_validate(extract_json(text))
    except (ValidationError, ValueError) as exc:
        sent_prompt = _cli_retry_prompt(full, text, exc)
        # Retry on whoever answered: re-walking a wedged primary doubles the worst case.
        text, retry_cost, answered = await _run_cli(sent_prompt, answered, cli_tools, cli_timeout)
        cost += retry_cost
        try:
            obj = response_model.model_validate(extract_json(text))
        except (ValidationError, ValueError) as exc2:
            raise LlmError(f"claude CLI response invalid after retry: {exc2}") from exc2
    latency_ms = int((time.monotonic() - start) * 1000)

    # A fallback combo's answer is not cached under the primary model's key, or the
    # degraded answer would be served as that model for good.
    if use_cache and answered == cli_model:
        row = _cli_row(
            key,
            cli_model,
            settings,
            prompt,
            sent_prompt,
            response_model,
            text,
            obj,
            cost,
            latency_ms,
        )
        await store_cached(row)
        logger.info("llm cache store: %s %s cost=$%.4f", cli_model, key[:8], cost)
    return obj
