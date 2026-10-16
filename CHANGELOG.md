# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Container image: `runtime` target (API, CLI, migrations; non-root, healthcheck) and `browser` target with
  headless Chromium for Ozon. CI builds the image and smoke-tests it.
- `docker compose up -d --build` runs the whole stack: Postgres, Redis, a one-shot `migrate` and the API on :8000;
  the `browser` profile adds the Chromium image. `make up` still starts only Postgres and Redis.

### Changed
- `doctor` checks Chromium with a headless launch, so the headless-only shell counts as installed.
- Compose publishes Postgres and Redis on 127.0.0.1 only.

## [0.1.2] - 2026-10-15

### Added
- Published on PyPI (`uvx buysmarter`) via trusted publishing; package metadata: license, authors, project URLs.

## [0.1.1] - 2026-10-14

### Changed
- fastmcp 4.1 (MCP SDK v2): the MCP server answers both the handshake-era and the sessionless protocol; tools and
  their schemas are unchanged.
- Reports go to `~/.local/share/buysmarter/reports/` and logs to `~/.cache/buysmarter/logs/`, not to the folder the
  command runs in.

### Security
- pyjwt 2.15.1, cryptography 50.0.2 and mako 1.4.3 close the open advisories (1 critical, 6 high).

### Dependencies
- pydantic 2.13.5, pydantic-settings 2.15.0, fastapi 0.142.2, asyncpg 0.32.0.

## [0.1.0] - 2026-10-14

First release.

### Added
- `recommend`: a request in plain words → offers from Kaspi.kz, Ozon.kz and OLX.kz (used) ranked for budget, city
  and required features, with a score per axis (brand or ingredient, price with a 30-day trend, reviews, specs,
  popularity, availability) and a markdown report.
- Review trust: five-star share over the full distribution, review bursts, junk texts and defect complaints
  discount the reviews axis; required features are checked against verbatim review quotes.
- Brand and model knowledge per category (`learn-category`, `learn`), seeded for water flossers.
- Smartphones: Kimovil index and phone specs in the specs axis.
- OLX used listings: defects the seller admits to are listed as cons; ads sold for parts are dropped.
- Price history (`refresh`, `history`) in Postgres; everything else works without it.
- MCP server for Claude over the same services.
- `doctor`: what is missing and what that turns off.
- One-command installers for Linux, macOS (`install.sh`) and Windows (`install.ps1`); settings also read
  `~/.config/buysmarter/.env`.
