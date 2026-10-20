# Proposal

## Why

The default model `sonnet` routes to a local `claude` CLI that people installing the desktop app will not have. API keys today come only from env vars (`OPENAI_API_KEY`, …), which a desktop user does not set. ADR 0004: the user brings their own key, it is stored in the OS keyring, and without any provider buy-smarter runs without LLM and says so.

## What Changes

- A key store backed by the OS keyring (Secret Service, Credential Manager, Keychain), holding one key per provider (openai, anthropic, gemini, deepseek) and the chosen provider.
- Provider resolution order: env var, then keyring key for the chosen provider, then local `claude` CLI, then no LLM. This changes existing setups: an env key now wins even for `haiku`/`sonnet`/`opus`/combo calls that used to go to the CLI unconditionally (the model becomes that provider's default). Calls with `cli_tools` (web search) still prefer the CLI when it is installed.
- Without any provider, a recommendation runs as with `--no-llm`, and the report lists what it skipped (query parsing, review summary, ingredient extraction).
- CLI: `buysmarter llm set-key <provider>` (prompted, never echoed), `buysmarter llm clear-key <provider>`, `buysmarter llm status`. The GUI settings screen (desktop-gui-v1) calls the same functions.
- `doctor` reports the active LLM route, without the key.

## Capabilities

### New Capabilities

- `llm-access`: how buy-smarter finds an LLM provider and its key, how the key is stored, and how a run behaves with no provider.

### Modified Capabilities

(none)

## Impact

- Dependency: `keyring`.
- Code: `llm/adapter.py` (`_resolve_provider`, `_make_client`), `config.py`, `services/recommend.py` (auto no-LLM), `cli/report.py` (skipped-steps note), `cli/main.py` (`llm` sub-app, doctor).
- Security: keys never logged, never written to config files, never cached alongside LLM responses.
