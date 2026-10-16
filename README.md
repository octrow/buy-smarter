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
For every pick it shows which axes scored it and why. It runs as a CLI and as an MCP server for Claude.

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
  Postgres, or to `~/.config/buysmarter/knowledge/<category>.json` when Postgres is down.
- **Phone specs from Kimovil.** For smartphone queries each model is looked up on Kimovil: its overall index
  (0–100) goes into the specs axis ("Kimovil 83/100"), and refresh rate, battery, fast charging, chipset, AnTuTu
  and IP rating are added in Kaspi's wording, so "120 Гц" or "5000 мАч" as a required feature can match. Results
  are cached in `~/.cache/buysmarter/kimovil.db` (30 days; misses 7 days).
- **Price history.** `refresh` and `history` record price and review-count snapshots per product.
  Kaspi has no public Keepa-style tracker.

Closest open-source projects and what was borrowed from them: [docs/analogs](docs/analogs/README.md).

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

The installer gets [uv](https://docs.astral.sh/uv/) (it brings its own Python), installs the latest release,
offers the browser for Ozon and runs `buysmarter doctor`, which says what is missing and what that turns off.
Re-run it to update; `sh -s -- --uninstall` removes it. Prefer to read it first? Download `install.sh`, read it,
then run `sh install.sh`. API keys and a proxy go in `~/.config/buysmarter/.env` (see [.env.example](.env.example)).

From source, for price history in Postgres and for development
([uv](https://docs.astral.sh/uv/) and Docker):

```bash
git clone https://github.com/octrow/buy-smarter && cd buy-smarter
uv sync
uv run buysmarter doctor      # tells you what is missing and what that turns off

# Optional pieces
make up && uv run alembic upgrade head   # Postgres :5434 + Redis :6380: history and preferences
uv run playwright install chromium       # Ozon source
uv run buysmarter ozon-setup             # pass Ozon's anti-bot check once in a visible browser
cp .env.example .env                     # optional API keys and proxy
```

Every piece is optional: without Postgres, writes are skipped; without the `claude` CLI, `--no-llm` falls
back to a naive parser. In a clone, prefix the commands below with `uv run`.

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
| `recommend` | Full pipeline → markdown report (best pick with axes, alternatives, top N, OLX used). Parameters: `query`, `city`, `budget`, `sources`, `trusted_only`, `top`, `parse_with_llm` (default `False`: Claude parses the request itself). |
| `search_products` | Raw search on one marketplace, no ranking (title, price, rating, url). |
| `compare` | 2–4 comma-separated products → side-by-side specs (Kaspi). |
| `get_preferences` / `set_preference` | Read/write preferences (city, budget…). Postgres, or `~/.config/buysmarter/prefs.json` when it is down. |

## Docker

```bash
docker compose up -d --build        # Postgres + Redis + migrations + API on :8000 (/health); keys come from .env
docker compose run --rm app buysmarter recommend "…" --budget 30000
docker compose --profile browser up -d --build app-browser   # with Chromium for Ozon, on :8001
docker compose down                 # add -v to drop the data
```

Prebuilt images: `ghcr.io/octrow/buy-smarter:latest` (releases), `:main` (every commit to main), `-browser`
suffix for Chromium. `TAG=main docker compose pull && docker compose up -d` runs them without building.
Check where an image came from with `gh attestation verify oci://ghcr.io/octrow/buy-smarter:latest -R octrow/buy-smarter`.

The image has no `claude` CLI: LLM calls use the API fallback (`DEFAULT_LLM_MODEL` plus a provider key in `.env`).
Without compose: `docker build -t buysmarter .` (`--target browser` for Chromium), then run `alembic upgrade head`
from the same image before the API.

## Kubernetes

A Helm chart lives in [`deploy/helm/buysmarter`](deploy/helm/buysmarter). Postgres is yours (managed or in-cluster):

```bash
helm install buysmarter deploy/helm/buysmarter -n buysmarter --create-namespace \
  --set secret.data.DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/buysmarter
helm test buysmarter -n buysmarter
```

Migrations run as a pre-install/pre-upgrade hook Job, once per release. Probes: `/health` (liveness, no
dependencies) and `/ready` (readiness, 503 until the database answers). Pods pass the Pod Security Standard
`restricted`. Keys go in `secret.data` or in your own Secret (`secret.existing`). CI installs and upgrades the chart in
kind on every PR ([`deploy/kind/smoke.sh`](deploy/kind/smoke.sh)); the smoke test never calls the marketplaces.

## Observability

The API serves Prometheus metrics on `GET /metrics` (OpenTelemetry SDK with the Prometheus exporter): HTTP request
rate and latency by route, plus `buysmarter_recommend_duration_seconds` (by outcome), `buysmarter_source_requests_total`
(by marketplace and outcome), `buysmarter_llm_calls_total` (by model, cache hit or miss) and
`buysmarter_recommend_offers_ranked`. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to also export traces (FastAPI, httpx calls to
the marketplaces, SQL) over OTLP/HTTP; `BUYSMARTER_METRICS=0` turns `/metrics` off.

```bash
make observe   # the compose stack + Prometheus :9090, Grafana :3000 (dashboard "buy-smarter"), Jaeger :16686
```

The chart adds `prometheus.io/scrape` annotations, an optional `ServiceMonitor` (`metrics.serviceMonitor.enabled`) and
`otel.endpoint` for traces.

## Development

```bash
make check   # ruff + pytest (no network needed; db tests skip without Postgres)
make test
make up / make down
```

Layout: `src/buysmarter/{normalize,db,llm,ranking,services,cli,knowledge,mcp_server}`. The marketplace
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
