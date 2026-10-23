**English** | **[Русский](README.ru.md)**

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="buy-smarter: describe what you need, get offers from Kaspi, Ozon and OLX ranked for your budget and city. A real best pick scores 66 of 100, with a bar for each axis: ingredient 25/25, brand 1.5/10, price 11/25, reviews 3.4/12, specs 15/15, popularity 0.3/3, availability 10/10.">
</p>

[![CI](https://github.com/octrow/buy-smarter/actions/workflows/ci.yml/badge.svg)](https://github.com/octrow/buy-smarter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

An explainable shopping assistant for Kazakhstan. You describe what you need in plain words
("a portable water flosser, IPX7, under 40,000 ₸, Almaty"). buy-smarter searches **Kaspi.kz**,
**Ozon.kz** and **OLX.kz** (used items), and ranks the offers for your budget, city and must-have features.
For every pick it shows which axes scored it and why. It runs as a CLI, as an MCP server for Claude and as an HTTP API
with a job queue.

It does not hunt for "the best product in general". It looks for the best one for you, today, under your
limits, and tells you what it could not verify.

## What a report looks like

An excerpt from a real run (a saved report, query: adapalene 0.1% cream under 10,000 ₸, Almaty):

```text
🥇 ELEGANT COSMED Eleklin-A gel, 15 g   [kaspi]
Score 66/100 · Confidence 70% · 7,700 ₸

| Axis         | Score   | Why                                                      |
| ingredient   | 25/25   | active ingredient present (0.1%)                         |
| brand        | 1.5/10  | unknown brand, no trust data                             |
| price        | 11/25   | 7,700 vs pool median 5,238 (ratio 1.47)                  |
| reviews      | 3.4/12  | 4.9/5 across 31 reviews; review trust 0.5: 96% five-star |
| availability | 10/10   | in stock                                                 |

+ cheaper analog by active ingredient: Kormesic Adapalene gel, 50 g, 2.6× cheaper
- unknown brand · weak reviews · weak popularity
```

The report goes on with a review summary, alternatives (best value, best budget), the top N with links and a
"Used on OLX" section.

## Why not just ask a chatbot or sort by rating?

- **Kazakh marketplaces.** General assistants (ChatGPT shopping, Perplexity, Google AI Mode) barely see Kaspi
  or OLX.kz. buy-smarter reads their JSON endpoints directly and survives one source going down.
- **Ratings get gamed.** Review trust looks at the five-star share across the full distribution, bursts of
  reviews on the same dates, junk texts ("haven't tried it yet", "courier was fast"), unverified purchases
  and defect complaints. Then it discounts the reviews axis accordingly.
- **Your requirements are checked, not assumed.** A required feature the specs don't confirm is checked
  against reviews. The quote must exist verbatim in a review and appear at least twice, or the verdict stays
  "not enough data".
- **Brand and model knowledge.** Brand tiers and model aliases are seeded for water flossers. Any other
  category gets them via `learn-category`, so "me 705" on OLX resolves to "Xiaomi Water Flosser 2". Without it,
  every brand ranks as unknown: run `learn-category "смартфон"` once before phone queries. Knowledge goes to
  the local store.
- **Auto-learning.** A query for a category with no knowledge anywhere starts learning it in the background
  (opus with web search, 1-3 minutes) in the desktop app, MCP (with `parse_with_llm`) and the CLI (it asks;
  `--learn` skips the question). The search never waits; repeat it once the category is learned. At most 10
  starts per hour (`BUYSMARTER_AUTO_LEARN_PER_HOUR`); `0` turns auto-learning off. The API never learns on its
  own unless `BUYSMARTER_AUTO_LEARN=true`.
- **How the category is judged.** Every report says which capabilities took part and who decided ("Как
  оценивали: состав — обучение"); the desktop app lets you switch a category capability on or off for your
  query's category, or reset it to automatic. "Рынок б/у" (OLX) and "Официальные магазины" (brand sites) are
  such switches too: on by default, off for a category when learning or you say so.
- **Used goods.** A query like "б/у ноутбук" ("только б/у") adds OLX by itself, even if you did not tick it, and
  puts the OLX section first in the CLI, the desktop app and the markdown report (CLI, MCP, API).
- **Phone specs from Kimovil.** For smartphone queries each model is looked up on Kimovil: its overall index
  (0–100) goes into the specs axis ("Kimovil 83/100"), and refresh rate, battery, fast charging, chipset, AnTuTu
  and IP rating are added in Kaspi's wording, so "120 Гц" or "5000 мАч" as a required feature can match. Results
  are cached in `~/.cache/buysmarter/kimovil.db` (30 days; misses 7 days).
- **Price history.** `refresh` and `history` record price and review-count snapshots per product.
  Kaspi has no public Keepa-style tracker.

Closest open-source projects and what was borrowed from them: [docs/analogs](docs/analogs/README.md).

## How it runs

One pipeline sits behind all three entry points. A run takes tens of seconds, so the HTTP API puts it on a Redis
stream, a worker runs it, and the client polls for the result.

```mermaid
flowchart TD
    client([client]) -->|"POST /recommend, GET /recommend/{id}"| api[FastAPI]
    api -->|"job, dedupe, rate limit"| redis[(Redis)]
    redis -->|"stream, ack after the run"| worker[taskiq worker]
    worker -->|result| redis
    worker -->|"search, offers, reviews"| markets[Kaspi · Ozon · OLX]
    worker -->|price history| pg[(Postgres)]
    api -.->|metrics, traces| obs[Prometheus · Grafana · Jaeger]
    worker -.-> obs
```

<p align="center">
  <img src="./assets/readme/api-demo.gif" width="100%" alt="Terminal: POST /recommend for a portable IPX7 water flosser under 38,000 tenge returns a job id with status queued. Polling the job later shows status done, the best pick Xiaomi Mijia Electric Flusher 2 MEO705 at 15,999 tenge from Kaspi with score 65.78, and the start of the markdown report.">
</p>

`make observe` adds Prometheus, Grafana and Jaeger to the compose stack. The screenshots below come from nine real
jobs: the dashboard with HTTP, marketplace, ranking and queue panels, and one job as a single trace, from
`POST /recommend` through the worker to the Postgres queries and the marketplace HTTP calls.

<p align="center">
  <img src="./assets/readme/grafana.png" width="100%" alt="Grafana dashboard buy-smarter over the last 30 minutes: requests per route, p95 latency, marketplace searches by outcome for Kaspi and OLX, LLM calls (none in this run), recommend p95 duration around 45 seconds, about 12 ranked offers per run, and a Queue row with finished jobs and about 5 seconds p95 time in queue.">
</p>
<p align="center">
  <img src="./assets/readme/jaeger.png" width="100%" alt="Jaeger trace buysmarter: POST /recommend, 43.8 seconds, 426 spans. The POST span (202) leads to the taskiq send span and the execute span in the worker, which holds Postgres connect and SELECT spans followed by a row of HTTP GET 200 calls of 350 to 730 milliseconds each.">
</p>

Every PR runs the tests, builds the image (non-root, healthcheck), starts the compose stack and installs, tests
and upgrades the Helm chart in kind with one job end to end. More in [HTTP API](#http-api),
[Kubernetes](#kubernetes) and [Observability](#observability).

## Quick start

You need a **Kazakhstan IP** for Kaspi: datacenter IPs get HTTP 429 (set `KASPI_PROXY` to a residential proxy).

macOS / Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/octrow/buy-smarter/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/octrow/buy-smarter/main/install.ps1 | iex"
```

The installer gets [uv](https://docs.astral.sh/uv/) (it brings its own Python), installs the latest release
with the desktop app (`buysmarter gui`), offers the browser for Ozon and runs `buysmarter doctor`, which says what is missing and what that turns off.
Re-run it to update; `sh -s -- --uninstall` removes it. Prefer to read it first? Download `install.sh`, read it,
then run `sh install.sh`. API keys and a proxy go in `~/.config/buysmarter/.env` (see [.env.example](.env.example)).

**Desktop app without the terminal:** each release has a `.deb` for Ubuntu and ready-made builds for Windows, macOS and Linux.
[Installing the desktop app](docs/install-desktop.md) says which file to download and how to open an
unsigned build; Ozon needs Google Chrome.

From source, for development ([uv](https://docs.astral.sh/uv/); Docker only for the server stack):

```bash
git clone https://github.com/octrow/buy-smarter && cd buy-smarter
uv sync
uv run buysmarter doctor      # tells you what is missing and what that turns off

# Optional pieces
uv sync --extra server && make up        # HTTP API stack: Postgres :5434 + Redis :6380 (see .env.example)
uv run playwright install chromium       # Ozon source
uv run buysmarter ozon-setup             # pass Ozon's anti-bot check once in a visible browser
cp .env.example .env                     # optional API keys and proxy
```

Price history, preferences and learned categories live in the **local store**, a SQLite file in the user
data dir (`~/.local/share/buysmarter/buysmarter.db` on Linux), created at first start. `doctor` shows where it
is. Every other piece is optional. The LLM comes from, in this order: an API key in the environment
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`), a key you stored with
`buysmarter llm set-key <provider>` (kept in the OS keyring, never in a file), or the local `claude` CLI.
With none of them a run goes keyless (naive parser, no review summary) and the report says so;
`buysmarter llm status` shows which one is in use. In a clone, prefix the commands below with `uv run`.

## Usage

```bash
# LLM request parsing via the claude-cli library (no API key needed)
buysmarter recommend "need a water flosser, portable, IPX7, under 40000 tg, Almaty"

# All three sources (default) or a subset; more candidates; no markdown report
buysmarter recommend "ирригатор до 40000 тг" --sources kaspi,olx --top 10 --no-save

# Trusted brands only; no LLM (naive parser, features are not filtered)
buysmarter recommend "ирригатор" --city Алматы --budget 40000 --trusted-only --no-llm

# Category knowledge: generate it (opus) or extract it from your own notes (sonnet)
buysmarter learn-category "робот-пылесос"
buysmarter learn-category "смартфон"      # brand tiers + model aliases for phones
buysmarter learn ~/notes/robot-vacuum.md --category "робот пылесос"

# Smartphones: Kimovil index in the specs axis, "120 Гц" / "5000 мАч" as required features
buysmarter recommend "смартфон до 250000 тг, 120 Гц, 5000 мАч" --city Алматы

# Price history: re-scan tracked categories, then look up one product
buysmarter refresh
buysmarter history "F300"
```

Output: the best pick with a score out of 100, confidence, a per-axis breakdown (brand, price with a 30-day
trend, reviews, specs, popularity, availability with the delivery date), a review summary
(pros, cons, frequent complaints, requirement checks), alternatives, the top N with links and the
OLX used section. Reports are saved to `~/.local/share/buysmarter/reports/` (`--no-save` turns that off), logs
go to `~/.cache/buysmarter/logs/buysmarter.log` (`BUYSMARTER_LOG_FILE` moves it). Nothing is written to the
folder you run it from.

Sources: **Kaspi** (JSON endpoints), **Ozon.kz** (Playwright browser session), **OLX.kz** (open REST,
used items). Accessories and off-topic listings are dropped by a heuristic filter with an LLM fallback.
In the used section, defects the seller admits to in the ad text (cracks, "doesn't turn on", water damage, weak
battery, past repairs) are listed under Con, and ads sold for parts are dropped.
Ranking also discounts reviews of unknown brands and penalizes suspiciously low prices.

**Request understanding.** Before any search the request is read for what kind of request it is. An ambiguous one
gets a question instead of a ranking: "подарок для мамы" asks what to look for, "iPhone 15 или S24" asks which one
to start with, "ремонт телефона" says it is a service, "аыавыа" asks to rephrase. On a terminal you pick a number or
type your own answer; at most two rounds, then the first option is searched and the report says so ("Искал: …").
`--no-clarify` never asks. Restricted goods (alcohol, tobacco and vapes, weapons, prescription medicines with an
LLM) are searched with a warning on the first line, never refused. Budgets are read by rules that never take a unit
for money: "до 50 штук" is 50 000 ₸, "до 2 м" or "4K" is no budget, "$500" adds a note instead.

Exit codes of `recommend`: `0` a recommendation (or OLX ads when no new product fits), `1` nothing to recommend
(including "nothing to search" after the last clarification round), `2` a marketplace request failed, `3` the request
needs a clarification and stdin is not a terminal (the question is printed).

## LLM: models and cache

All LLM calls go through [claude-cli](https://github.com/octrow/claude-cli): an OmniRoute gateway when one is
configured, otherwise the local `claude` CLI. Each task uses a fixed model: a cheap one for request parsing,
review summaries and classification fallbacks; sonnet for extracting knowledge from files; opus for
generating category knowledge. Every request/response pair is cached, so a repeat call is a free cache hit:

```bash
sqlite3 ~/.cache/buysmarter/llm_cache.db \
  "SELECT model, substr(cache_key,1,8), cost_usd, created_at FROM llm_cache ORDER BY created_at DESC LIMIT 10;"
grep "llm cache" ~/.cache/buysmarter/logs/buysmarter.log   # hit/miss for every call
```

## MCP server

buy-smarter is also an MCP server (fastmcp, stdio). The connected Claude acts as the conversational layer
and passes structured arguments (city, budget). The tools are thin wrappers over the same service and render
layers as the CLI.

```bash
uv run python -m buysmarter.mcp_server   # blocks on stdio; normally the client starts it
```

**Claude Code** picks up the `.mcp.json` in the repo root. For **Claude Desktop**, add to
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "buy-smarter": {
      "command": "uv",
      "args": ["run", "python", "-m", "buysmarter.mcp_server"],
      "cwd": "/path/to/buy-smarter"
    }
  }
}
```

| Tool | Purpose |
|---|---|
| `recommend` | Full pipeline → markdown report (best pick with axes, alternatives, top N, OLX used). Parameters: `query`, `city`, `budget`, `sources`, `trusted_only`, `top`, `parse_with_llm` (default `False`: Claude parses the request itself), `answer`, `clarify_round`, `no_clarify`. An ambiguous request returns a question with options: ask the human and call again with `answer` and the `clarify_round` it names. |
| `search_products` | Raw search on one marketplace, no ranking (title, price, rating, url). |
| `compare` | 2–4 comma-separated products → side-by-side specs (Kaspi). |
| `get_preferences` / `set_preference` | Read/write preferences (city, budget…) in the local store. |

## HTTP API

The API queues a recommend run and a worker executes it (taskiq on a Redis stream), because one run takes tens of
seconds. OpenAPI lives at `/docs`.

```bash
curl -s -X POST localhost:8000/recommend -H 'content-type: application/json' \
  -d '{"query": "ирригатор до 40000", "sources": ["kaspi"]}'
# 202 {"job_id": "…", "status": "queued", "status_url": "/recommend/…"}
curl -s localhost:8000/recommend/<job_id>
# {"status": "done", "result": {"report_markdown": "…", "best": {…}, "alternatives": {…}, …}}
curl -s 'localhost:8000/history?q=wp-100'
```

- Statuses: `queued` → `running` → `done`, `no_match` (no product has the required substance, or nothing passed the
  filters) or `failed` (`timeout`, `internal`). Jobs are kept for 24 hours.
- A request that needs a clarification ends as `no_match` with `result.reason = "needs_clarification"` and
  `result.clarification` (`kind`, `question`, `options`, `round`, `restricted_note`). Ask the user and post the
  request again with `answer` (an option or their own words) and `clarify_round` = `round` + 1 (at most 2);
  `no_clarify: true` searches the first option instead. `result.constraints` shows the parse (`intent`,
  `search_phrase`, `product_type`, ...); its `category` is a deprecated copy of `search_phrase`.
- A `no_match` where no new product passed the filters has `result.rejected_reasons` (reason → count) and
  `result.used_listings` (the OLX ads within the budget, possibly empty); `report_markdown` shows both.
- The same request within 15 minutes returns the same job (200). Each client may start 10 new jobs per 10 minutes,
  then gets 429 with `Retry-After` (`BUYSMARTER_RATE_LIMIT_JOBS`, `BUYSMARTER_RATE_LIMIT_WINDOW_S`).
- Auto-learning is off in the API: `BUYSMARTER_AUTO_LEARN=true` queues a separate `buysmarter.learn_category`
  task after a job for a new category (once per category, 600 s limit, `BUYSMARTER_AUTO_LEARN_PER_HOUR`).
  `result.assessed_by` lists the capabilities that took part and who decided.
- `BUYSMARTER_API_KEYS=k1,k2` requires `X-API-Key`. `BUYSMARTER_SOURCES` lists the marketplaces the API accepts:
  `kaspi,olx` by default, `ozon` needs the worker's Chromium image.
- A worker that dies mid-run loses nothing: its job is picked up by another worker after twice the job timeout
  (`BUYSMARTER_JOB_TIMEOUT_S`, 300 s by default).
- From cloud IPs the marketplaces often refuse requests (Kaspi answers 429 to datacenter addresses; see `KASPI_PROXY`),
  so most jobs there end as `no_match` or `failed`.

## Docker

```bash
docker compose up -d --build        # Postgres, Redis, migrations, API on :8000, worker; keys come from .env
docker compose run --rm app buysmarter recommend "…" --budget 30000
docker compose -f docker-compose.yml -f docker-compose.browser.yml up -d --build   # worker with Chromium: Ozon
docker compose down                 # add -v to drop the data
```

Prebuilt images: `ghcr.io/octrow/buy-smarter:latest` (releases), `:main` (every commit to main), `-browser`
suffix for Chromium. `TAG=main docker compose pull && docker compose up -d` runs them without building.
Check where an image came from with `gh attestation verify oci://ghcr.io/octrow/buy-smarter:latest -R octrow/buy-smarter`.

The image has no `claude` CLI: LLM calls use the API fallback (`DEFAULT_LLM_MODEL` plus a provider key in `.env`).
Without compose: `docker build -t buysmarter .` (`--target browser` for Chromium), then run `alembic upgrade head`
from the same image before the API.

## Kubernetes

A Helm chart lives in [`deploy/helm/buysmarter`](deploy/helm/buysmarter): the API, the worker (`worker.browser=true`
for Ozon) and the migration Job. Postgres and Redis are yours (managed or in-cluster):

```bash
helm install buysmarter deploy/helm/buysmarter -n buysmarter --create-namespace \
  --set secret.data.DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/buysmarter \
  --set secret.data.REDIS_URL=redis://host:6379/0
helm test buysmarter -n buysmarter
```

Migrations run as a pre-install/pre-upgrade hook Job, once per release. Probes: `/health` (liveness, no
dependencies) and `/ready` (readiness, 503 until the database answers). Pods pass the Pod Security Standard
`restricted`. Keys go in `secret.data` or in your own Secret (`secret.existing`). CI installs and upgrades the chart in
kind on every PR and runs one job through API → Redis → worker ([`deploy/kind/smoke.sh`](deploy/kind/smoke.sh)); it
checks the wiring, not the ranking, since the marketplaces may refuse CI traffic.

## Observability

The API serves Prometheus metrics on `GET /metrics` and the worker on `:9000/metrics` (OpenTelemetry SDK with the Prometheus exporter): HTTP request
rate and latency by route, plus `buysmarter_recommend_duration_seconds` (by outcome), `buysmarter_source_requests_total`
(by marketplace and outcome), `buysmarter_llm_calls_total` (by model, cache hit or miss) and
`buysmarter_recommend_offers_ranked`, plus the queue: `buysmarter_jobs_total` (by status), `buysmarter_job_wait_seconds`
and `buysmarter_rate_limited_total`. One trace follows a job from `POST /recommend` through the worker. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to also export traces (FastAPI, httpx calls to
the marketplaces, SQL) over OTLP/HTTP; `BUYSMARTER_METRICS=0` turns `/metrics` off.

```bash
make observe   # the compose stack + Prometheus :9090, Grafana :3000 (dashboard "buy-smarter"), Jaeger :16686
```

The chart adds `prometheus.io/scrape` annotations, an optional `ServiceMonitor` (`metrics.serviceMonitor.enabled`) and
`otel.endpoint` for traces.

## Development

```bash
make check   # ruff + pytest (no network or database server needed)
make test
make up / make down
```

Layout: `src/buysmarter/{normalize,db,llm,ranking,services,cli,knowledge,mcp_server,api}`, plus `jobs.py` (job
store in Redis), `worker.py` (taskiq worker) and `telemetry.py`; `deploy/` holds the Helm chart, the kind smoke test
and the observability configs. The marketplace
adapters live in a separate library, [market-sources](https://github.com/octrow/market-sources)
(`kaspi`/`olx`/`ozon` on top of a polite HTTP client and a Playwright session). Parser tests run on saved
JSON samples in `tests/samples/`. Notes from the source spikes are in [docs/spikes](docs/spikes).

## Limits

- Kaspi needs a Kazakhstan residential IP or `KASPI_PROXY`.
- Ozon may still show its anti-bot page; `ozon-setup` refreshes the saved session.
- Review velocity is displayed but not scored until snapshots are collected on a regular schedule.
- Kimovil: a burst of requests can get the IP challenged by Cloudflare for a while; until it clears, only cached
  phone specs are used. A Kimovil page describes the model's base variant, so RAM and storage come from the
  listing, and a title without "5G" may resolve to the 4G sibling.
- The marketplace endpoints are unofficial and can change without notice.

## License

[MIT](LICENSE). Third-party code is listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
