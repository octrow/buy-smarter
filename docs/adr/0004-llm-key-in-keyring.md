# Desktop LLM access: the user brings their own key, stored in the OS keyring

The default `sonnet` model routes to a local `claude` CLI that a stranger will not have. The Settings screen asks for a provider (openai, anthropic, gemini, deepseek) and a key, stored with `keyring` (Secret Service, Credential Manager, Keychain). If `claude` CLI is on PATH, it is used. With no key the search runs `--no-llm`, and the report says what it lacks. A plaintext key in a config file was rejected: this is a trust boundary in a distributed app.
