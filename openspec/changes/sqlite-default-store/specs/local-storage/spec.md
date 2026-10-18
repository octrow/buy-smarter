## Purpose

Defines where buy-smarter persists price history, offer snapshots and user preferences, so that every in-process entry point (CLI, MCP, desktop GUI) works on a clean machine with no database server, while a server deployment can still point it at Postgres.

## ADDED Requirements

### Requirement: Works without a database server

On a machine with no database server and no storage configuration, buy-smarter SHALL persist price snapshots and preferences to a local file in the per-user data directory.

#### Scenario: First run on a clean machine

- **WHEN** a user with no `DATABASE_URL` set and no Postgres running runs a recommendation
- **THEN** the run succeeds and its price snapshots are stored locally
- **AND** a later `history` for a matched product returns those snapshots

#### Scenario: Data survives restarts

- **WHEN** the user sets a preference, quits, and starts buy-smarter again
- **THEN** the preference is still in effect

### Requirement: Schema is prepared automatically

buy-smarter SHALL create the local store and bring its schema up to date on first use, without any manual migration step.

#### Scenario: Missing store file

- **WHEN** the local store file does not exist
- **THEN** the first command that needs storage creates it with the current schema

#### Scenario: Store from an older version

- **WHEN** the store was created by an older buy-smarter version
- **THEN** it is upgraded in place on first use and the existing history is kept

### Requirement: Server deployments can use Postgres

When `DATABASE_URL` points at Postgres, buy-smarter SHALL use it with the same schema and behaviour as the local store.

#### Scenario: Postgres configured

- **WHEN** `DATABASE_URL` is a Postgres URL and the server extra is installed
- **THEN** snapshots, history and preferences behave the same as with the local store

### Requirement: Core installs without server dependencies

The CLI, the MCP server and the recommendation core SHALL install and run without the multi-user server dependencies (HTTP API framework, job queue, Postgres driver, Redis client).

#### Scenario: Plain install

- **WHEN** buy-smarter is installed without the server extra
- **THEN** `recommend`, `history`, `refresh`, `doctor` and the MCP server start and work

#### Scenario: Server entry point without the extra

- **WHEN** the HTTP API or the worker is started without the server extra
- **THEN** it exits with a message naming the extra to install, not a raw import error

### Requirement: Existing preferences are carried over

Preferences that an earlier version saved to its JSON preferences file SHALL be imported into the store once, and SHALL NOT overwrite values already in the store.

#### Scenario: Upgrade with a preferences file

- **WHEN** a JSON preferences file from an earlier version exists and the store has no value for one of its keys
- **THEN** that value is imported, and a second start imports nothing again

### Requirement: Doctor reports the store

`doctor` SHALL report which storage backend is in use and where the store is located, and SHALL flag it when the store is not writable.

#### Scenario: Default local store

- **WHEN** a user runs `doctor` with default settings
- **THEN** it shows the local store's path as OK

#### Scenario: Unwritable location

- **WHEN** the store location cannot be written
- **THEN** `doctor` marks storage as failing and says history and preferences are off
