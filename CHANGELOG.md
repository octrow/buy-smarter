# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- The desktop app's search form has the CLI's options: "Сводка отзывов" (`--no-review-summary`) and "Только
  известные бренды" (`--trusted-only`), in a new "Как искать" row with the AI checkbox, now "AI: разбор запроса"
  (CLI ↔ GUI parity #2–3).
- `buysmarter prefs`: show, `set` and `clear` the saved search settings (city, budget, marketplaces, excluded
  brands) with the same checks and messages as the app's «Настройки поиска» (CLI ↔ GUI parity #9).

### Changed
- A new category is learned before its first search in the desktop app and the CLI (ADR-0006, finding #26): the
  first result already has its brands, models and ingredients. The desktop app shows "Изучаю категорию «…» с AI
  (1–3 мин, платно)" with a timer; "Искать без изучения" searches now while the learning goes on, "Отмена" stops
  both. The CLI offers the same choices on a terminal (`cli/learning`): learn first (default yes) or search now
  while it learns in the background, then "Повторить поиск?"; a report without books offers to learn by hand, as
  the app's button; `--learn` learns before the search. A failed learn searches without the
  knowledge and says so. A used-up hourly limit (or `BUYSMARTER_AUTO_LEARN_PER_HOUR=0`) is said in the report.
  MCP, the HTTP API and the worker still learn after the report.

### Fixed
- The CLI searches the saved marketplaces when `--sources` is not given (else all three) and excludes the saved
  brands, as the desktop app (CLI ↔ GUI parity #8).
- The CLI shows the search's progress on a terminal, as the desktop app: the running stage in Russian, then
  "Найдено предложений: N, б/у на OLX: M." above the report (CLI ↔ GUI parity #21).
- The CLI's OLX table has the app's columns: what each ad was compared with, and the saving without a minus
  under "Дешевле нового на" (CLI ↔ GUI parity #22).
- The CLI report says what could not be checked, as the desktop app: a marketplace whose search failed
  (also in the saved report and MCP, new `failed_sources` in the result), why there is no review summary or that
  the AI was off, and an unchecked composition (CLI ↔ GUI parity #18–20).
- The CLI report reads in Russian, as the saved report and the desktop app: headings, axis names and
  explanations, pros and cons, marketplace names, the hard-filter count (CLI ↔ GUI parity #14).
- In the desktop app a city or budget named in the query beats the saved ones again: the city and budget
  fields stay empty and show the saved values as hints ("из запроса, иначе …"), and the report says "(по
  умолчанию)" for a saved city. The core now falls back to the saved budget after the query, as it does for the
  city, so the CLI uses it too and does not ask for a budget when one is saved (CLI ↔ GUI parity #1).
- A cancelled learn no longer blocks auto-learning of its category for 15 minutes.

## [0.8.0] - 2026-10-24

### Added
- "Только б/у" (phase 4): "б/у ноутбук" adds OLX by itself, before the marketplaces are opened (even when the form or `--sources` did not name it), and every report shows the OLX section first (markdown, console, desktop app, MCP). The note "Вы искали б/у: объявления с OLX идут первым разделом, новые товары ниже." replaces the old "смотрите раздел" line. The desktop app now shows the progress of a marketplace a request opened for you.
- Category switches "Рынок б/у" and "Официальные магазины" (phase 4) in the desktop app's "Как оцениваем эту категорию" group: on by default, off for a category by learning or by you. Off, the used market takes OLX out of the marketplaces (a used query keeps it) and the official stores add no brand-site offers; their sections and the shared listing check stay in the core. The "Как оценивали" line names them only once learning or you decided ("рынок б/у — выключено: обучение").
- "Только оригинал" (phase 4): "пылесос Dyson только оригинал" hides the suspected fakes instead of showing them with a warning. They count as rejected ("подозрение на подделку"), every report lists them ("Скрыто подозрительных предложений: N": markdown, console, desktop app, API `hidden`, empty for results stored before it), and when every offer is suspected the run ends as "no new pick" with the OLX ads. "Оригинальный подарок" does not switch it on.
- A line "Как оценивали: состав — обучение; бенчмарк Kimovil — Jev." (phase 3) in every report: the markdown and console reports, the desktop app and the API (`assessed_by` in the job outcome, empty for results stored before it). It names each active capability and who decided it: learning, Jev, a rule, the request or you.
- A group "Как оцениваем эту категорию" on every report of the desktop app (phase 3): a switch for each category capability (composition, benchmark) that names who set it (learning, Jev, a rule, you) and a reset to automatic. A switch is saved to the request's own category profile, never to the related profile it was found through, and only offers a new search.
- Auto-learning of a new category in the desktop app (phase 3): after a report with no brand data the app learns the category in the background, kept per category (a new report does not touch another's learning), asks before closing the window while one runs, and turns the button into "Повторить поиск" when the category on screen is learned.
- Auto-learning of a new category (phase 3) in the MCP server, the HTTP worker and the CLI. MCP learns in the
  background after a `parse_with_llm=True` recommend and tells the client to repeat in a few minutes. The CLI asks
  "Изучить категорию сейчас?" after the report on a terminal (`--learn` learns without asking) and offers to repeat
  the search. The worker does it only with `BUYSMARTER_AUTO_LEARN=true`: a separate `buysmarter.learn_category`
  task (600 s limit, one per category at a time), queued after the recommend job is finished. Everywhere at most
  10 starts per hour (`BUYSMARTER_AUTO_LEARN_PER_HOUR`); `0` turns auto-learning off.
- Request understanding (ADR-0005, phase 2): the parse returns the request intent (product, need, open, multi,
  service, unclear), the search phrase, the product type, options, a restricted flag and the condition. An ambiguous
  request gets a question instead of a ranking, at most two rounds; on the last one the first option is searched
  and the report says "Искал: …". The CLI asks on a terminal before the budget question (exit code 3 off a
  terminal, `--no-clarify` never asks), the desktop app shows a card with option buttons and an own-answer field,
  MCP and the HTTP API take `answer`, `clarify_round` and `no_clarify`.
- Restricted goods (alcohol, tobacco and vapes, weapons; prescription medicines with an LLM) are searched with a
  warning on the first line of the report, never refused. Without an LLM a whole-word list flags them.
- The budget range ("30-40 тыс") and a note for a budget in another currency are shown in the report; "б/у" in a
  query adds a pointer to the OLX section.
- A «Настроить» link next to «Как оценивали» at the top of a report in the desktop app scrolls to the category switches.

### Changed
- The JSON schema sent to the LLM with each request no longer carries field `description`s and `title`s (types, enums,
  required fields and `$defs` stay): they were 25-45% of a request's text. The Pydantic models keep their descriptions
  and still validate the answer. The LLM cache key holds the model's class name, not the schema text, so the cache stays valid.
- The relevance passes over new goods and over OLX ads run at the same time: one LLM wait instead of two.

### Fixed
- A size chain in a title gives its unit to every side: "Матрас 160x200x22 см" no longer gets the con
  "название: 22 см, характеристики: 160 см".
- "Бессульфатный" (and other "бес…" spellings) reads as "без сульфатов": such a title gets the pro, such a query the exclusion.
- Compatibility (phase 4) no longer reads a spec glued to its unit as a device: "зарядка на 65W", "повербанк на 20000mAh", "флешка на 64GB", "кабель на 2m" get no fit target, also when the parser returns one.
- Excluded substances (phase 4) are read from the whole "без X и Y" list: "шампунь без сульфатов и парабенов" and "без сульфатов, парабенов" exclude both, and the list leaves the product type and the relevance phrase.

## [0.7.0] - 2026-10-23

### Changed
- The review summary asks the LLM for the shopper text only (pros, cons, complaints, verdict, red flags); Jev alone
  decides counterfeit suspicion, reliability and the required-feature checks, as it already overrode them. A shorter
  answer: the review stage took 23 s on a live run. `ReviewSummary.negative_share_estimate`, read nowhere, is gone.
- `learn` reads long notes in chunks of 10 list items, each headed by the notes' intro, and merges the answers: notes
  listing ~30 phone models no longer get a cut-off LLM answer. Short notes are still one call with the same prompt.
- The category profile is keyed by the product type: «мужские кроссовки 42» and «кроссовки» use one profile. An old
  record keyed by the whole phrase moves under the product type on first use when they differ only by audience or
  size words (logged as `knowledge renamed`); a narrower record such as «шампунь-от-перхоти» is used as is.
- HTTP API: `result.constraints` is now a public `ParsedRequest` with the new parse fields; `category` stays as a
  deprecated copy of `search_phrase`. A clarification is `no_match` with `reason: "needs_clarification"` and
  `result.clarification`; the three terminal statuses are unchanged. Results stored before this change still load.
- `QueryConstraints.category` is gone; read `search_phrase` (what is searched) or `product_type` (the profile key).

### Fixed
- The budget no longer reads units as money: «до 2 м», «до 1.8 м», «до 5000 МЕ», «телевизор 4K» have no budget,
  «до 15000 3 упаковки» is 15 000 (was 150 003), «до 50 штук» is 50 000 and «штук» no longer stays in the search.

## [0.6.1] - 2026-10-23

### Changed
- Model names for OLX ads come from Jev's confident picks only; the LLM runs only when Jev itself fails. Asked on
  every unsure title it cost 40-60 s per search and once named every «iPhone 13» ad «Apple iPhone 17 Pro Max».
- A related category is found by Jev only: when Jev is unsure there is no related category, instead of a 40-60 s
  LLM call that answered «чехол для iPhone 15» → «смартфон».
- `learn` says to split the notes into smaller files when the LLM answer comes back cut off.

### Fixed
- A model title resolves to the entry with the longest matching alias: «Galaxy S24 FE» is the S24 FE, not
  the S24, whatever order the models were learned in.
- Kaspi: seller offers are fetched for 8 cards even when the brand-targeted search finds fewer than 4, so more
  main-search cards carry their cheapest seller price instead of the search-card price.
- The city reaches OLX: a search for Astana or Shymkent no longer lists Almaty ads. Kaspi and OLX know 21 cities
  (Russian or Latin names) instead of Almaty and Astana only; MCP `search_products` passes the city to OLX too.
- A brand after a compatibility word is the device the item fits, not its maker: «Чехол для Apple iPhone 15»,
  «Ремешок на Apple Watch» and «Кабель к iPhone 15 Pro» are no longer flagged «возможная подделка под Apple», and a
  case with no brand field no longer borrows Apple's trust tier from its title. Only the device is skipped: in
  «Наушники для iPhone Apple AirPods» Apple is still read, and «под Nike» on a no-brand card still reads as a replica.
- OLX: when both relevance classifiers (Jev and the fallback LLM) fail, only ads naming the queried model stay;
  the used section no longer fills with every ad the search returned.
- Candidates kept for a confirmed substance count towards the relevance floor, so five matching gels no longer bring
  back three dropped accessories.
- Dedupe merges only across marketplaces: two Kaspi cards, or two items of one official store («Puma RS-X» and
  «Puma Smash»), are no longer merged into one candidate. «Galaxy S24» and «S24 Ultra», «Note 13 Pro» and
  «Note 13 Pro+» no longer merge either.
- OLX: a model the LLM names for an ad is dropped when its model number, line or storage contradicts the ad's title
  (every «iPhone 13» ad was once named «Apple iPhone 17 Pro Max»), so such a name is never learned as an alias nor
  shown.

## [0.6.0] - 2026-10-22

### Changed
- Category knowledge is read as a category profile: books, the capabilities switched on (composition, benchmark) and
  an optional parent the profile inherits missing fields from (ADR-0005, phase 1). The ranking with an exact category
  match is unchanged; the composition, Kimovil, audience and size branches of the core moved onto capabilities.
- A related category is found once per run: exact name, then a profile's synonyms, then Jev (the LLM when Jev is
  unsure). No substring matching any more: «чай» no longer borrows «чайник»'s brands and price floors. Without an
  LLM only the exact name and synonyms match.
- Saving category knowledge merges into the stored record instead of rebuilding it; `generated_by` keeps the first
  writer and `updated_by` names the last.
- `learn --file` merges the notes with the category's own record only, never a parent's or a related category's.
- `Candidate.kimovil_index` is now `benchmark_index` (with `benchmark_label`, «Kimovil»).

### Fixed
- OLX: an ad padded with the shop's name («iphone 13 Shark Store») now matches the new iPhone 13 on the shared
  model number, and a no-name ad for another model number («TWS Pro 4» for «TWS Pro 6») is no longer offered
  as a cheaper option.
- OLX: the saving is counted against a new product with the same storage («128 GB» against 128 GB, not 512 GB);
  an ad whose storage no new product has shows without a saving. Kaspi and Ozon listings with different storage
  are no longer merged as one product.
- OLX: «iPhone 13 Pro» and «Pro Max» ads are no longer matched to the plain iPhone 13 (likewise Mini, Plus, Ultra,
  Lite).
- A listing whose title names the queried model («Iphone 13 128 gb midnight» for «смартфон iPhone 13») is no longer
  dropped by the relevance check, which scored such titles anywhere from 0.2 to 0.6 from run to run.
- OLX aliases learned for a borrowed composition category («крем с адапаленом» using «косметика») no longer create a
  spec-driven record under the query that hid the substance search from then on: only the model book is written,
  to the profile that owns it. Records this bug already created are healed once on read: they become children of
  the composition profile and their aliases move there (logged as `knowledge healed: <slug> -> parent <slug>`).

### Note
- Until phase 3, records still get the old `category_type` and `ingredient_book` keys next to `capabilities`, so an
  older app version reads them. Drop that dual write after phase 3.

## [0.5.0] - 2026-10-22

### Added
- When no new product passes the filters (every one over the budget, say), the search no longer ends with «no
  candidates passed hard filters»: the CLI, the desktop app, MCP and the API jobs say why («Новых предложений нет:
  дороже бюджета ×2») and list the OLX ads within the budget, with the saving against the rejected new ones. The
  API job stays `no_match` and adds `result.rejected_reasons` and `result.used_listings`; the CLI exits 0 when there
  are ads.
- OLX: an ad priced under 30% of the median ad for the same query (at least 5 ads) is dropped as bait («СРОЧНО!
  iPhone 13» at 35 000 ₸ next to 125 000).
- The log records every relevance score against its threshold and every title restored to meet the pool floor
  (`relevance score: 0.920 keep threshold=0.25 category='…' title='…'`), the data the thresholds are tuned on.
- `scripts/screenshots.py` grabs every desktop screen in light and dark offscreen; CI uploads them as an artifact.
- Release candidates (`vX.Y.ZrcN`) build a GitHub pre-release and skip PyPI.

### Changed
- OLX relevance threshold 0.55 → 0.35: at 0.55 it dropped most used iPhone 13 ads (scored 0.23-0.54), so «iphone
  13 б/у» found nothing. Tuned on 14 labelled queries.
- The relevance question treats the category's words as required properties: over-ear and wired headphones no
  longer pass for «tws наушники», wired in-ear monitors for «TWS наушники внутриканальные».
- An OLX ad's saving is counted against the cheapest matching new product, not the priciest variant.

### Fixed
- Titles that differ only in a model number («iPhone 12» / «iPhone 13») no longer match: an OLX ad for another
  model is not paired with a new one or shown as notable, and two new listings are not merged as duplicates.
- Desktop app: every card shares one inner padding, card titles line up with their content, report tables span
  the page, and «Сохранено» sits beside the «Сохранить» button instead of padding the card.
- Composition-driven categories (medicines, cosmetics): "composition not checked" no longer counts as "substance
  absent". A failed ingredient extraction leaves candidates unchecked instead of rejecting every listing with a
  composition text or stopping the run with «Не найдено товаров с действующим веществом»; a title naming the
  substance or its equivalent («Клензит») counts as checked. Without extraction (`--no-llm`, MCP and API by
  default) every unchecked candidate gets the same 7.5 of 25 on the ingredient axis with «состав не проверен», and
  the report (CLI, MCP, desktop app) says «Состав не проверен у N из M — вещество не подтверждено».

## [0.4.0] - 2026-10-22

### Added
- Ranking: counterfeit checks for branded listings. The card's own brand field wins over the title: an unknown or
  «Без бренда» card naming a known brand or model line in its title («PODPODOSHVOI yeezy», «XAN Air Jordan 4»)
  is a brand mimic, barred from every role. A known brand far below the genuine item's price (the model's official
  price, else the brand's cheapest original) is suspicious below 0.4 and lowers confidence below 0.7; official
  sellers and OLX are exempt. Reviews are read from the negative page too, and «подделка/реплика/копия/паль/фейк»
  (also Kazakh) mentions bar the listing; reviews that failed to load are reported.
- Official brand stores as a source: store catalog pages are read through their schema.org JSON-LD, so their
  cheapest in-budget products join the ranking («Офиц. магазин», e.g. puma.kz from 13 000 ₸), and `learn-category`
  stores each store's lowest price as the brand's genuine-price floor. Knowledge also keeps official marketplace
  sellers and model reference prices; product lines are no longer brand aliases, 1-2 letter aliases are dropped.
- Search: the query's audience («мужские», «женские», «детские») filters out listings for another audience, by
  title and the marketplace category; a size («42 размер») is parsed, kept out of the search phrase and reported as
  unverified.
- Desktop app: «Прошлые поиски» lists the saved reports, newest first, and shows the selected one. Each search in
  the app now saves the same markdown report as the CLI's `search`, to the same folder
  (`~/.local/share/buysmarter/reports/`), so CLI runs show up there too.
- Desktop app: when a category has no brand knowledge, the report says so and offers «Изучить категорию»
  (the CLI's `learn-category`, with a timer) and then «Повторить поиск».
- Desktop app: the report shows the review summary, or why there is none; «История цен» opens on the recently
  tracked products with their last price change, draws a price line per product and lists the biggest drops
  after «Обновить цены»; the search screen says what each marketplace brought and links back to the report.

### Changed
- The markdown report and CLI prices are in Russian / «35 000 ₸»; the OLX section is «На OLX (частники)» with a
  «попросите фото бирки и чек» hint for trusted brands; OLX ads pass a stricter relevance check («туфли» in a
  sneaker search); a pricier copy on another marketplace is no longer a pro; a listing without brand or model
  takes no role while named products compete. Needs market-sources 0.4.0 (Kaspi brand, configSku, partner flag).
- Ranking: «Best budget» goes only to an offer cheaper than the best pick. Without required features the specs
  axis is neutral (half credit for everyone), so a marketplace that ships a spec sheet (Ozon) no longer outranks
  one that does not (Kaspi search).
- Desktop app: the report is in Russian throughout (headings, columns, axes, roles, pros and cons, axis reasons),
  titles drop a doubled first word, marketplaces are capitalized, the best pick is labeled in the alternatives,
  per-axis columns are behind «Баллы по осям», axis bars are colored by strength, confidence explains itself in a
  tooltip, the used-market table shows the saving without a minus and what it was compared with, and «Отсеяно»
  appears only when something was. Pages stop at 1200 px on wide screens.

### Fixed
- Desktop app: wrapped table rows no longer clip the last row in a narrow window or leave a blank strip in a wide
  one; the sort arrow points down for a descending sort.

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
