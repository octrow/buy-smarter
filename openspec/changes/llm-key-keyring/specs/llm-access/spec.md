## Purpose

Defines how buy-smarter finds an LLM provider and its API key on a user's machine, how a user-supplied key is stored safely, and how a recommendation behaves when no LLM is available, so the app works for people without developer tooling.

## ADDED Requirements

### Requirement: User-supplied key is stored in the OS keyring

A key that the user enters SHALL be stored in the operating system's credential store, and SHALL NOT be written to any file buy-smarter controls.

#### Scenario: Setting a key

- **WHEN** the user sets an OpenAI key
- **THEN** the key is readable from the OS credential store under buy-smarter's service name
- **AND** no buy-smarter config, cache or log file contains it

#### Scenario: Clearing a key

- **WHEN** the user clears the key for a provider
- **THEN** it is removed from the credential store, and the next run no longer uses that provider

### Requirement: Provider resolution order

buy-smarter SHALL pick the LLM route in this order: a key from the environment, then the stored key for the chosen provider, then the local `claude` CLI if installed, then no LLM.

#### Scenario: Environment wins

- **WHEN** both an environment key and a stored key exist for the chosen provider
- **THEN** the environment key is used

#### Scenario: Stored key without CLI

- **WHEN** no environment key is set, a stored key exists, and `claude` CLI is not installed
- **THEN** LLM calls go to the stored key's provider

#### Scenario: CLI only

- **WHEN** no key is available and `claude` CLI is installed
- **THEN** LLM calls go through the CLI

### Requirement: Runs without any LLM

With no LLM route available, a recommendation SHALL complete without LLM, and the report SHALL list the steps that were skipped.

#### Scenario: Nothing configured

- **WHEN** no key and no `claude` CLI are available
- **THEN** a recommendation returns ranked offers
- **AND** the report says query parsing, review summaries and ingredient extraction ran without LLM

### Requirement: Key is never revealed

Status output (`doctor`, `llm status`, the GUI) SHALL show the provider and where the key came from (environment, keyring, CLI), and SHALL NOT show the key or any part of it.

#### Scenario: Status with a stored key

- **WHEN** the user runs `llm status` with an OpenAI key in the keyring
- **THEN** it shows "openai (keyring)" and no characters of the key

### Requirement: Missing credential store degrades safely

If the OS has no usable credential store, setting a key SHALL fail with a message explaining that environment variables can be used instead. It SHALL NOT fall back to plaintext storage.

#### Scenario: Headless Linux without Secret Service

- **WHEN** no keyring backend is available and the user sets a key
- **THEN** the command fails with a message naming the env var for that provider, and nothing is written to disk
