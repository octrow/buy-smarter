# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-10-22

### Added
- **Installers for the desktop app** on every release, each installed and self-tested in CI before it is attached:
  a Windows setup program (per user, no administrator rights, Start-menu entry, uninstaller) and a macOS disk
  image (the app next to an Applications link). Ubuntu and Debian keep the `.deb`. The app has its own icon on the
  window, the taskbar, the menus and the installers.

### Fixed
- Desktop app: the health rows in «Состояние и ключи» say in Russian what to click (the AI-key block, the Ozon
  check, installing Chrome, a server `DATABASE_URL` in `~/.config/buysmarter/.env`) instead of terminal commands.
- Desktop app: the search screen shows its progress in Russian and prices as "35 000 ₸"; the sidebar has no
  scrollbar.
- **Desktop app started from the app menu** now finds the claude CLI route: it takes PATH from the login shell,
  so `omniroute` and `node` installed through nvm are visible. The AI health check no longer reports it missing.
- Settings: health-check rows are in Russian, a failing store names the DATABASE_URL it tried, and the AI hint
  mentions OmniRoute. Budget fields align with the other inputs; the report and price-history screens explain
  their empty state; the search form labels its AI and marketplace checkboxes.
- Desktop app: with the store off, «Настройки поиска» and «История цен» say so up front instead of failing on
  use. Settings rows align to the top of a long hint, and the AI-key panel says a key is optional with the claude
  CLI.
- Desktop app: the «Настройки» screen is now «Состояние и ключи», so it is no longer confused with «Настройки поиска».

## [0.3.0] - 2026-10-21

### Added
- **Desktop app** (`buysmarter gui`, PySide6): search with live per-marketplace progress, the explainable report
  (best pick, score axes, sortable and filterable alternatives, used offers on OLX, what could not be checked),
  search preferences, price history and a Settings screen with health checks. Follows the OS light/dark theme.
- **Ready-made desktop builds** on every release: a `.deb` for Ubuntu and Debian (app menu entry, pulls in the
  libraries it needs), and archives for Windows, macOS (Apple silicon) and Linux. Each build passes a self-test in
  CI before it is attached. See [Installing the desktop app](https://github.com/octrow/buy-smarter/blob/main/docs/install-desktop.md).
- **Your own LLM key, kept in the OS keyring:** `buysmarter llm set-key <provider>` (OpenAI, Anthropic, Gemini,
  DeepSeek), `llm clear-key`, `llm status`, or the AI key block in the app's Settings. Order: an env key, the stored
  key, then the `claude` CLI. With none of them a run goes keyless and the report says what it skipped.
- **Ozon on your own Google Chrome:** once `buysmarter ozon-setup` has saved a session, Ozon runs on the installed
  Chrome, so no 150 MB Chromium download is needed; otherwise on Playwright's Chromium. With no browser Ozon is
  skipped, the report says so, and `doctor` shows an "Ozon browser" row with what to do.
- The install scripts install the desktop app too and end with `buysmarter gui`.

### Fixed
- A `DATABASE_URL` whose driver is not installed (for example a Postgres URL in a plain install) turns the local
  store off with a warning instead of crashing at start.

### Changed
- **The local store is the default.** Price history, preferences and learned categories go to a SQLite file in the
  user data dir (`~/.local/share/buysmarter/buysmarter.db` on Linux), created and migrated at start by the CLI and the
  MCP server; no database server is needed. Postgres still works when `DATABASE_URL` points at it, and keeps its
  explicit `alembic upgrade head` step. `doctor` shows a `Storage` row (backend and path) instead of `Postgres`.
- **BREAKING (packaging):** the HTTP API, the worker and their dependencies (fastapi, uvicorn, asyncpg, redis, taskiq,
  the Prometheus exporter) moved to the `server` extra: `pip install "buysmarter[server]"`. The Docker image has it.
- Migrations ship inside the package (`buysmarter.migrations`); `alembic.ini` points there.

### Removed
- The JSON-file fallbacks for preferences (`~/.config/buysmarter/prefs.json`) and category knowledge
  (`~/.config/buysmarter/knowledge/`). Both are imported into the store once and renamed `*.imported`; an unreadable
  preferences file becomes `prefs.json.invalid`. Rollback: reinstall 0.2.0 and rename the `*.imported` files back.

## [0.2.0] - 2026-10-16

### Added
- HTTP API for recommendations: `POST /recommend` queues a run (202 + `status_url`), `GET /recommend/{id}` returns its
  status and the result (the markdown report plus the best offer, alternatives and used listings), `GET /history`
  reads price history. Jobs run in a taskiq worker on a Redis stream and survive a worker crash; the same request
  within 15 minutes returns the same job; 10 new jobs per 10 minutes per client, then 429; optional `X-API-Key`.
- Worker in compose (`worker`, metrics on :9000) and in the Helm chart (`worker.*`, PodMonitor); the kind smoke test
  runs a job end to end. Queue metrics, a Queue row on the Grafana dashboard, one trace per job across API and worker.
- Helm chart (`deploy/helm/buysmarter`): migrations as a pre-install/pre-upgrade hook Job, Pod Security Standard
  `restricted`, optional Ingress, HPA and PodDisruptionBudget, `helm test`. CI lints it, validates it with
  kubeconform, then installs and upgrades it in kind.
- `GET /ready`: 503 until the database answers; `/health` stays dependency-free.
- `BUYSMARTER_LOG_FILE=-` sends INFO logs to stderr instead of a file (the chart sets it).
- OpenTelemetry: `GET /metrics` in the Prometheus format (HTTP by route, recommend duration and ranked pool size,
  marketplace searches by outcome, LLM calls by cache hit or miss); OTLP traces when `OTEL_EXPORTER_OTLP_ENDPOINT` is
  set. `make observe` adds Prometheus, Grafana with a dashboard and Jaeger to the compose stack; the chart gets scrape
  annotations, an optional ServiceMonitor and `otel.endpoint`.

### Changed
- Ozon in compose: `docker-compose.browser.yml` runs the worker on the Chromium image; the `browser` profile with
  `app-browser` is gone. The chart requires `REDIS_URL`; `/ready` checks Redis too.
- The runtime image is 360 MB (OpenTelemetry, taskiq).

### Fixed
- The API reports the package version instead of a hardcoded 0.1.0.

## [0.1.3] - 2026-10-16

### Added
- Container image: `runtime` target (API, CLI, migrations; non-root, healthcheck) and `browser` target with
  headless Chromium for Ozon. CI builds the image and smoke-tests it.
- `docker compose up -d --build` runs the whole stack: Postgres, Redis, a one-shot `migrate` and the API on :8000;
  the `browser` profile adds the Chromium image. `make up` still starts only Postgres and Redis.
- Images on GHCR (`ghcr.io/octrow/buy-smarter`, `-browser` suffix for Chromium): `main` and `sha-…` on every commit
  to main, `X.Y.Z`, `X.Y` and `latest` on releases; with build provenance, SBOM and a Trivy gate on critical CVEs.

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
