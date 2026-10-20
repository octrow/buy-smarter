# Design

## Context

`llm/adapter.py::_resolve_provider` maps a model prefix to `PROVIDERS[...]` and reads `getattr(settings, provider.key_field)`. `haiku`/`sonnet`/`opus` always route to the CLI. An unknown model without the CLI raises `LlmConfigError`. `services/recommend.py` already has a `use_llm=False` path.

## Goals / Non-Goals

**Goals:** a keyring-backed key per provider; a deterministic resolution order; graceful no-LLM.
**Non-Goals:** OAuth/subscription logins; proxying through a buy-smarter server; per-model tuning UI.

## Decisions

- **`llm/keys.py`** wraps `keyring` with service `buysmarter` and username = provider name. The chosen provider is stored as username `_provider`. It is the only module that imports `keyring`.
- **Model per provider.** When the route is a stored key, the model comes from a `DEFAULT_MODEL_BY_PROVIDER` constant (e.g. openai → a current small GPT model). The exact ids are picked from each provider's docs at implementation time. `default_llm_model` still wins when it matches that provider.
- **Resolution** is one pure function `resolve_route(settings, keys, claude_available) -> Route | None`, unit-tested over a matrix. `_resolve_provider` and `_make_client` consume the route. Env keys keep their current pydantic-settings fields.
- **Auto no-LLM.** `recommend()` calls `resolve_route` once. A `None` route sets `use_llm=False` for the whole run and records `skipped_llm_steps` on `RecommendResult`. That is the opposite of today, where it raises mid-pipeline.
- **No plaintext fallback.** `keyring.errors.NoKeyringError` → user-facing error, so `keyrings.alt` is never installed. Rationale: ADR 0004 trust boundary.
- **Redaction.** Keys are never part of the LLM cache key or of exception messages. A log filter scrubs `sk-…`/`AIza…` patterns as defence in depth.

## Risks / Trade-offs

- [Risk] Linux desktops without Secret Service (minimal WMs) → the error message points to env vars, and `doctor` shows the keyring backend.
- [Risk] PyInstaller misses keyring backends → covered in desktop-packaging (hidden imports + a smoke test that sets and reads a dummy key).
