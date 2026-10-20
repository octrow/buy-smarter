# Tasks

## 1. Key store

- [x] 1.1 Add `keyring`; implement `llm/keys.py` (set/get/clear per provider, chosen provider); tests use a fake in-memory keyring backend
- [x] 1.2 `NoKeyringError` → user-facing error naming the env var, with nothing written to disk; test with a failing backend

## 2. Resolution

- [ ] 2.1 `resolve_route()` pure function (env > keyring > CLI > None) with `DEFAULT_MODEL_BY_PROVIDER`; table-driven tests over every combination
- [ ] 2.2 Wire `_resolve_provider`/`_make_client` to the route; existing adapter tests stay green
- [ ] 2.3 Log filter redacting key-shaped strings; test that a logged key is masked

## 3. No-LLM run

- [ ] 3.1 `recommend()` switches to no-LLM when the route is None and fills `skipped_llm_steps`; test with no keys and no CLI returns ranked offers
- [ ] 3.2 Report renders the skipped-steps note; snapshot test of the report section

## 4. CLI

- [ ] 4.1 `buysmarter llm set-key|clear-key|status` (hidden prompt; status never prints the key); CliRunner tests including "no key characters in output"
- [ ] 4.2 `doctor` "LLM" row shows the route and its source; test for each source
