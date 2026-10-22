# local-storage Specification

## Purpose
Defines where buy-smarter persists price history, offer snapshots, user preferences and category knowledge, so that every in-process entry point (CLI, MCP, desktop GUI) works on a clean machine with no database server, while a server deployment can still point it at Postgres.

## Requirements

### Requirement: Works without a database server

On a machine with no database server and no storage configuration, buy-smarter SHALL persist price snapshots, preferences and category knowledge to a local file in the per-user data directory.

#### Scenario: First run on a clean machine

- **WHEN** a user with no `DATABASE_URL` set and no Postgres running runs a recommendation
- **THEN** the run succeeds and its price snapshots are stored locally
- **AND** a later `history` for a matched product returns those snapshots

#### Scenario: Data survives restarts

- **WHEN** the user sets a preference, quits, and starts buy-smarter again
- **THEN** the preference is still in effect

### Requirement: Local store schema is prepared automatically

For the local store, buy-smarter SHALL create the file and bring its schema up to date at process start, without any manual migration step. It SHALL also do this when installed from a package rather than run from a source checkout.

#### Scenario: Missing store file

- **WHEN** the local store file does not exist and any CLI command, the MCP server or the GUI starts
- **THEN** the file is created with the current schema before the first query

#### Scenario: Store from an older version

- **WHEN** the store was created by an older buy-smarter version
- **THEN** it is upgraded in place at start, and the existing history is kept

#### Scenario: Two processes start together

- **WHEN** the GUI and the MCP server start at the same moment on a fresh machine
- **THEN** both start without error, and the store holds one schema at the current version

#### Scenario: Installed package

- **WHEN** buy-smarter is installed as a package (no source checkout) and run for the first time
- **THEN** the local store is created at the current schema

### Requirement: Server deployments use Postgres with explicit migrations

When `DATABASE_URL` points at Postgres, buy-smarter SHALL use it with the same schema and behaviour as the local store, and SHALL NOT migrate it automatically at start.

#### Scenario: Postgres configured

- **WHEN** `DATABASE_URL` is a Postgres URL and the server extra is installed
- **THEN** snapshots, history, preferences and knowledge behave the same as with the local store

#### Scenario: No auto-migration on Postgres

- **WHEN** an API replica or worker starts against a Postgres database that is behind the current schema
- **THEN** it does not run migrations itself

### Requirement: Case-insensitive search in any script

Product search over stored data (e.g. `history <term>`) SHALL be case-insensitive for Cyrillic as well as Latin text on every supported backend.

#### Scenario: Cyrillic capital letter

- **WHEN** a snapshot was stored for "Ирригатор PECHAM" and the user searches history for "ирригатор"
- **THEN** that product's snapshots are returned

### Requirement: Timestamps are UTC and ordered

Stored timestamps SHALL come back timezone-aware in UTC on every backend. Snapshots recorded within the same second SHALL keep a stable newest-first order.

#### Scenario: Same-second snapshots

- **WHEN** two snapshots for one product are recorded within the same second
- **THEN** history lists the later-recorded one first, every time, with UTC timestamps

### Requirement: Core installs without server dependencies

The CLI, the MCP server and the recommendation core SHALL install and run without the multi-user server dependencies (HTTP API framework, job queue, Postgres driver, Redis client, Prometheus exporter).

#### Scenario: Plain install

- **WHEN** buy-smarter is installed without the server extra
- **THEN** `recommend`, `history`, `refresh`, `doctor` and the MCP server start and work

#### Scenario: Server entry point without the extra

- **WHEN** the HTTP API or the worker is started without the server extra
- **THEN** it exits with a message naming the extra to install, not a raw import error

### Requirement: Existing local data is carried over

Preferences and category knowledge that an earlier version saved to JSON files SHALL be imported into the store once. Values already in the store SHALL NOT be overwritten, and an unreadable file SHALL NOT stop the app.

#### Scenario: Upgrade with a preferences file

- **WHEN** a JSON preferences file from an earlier version exists and the store has no value for one of its keys
- **THEN** that value is imported, and a second start imports nothing again

#### Scenario: Upgrade with learned categories

- **WHEN** knowledge files from an earlier version exist for categories the store does not have
- **THEN** those categories are imported and used by the next recommendation

#### Scenario: Corrupt preferences file

- **WHEN** the preferences file is not valid JSON
- **THEN** the app starts normally, the file is set aside, and a warning is logged

### Requirement: Doctor reports the store

`doctor` SHALL report which storage backend is in use and where the store is located, under the name `Storage`. It SHALL flag the store when it is not writable.

#### Scenario: Default local store

- **WHEN** a user runs `doctor` with default settings
- **THEN** a `Storage` row shows the local store's path as OK

#### Scenario: Unwritable location

- **WHEN** the store location cannot be written
- **THEN** `doctor` marks `Storage` as failing, saying history, preferences and learned categories are off
