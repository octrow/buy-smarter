# Logic map

How one request becomes a ranked answer.
The map describes the code as it is today, step by step.
Each step says what it decides, where the flow branches, and which module does it.

Read `CONTEXT.md` first for the terms.
Known gaps and fixes in progress are in `docs/plans/2026-10-22-logic-map-fixes.md`.

Rule: if you change a step or a branch, update this file in the same commit.
`tests/test_logic_map.py` checks that every `Module:` line points to a real module.
The Russian mirror is `docs/logic-map.ru.md`; this file is the source of truth.

## The flow at a glance

```
query text
  │
  ├─ 1  LLM route ............ is there an LLM at all?
  ├─ 2  parse ................ text → constraints (intent, search phrase, product type, budget...)
  ├─ 2a clarify .............. ambiguous request → a question instead of a ranking
  ├─ 3  city ................. override > query > stored preference > Алматы
  ├─ 4  clean constraints .... capabilities read the query (audience, size, fit, dims, exclude, original_only); implied features out
  ├─ 5  sources .............. Kaspi / Ozon (needs a browser) / OLX
  ├─ 6  category profile ..... books, capabilities, who owns each; capabilities switched on;
  │                            a capability may ask (runs before step 5: no browser for a question)
  ├─ 7  extra searches ....... from capabilities (composition: substances)
  ├─ 8  collect .............. three marketplaces in parallel
  ├─ 9  normalize ............ raw card → Candidate
  ├─ 10 official stores ...... brand sites as extra candidates
  ├─ 11 history .............. price history, review velocity
  ├─ 12 relevance filter ..... drop accessories and off-category items
  ├─ 13 dedupe ............... same product → keep the cheaper
  ├─ 14 used filter .......... OLX ads: capability listing checks + strict relevance
  ├─ 15 benchmark ............ capability enrich: Kimovil for phones
  ├─ 16 ingredients .......... capability enrich: composition; may stop the run
  ├─ 17 rank ................. hard filters → axes → score → pros/cons
  ├─ 18 roles ................ best, best value, budget, premium
  ├─ 19 review pass .......... reviews may change the best pick
  ├─ 20 persist .............. snapshots for future price history
  └─ 21 used market .......... OLX section next to the ranking
  │
RecommendResult
```

Kaspi and Ozon feed the ranking.
OLX never enters the ranking; it only feeds the used-market section.

## Capabilities

The core flow is the same for every category. What differs are capabilities (CONTEXT.md):
optional hooks the core calls at fixed points, in registry order (audience, size, fit, dims, exclude, original_only, used_only, used_market, official_stores, benchmark, composition).
A hook a capability does not implement is not called.
`ranking/` never imports capabilities: the flow hands `rank()` plain weights, predicates and functions (`RankHooks`).

| Capability | Level | Switched on by | Hooks it uses (steps) |
|---|---|---|---|
| audience | request | `constraints.gender`: the parser, or a regex over the query text (step 4) | `query` (4), `reject_listing` on the main pool, OLX ads and official-store offers (10, 14, 17) |
| size | request | `constraints.size`: the parser, or a regex over the query text for apparel (step 4) | `query` (4: the size leaves the search phrase and apparel features), `notes` (17: "size unverified") |
| fit | request | `constraints.fit_target`: the parser, or "для/на/под/к <device>" in the query text (step 4) | `query` (4: the device joins the search phrase and leaves the product type), `notes` (17: "подходит к <device>"); the model penalty (17) looks for the whole device model |
| dims | request | `constraints.dims` (tyre or mattress size: the parser, or a regex over the query for tyres and mattresses), or tyres or a mattress named without a size (step 4) | `query` (4: normalized size into the search phrase, out of the features), `clarify` (6: tyres or a mattress without a size; the answer joins the query, which is read again), `reject_listing` (10, 14, 17: another size of the same kind in the title), `reject_content` (17: the size specs say another size), `notes` (17: "размер не проверен") |
| exclude | request | `constraints.exclude_substances`: the parser, or "без X", "безX…", "бесX…", "X-free", "free of X" in the query for a known substance (сульфаты, парабены, сахар, лактоза) (step 4) | `query` (4: canonical names; the phrases leave the required features, the substances composition reads and the relevance phrase, but stay in the search), `reject_content` (17: the composition text names the substance outside a negation), `notes` (17: "состав не проверен на X" without a composition text; "без X по названию" when the title says so) |
| original_only | request | `constraints.original_only`: the parser, or "оригинал" / "только оригинал" in the query text, not "оригинальный" (step 4) | `query` (4: the word leaves the search phrase, the product type and the required features), `drops_barred` (19: after the review pass the core hides the candidates barred from roles, counts them as rejected and lists them in every report) |
| used_only | request | `constraints.condition == "used"`: the parser or a condition word in the required features ("б/у", "бу", "used") (step 4) | `sources` (5: adds OLX to the marketplaces, before they are opened, unless it is there); the report shows the used section first, in the CLI, the desktop app, markdown and MCP (21) |
| used_market | category | on unless the profile says off: no entry means on (`set_by: fallback`); learning or the user's switch turns it off | `sources` (5: takes OLX out of the marketplaces when off; a used query keeps it). Collecting, filtering and both used sections stay in the core |
| official_stores | category | on unless the profile says off (no entry means on, `set_by: fallback`) | `candidates` (10: `official_candidates` with the shared `reject_listing`, so every request rejection reaches the store offers) |
| benchmark | category | the profile, only with a provider from `BENCHMARK_PROVIDERS` (its parameter, else the provider whose rule takes the product type); without a profile setting, the providers' rule (`set_by: fallback`) | `enrich` (15: Kimovil specs and `benchmark_index`) |
| composition | category | the profile: the composition capability with an ingredient book (old records: `category_type: composition_driven`) | `searches` (7), `relevance` (12), `hard_features`, `reject_content` (17), `enrich` (16, may stop the run), `prepare` + `axis` + `notes` (17), `section` (Output) |

Weights: base weights plus each capability's axis and shifts, clamped at zero, normalized to 100 (`capabilities/weights`).
Composition gives the old composition table exactly (ingredient +25, brand −15, specs −5, popularity −5).

Examples:
- "крем с адапаленом" → composition (if the profile has it): searches by substance, rejects products whose composition proves the substance absent.
- "мужские кроссовки 42 размер" → audience and size; gender is a listing check everywhere, size an unverified con.
- "смартфон iPhone 13" → benchmark by the providers' rule (Kimovil); "чехол на iPhone 15", "держатель телефона" → none.
- "чехол на iPhone 15" → fit: the device joins the search phrase; "шины 205/55 R16" → dims (asks the size when the query has none); "шампунь без сульфатов" → exclude; "пылесос Dyson только оригинал" → original_only (suspected fakes hidden); "б/у ноутбук" → used_only (OLX added, its section first).
- A category with no profile at all → no category capability, every brand scores as unknown.

Module: `capabilities/base`, `capabilities/flow`, `capabilities/weights`, `capabilities/audience`, `capabilities/size`, `capabilities/fit`, `capabilities/dims`, `capabilities/exclude`, `capabilities/original_only`, `capabilities/used_only`, `capabilities/used_market`, `capabilities/official_stores`, `capabilities/benchmark`, `capabilities/composition`

## Entry points

All entry points call the same `recommend()`.
They differ in defaults.
`use_llm=False` switches off every LLM step of the run, not only parsing.

| Entry | Sources by default | LLM by default | Notes |
|---|---|---|---|
| CLI `recommend` | `--sources`, else the saved marketplaces, else kaspi, ozon, olx | on | merges the saved excluded brands into the parse, as the app (`memory/prefs.search_defaults`, `services/recommend.with_excluded`); on a terminal shows the running stage as the app does (the core's stage records, `cli/progress`), then "Найдено предложений: N, б/у на OLX: M." above the report; the notes under the request (`cli/report.report_notes`, also the saved `.md`) include the app's "нет данных о брендах" banner text; asks a clarification first (number or own text, next round), then a missing budget (not when one is saved), then whether to learn a new category before the search (`[Y/n]`, the desktop app's choices: no searches now while it learns in the background, then waits and asks "Повторить поиск?"; `--learn` learns without asking, also off a terminal; a failed learn searches without books; a report without books offers to learn by hand, then the rerun; a used-up hourly limit is said after the report); off a terminal a question exits with code 3, nothing to search with 1; `--no-clarify` searches the first option; a capability question's answer (tyre size) always joins the query, even when an option is picked; exit codes: marketplace HTTP error 2, `NoNewPick` 0 with OLX ads else 1 |
| Desktop GUI | user picks | on (the form's "AI: разбор запроса") | calls `recommend()` in-process; the form's "Сводка отзывов" (off without the AI) and "Только известные бренды" go as `with_review_summary` and `trusted_only`, the CLI's `--no-review-summary` and `--trusted-only` (a summary turned off is not reported as missing); the city and budget fields stay empty with the saved values as hints, so the query's city and budget beat the saved ones (steps 3, 4); merges excluded brands from settings into the parse; a clarification shows as a card under the form (option buttons, own answer: an option goes back as `answer`, own text joins the query; next round; "Искать без уточнений" repeats the search with `no_clarify`, as Enter in the CLI, and the rerun after learning keeps it; the screen scrolls when a long question does not fit); `restricted_note` heads the report; "Изучить категорию" learns the product type, and the search it reruns keeps the answer and round; a new category is learned before its search (`recommend(learn_first=True)` raises `NeedsLearning`; the search screen shows "Изучаю категорию «…»" with a timer, "Искать без изучения" searches now while the learning goes on, "Отмена" stops both, a failed learn searches without books and says so next to the counts); a report with no brand data starts no learning: it shows the running one or the "Изучить категорию" button (off when the run skipped LLM steps) and says when the hourly limit is used up or learning is off (kept per category in `gui/learning`, shared by the search and report screens; closing the window asks while one runs; when the on-screen category is learned the button becomes "Повторить поиск"); every report carries the group "Как оцениваем эту категорию" (`gui/capability_toggles`): a checkbox per category capability with its source in the text and a reset to automatic, written to the request's own profile on the core loop (the related profile found becomes its `parent`, never written to); a change only offers "Повторить поиск" |
| MCP `recommend` | kaspi, ozon, olx | **off** (`parse_with_llm=False`) | passes `city or preference or Алматы` as an override: a city in the query is ignored, `city_defaulted` is always false; a clarification comes back as markdown telling the host to ask the human and call again with `answer` and `clarify_round` + 1 |
| HTTP API + worker | `api_sources`, default kaspi + olx (no Ozon) | **off** (`use_llm=False`) | job queue in Redis; the same request within 15 minutes returns the same job; 10 new jobs per 600 s per client, else 429 with `Retry-After`; `X-API-Key` required when `BUYSMARTER_API_KEYS` is set; job timeout 300 s; auto-learning needs `use_llm=true` in the request and `BUYSMARTER_AUTO_LEARN=true`; a clarification is `no_match` with `reason="needs_clarification"` and `clarification`, the client posts again with `answer` and `clarify_round` + 1; `constraints` is the public `ParsedRequest` (`category` kept, deprecated) |
| direct call | kaspi only | on | used by tests |

Other MCP tools (`search_products`, `compare`, `get_preferences`, `set_preference`) do not call `recommend()`. The saved search settings (city, budget, marketplaces, excluded brands) are edited on the app's «Настройки поиска» screen and with `buysmarter prefs` (show, `set`, `clear`; the same keys, checks and messages, `cli/prefs`), and read through `memory/prefs`. The learned categories are listed and shown, and their «Как оцениваем эту категорию» switches set and reset, with `buysmarter category` (`list`, `show`, `set`, `reset`; `cli/category`), without learning: `show` resolves the profile with no LLM, `set`/`reset` write `set_user_choice` to the category's own record. The app's «Категории» screen shows the same data (`category_rows`, `category_detail`) and learns by hand through the shared `CategoryLearning` (`learn_category` with or without web search, `learn_from_notes`), not counted in the hourly limit; notes cut off by the LLM say to split them, as `learn` (`NOTES_TOO_LONG`).

Module: `cli/main`, `cli/console`, `cli/progress`, `cli/prefs`, `cli/category`, `cli/clarify`, `cli/learning`, `cli/report`, `gui/screens/search`, `gui/screens/clarify`, `gui/screens/report`, `gui/screens/categories`, `gui/learning`, `gui/capability_toggles`, `mcp_server/server`, `api/recommend`, `worker`, `jobs`, `services/auto_learn`

## Steps

### 1. LLM route

Decides: whether this run can use an LLM at all.
Branches:
- a route exists (API key in env or keyring, or the `claude` CLI) → LLM steps on;
- no route → run goes no-LLM and reports the skipped steps (query parsing, review summaries, ingredient extraction);
- user opted out (`--no-llm`, MCP/API defaults) → no-LLM, nothing reported as skipped;
- in both no-LLM cases these also stop: the LLM relevance pass, related-category matching by LLM, OLX title canonicalization (the skipped-steps list names only the three above).
Module: `llm/adapter`, `llm/keys`

### 2. Parse the request

Decides: the request intent, the search phrase, the product type, budget, city, required features, excluded brands, condition.
In: query text. Out: `QueryConstraints`.
Branches:
- LLM: one call extracts all fields: intent (product, need, open, multi, service, unclear), `search_phrase` (what the marketplaces get), `product_type` (the profile key without audience, size or theme), `options` to choose from, `restricted`, `condition`, budget range and note, `fit_target` (the device the goods are for), `dims` (a tyre or mattress size, never a required feature), `exclude_substances` ("без сульфатов" → сульфаты). The phrase is forced into Russian; a named model stays in it ("смартфон iPhone 13"), unless the goods are for it ("чехол на iPhone 15": phrase and type "чехол", fit target "iPhone 15"). An empty product type falls back to the naive one; no budget and no budget note from the LLM means the budget rules below decide ("до 50 штук" the LLM missed is still 50 000).
- No LLM or LLM error: intent is product (unclear when the phrase has fewer than two letters, an empty phrase included: «хочу до 20к» is never searched as «хочу»); regex takes budget, city and condition (б/у, новый); the rest of the text becomes the search phrase, and the product type is that phrase without audience and size words; no required features on purpose.
Budget rules (no LLM): another currency ($, ₽, €) gives no budget, only a note; tenge (and Kazakh "теңгеге"), thousands slang ("тыс", "косарь", "мың", "мыңға", "штук" after "до/за"), "млн" (×1 000 000) and "N k" from 10 up are money; Kazakh "дейін" after the amount means "до"; a bare "до N" is money only without a unit after it (м, кг, лет, МЕ, Вт, шт...) and under 1000 means thousands; a fraction is never money ("до 18.5 кг"), "15.000" is 15 000; "30-40 тыс" and "от 30 до 40 тыс" are ranges, a bare "N до M" is not ("iPhone 15 до 500 тыс": 15 stays in the phrase). The money words leave the search phrase.
The search phrase drives searches, relevance and the substances read from the query; the product type keys the category profile (step 6). The old `category` field is gone from the parse; the HTTP API still shows it as a deprecated copy of the search phrase.
Module: `llm/prompts`, `llm/budget`, `services/recommend`

### 2a. Clarify before searching

Decides: whether the request is searched, or answered with a question (`NeedsClarification`) or a final "nothing to search" (`NothingToSearch`).
In: the parse, the caller's `answer`, `clarify_round` (0, 1, 2) and `no_clarify`.
- An `answer` equal to one of the parse's options narrows the request to it: intent product, search phrase and product type from the answer; budget, city and audience stay; no second parse. Any other answer is added to the query, which is parsed again.
- Restricted goods are flagged here (see step 4), so a question carries the warning too.
- Asks on (blank options dropped first): open with options, need with two or more options, multi with two or more products, service, unclear. Open without options is unclear even with a phrase. Need, and multi with fewer than two options, are searched when they have a phrase ("шампунь от перхоти"), else unclear.
- Last round (2) or `no_clarify`: with options, the first one is searched and reported as `assumed` ("Искал: крем для рук"); without options, `NothingToSearch` (a `ValueError`) with the reason.
- A capability's `clarify` hook asks after step 6 by the same rules; on the last round the search goes on without the parameter.
`NeedsClarification` is not a `ValueError`: each entry catches it first. No LLM: the naive parse is always product (unclear without letters), so only capabilities can ask.
Module: `services/understanding`, `services/recommend`, `capabilities/flow`

### 3. Resolve the city

Decides: which city the search and delivery dates are for.
Order: explicit override → city in the query → stored preference → "Алматы".
MCP always passes an override (see Entry points), so this order never reaches the query there.
Kaspi and OLX get the city as their own city id (`market_sources.cities`, 21 cities, Russian or Latin). An unknown city searches Almaty without saying so; Ozon gets no city.
`city_defaulted` is set when neither the query nor the override named one.
Fails: a dead store means no preference, never an error.
Module: `services/recommend`, `db/repo`

### 4. Clean the constraints

Decides: final constraints before search.
- Restricted goods: when the parse did not set `restricted`, a static whole-word list over the query flags alcohol, tobacco and vapes, weapons (not prescription medicines; "бокал для вина", "кальянный уголь", "травматолог…" are not flagged). The search still runs; `restricted_note` leads the report.
- Every capability's `query` hook runs, before the profile is known (the profile key may fall back to the search phrase these hooks rewrite):
  - audience: "мужские", "детские" read from the query text by regex if the parser missed it, for any category;
  - size: "42 размер", "XL" read out and removed from the search phrase; once the size is known, a bare "42" after an apparel word leaves it too ("мужские кроссовки 42" → "мужские кроссовки", so an old record of that name moves under "кроссовки"); size phrases leave the required features for apparel;
  - fit: "для/на/под/к <device>" read from the query when the parser missed it (the device is Latin with a digit or a known device word such as айфон, Xiaomi, PS5; never a number, a unit or a spec glued to its unit: "64GB", "65W", "2m"; a target like that from the parser is dropped too); the device joins the search phrase and leaves the product type ("чехол на iPhone 15" → search "чехол iPhone 15", type "чехол");
  - dims: a tyre size ("205/55R16", "205/55 Р16", "ZR16", "R16C" → "205/55 R16") or a mattress size ("160х200", "200 на 160" → "160x200", sides sorted), read for tyres and mattresses only (by word start: "шины", "автошины", "резина", "покрышки", "матрас"; not "машина", "резинка", "наматрасник"); every size form leaves the search phrase and the normalized one is appended; size phrases leave the required features;
  - exclude: "без сульфатов", "безлактозное", "бессульфатный", "sulfate-free" give the known substance it names (не "без проводов"); a list is read whole: "без сульфатов и парабенов", "без сульфатов, парабенов" (the parse drops the comma, so a bare space continues the list before a known substance) exclude both; an LLM value maps to the same names, an unknown one is kept as is. Needles are concrete (SLS, SLES, laureth/lauryl sulfate, лаурет/лаурилсульфат; parabens; сахар, сахароза, sucrose, not сахарозаменитель; лактоза, lactose), so "магния сульфат" is no sulfate; a needle after "без", "не содержит", "free of" or before "-free" is skipped.
- Features already implied by the search phrase are dropped ("TWS" in "TWS наушники").
- Condition words (б/у, новый) are dropped: used goods are OLX's job. They set `condition`; "used" switches the used_only capability on (step 5) and adds a report note that the OLX section comes first.
Then CLI/API overrides apply: budget, trusted brands only. Budget order: explicit override (`--budget`, the app's field) → budget in the query → stored preference (`stored_budget`) → no limit.
Module: `services/recommend`, `services/understanding`, `capabilities/flow`, `capabilities/audience`, `capabilities/size`, `capabilities/fit`, `capabilities/dims`, `capabilities/exclude`, `capabilities/original_only`, `ranking/audience`

### 5. Pick the sources

Decides: which marketplaces this run queries.
Branches:
- The caller's list (default Kaspi) goes through every capability's `sources` hook, after the profile is known: used_only adds OLX to a used query ("б/у ноутбук"), once; used_market takes OLX out when the category switched the used market off (the user's choice outranks learning), unless the query is a used one. A source a capability took out is not "unavailable".
- Ozon needs a real browser; none found → Ozon is left out and listed in `unavailable_sources`.
- Kaspi may go through a proxy (`kaspi_proxy`).
Module: `services/collect`, `services/ozon_browser`, `capabilities/used_only`, `capabilities/used_market`, `capabilities/flow`

### 6. Resolve the category profile

Decides: brand book, model book, the capabilities switched on, and which profile owns each of them.
In: the product type (the search phrase when the parse gave none) and the search phrase. Out: a resolved profile.
Lookup, once per run (`resolve_profile`):
- a stored record under this exact slug → its profile, when it has a book or a parent; a record without either (only capabilities from Jev or the user) is no profile: the lookup goes on and its capabilities lie on top of what is found, by precedence;
- else a profile that lists the slug among its synonyms;
- else an old record keyed by the whole search phrase ("мужские-кроссовки-42"): when it differs from the product type only by audience or size words, it moves under the product type in one transaction with its synonym and the children's `parent` retarget (over a record without books there, deleted in the same transaction, whose capabilities are combined in) (a lookup running at the same time just reads it back) ("knowledge renamed" in the log); a narrower record ("шампунь-от-перхоти") or one with composition ("детский-крем": composition profiles are narrow) is never moved;
- an exact narrower or composition record under the phrase wins even over a profile found by the first two steps ("крем-с-адапаленом" over "крем"); such a profile only lists a plain audience or size variant record as a synonym (books are not merged);
- else, with LLM, Jev picks the covering stored profile (options: records with books only) ("солнцезащитный крем" → "косметика"); one cached LLM call only when Jev fails, an unsure Jev answer means no related profile; Jev and the LLM see the search phrase next to the product type, so "крем" from "крем с адапаленом" still finds an acne profile;
- no substring matching: "чай" does not find "чайник".
The found profile's parent (one step) fills the books it does not set; each book keeps its owner (own slug, parent, or the related profile). Capabilities are inherited by name: for each one the stronger automatic value wins (learned > Jev > fallback; the child on a tie), and a user's choice (`user`) outranks any automatic value.
Books nothing stored provides fall back to the static seed (irrigators), owned by the query's slug.
Old records are read through an adapter: `category_type: composition_driven` becomes the composition capability. Saving merges into the record, and the old keys are still written. Capabilities merge by name in one place (`update_knowledge`): an automatic writer (learning, Jev, a move, healing) changes a capability only when its source is not weaker than the stored one and never touches the user's choice; only the user's own writer changes it, and a reset keeps the learned parameters.
An old record that is a copy left by the pre-profile alias save (no capabilities, no parent, its brand book equal to a related composition profile's) is healed once: it becomes that profile's child and its aliases move to the parent. Every check marks the record, so Jev is asked once.
A product type seen first (nothing stored under it, with LLM) is classified by Jev (`ensure_profile`): one noul question per category capability that has one (composition) and per benchmark provider (Kimovil), state `{product_type}`. The questions ride in the Jev call that picks a related profile; with nothing to pick from, they go in a call of their own. A related profile found drops the answers; otherwise every answer is saved, yes or no (`set_by: jev`, threshold `CLASSIFY_MIN_NOUL`), in a record without books; a failed call saves nothing. The next request asks nothing. Composition from Jev without an ingredient book stays inactive.
Then capabilities are switched on (see Capabilities), and a capability's `clarify` hook may ask (step 2a). This step runs before step 5, so a question never starts a browser.
No LLM: exact slug and synonyms only.
Fails: never blocks. Missing knowledge sets `knowledge_missing` and weakens the brand axis.
Learn first (#26): with `learn_first`, after the `clarify` hook and before step 5, a category that may learn now (the condition in Learning a category) raises `NeedsLearning` (`check_learn_first`); the caller learns it and calls again without the flag. A related or parent profile with books is not new, so nothing is waited for. MCP, the API and the worker never pass the flag.
The run itself writes knowledge in four cases: aliases learned in step 21 (to the owner of the model book), healing, moving an old slug, and the classifier's capabilities. It never learns a whole category; it logs a hint to run `learn-category`.
Entry points write the rest, after the report: the learning marks (`services/auto_learn` `mark_*`), the learned books (`learn_category`) and the user's switches (`knowledge/store.set_user_choice`). When a category is learned on its own: see Learning a category.
Module: `knowledge/store`, `db/repo`, `knowledge/profile`, `knowledge/brands`, `knowledge/models_book`, `knowledge/ingredients`, `capabilities/classify`, `capabilities/flow`, `services/auto_learn`

### 7. Extra searches (capabilities)

Decides: extra Kaspi searches the active capabilities ask for.
Composition: the required active substances come from the search phrase and required features without their "без X" phrases, mapped to canonical names by the ingredient book; each substance gets searches by its name and its equivalent product names (at most 5).
No capability asks → none.
Module: `capabilities/composition`, `services/collect`

### 8. Collect

Decides: the raw pool.
All sources run in parallel; one source failing returns nothing for it and the run continues.

**Kaspi**
- main search by the search phrase; plus a search without Latin jargon if the phrase has it;
- extra searches (step 7) first, then brand searches from the brand book (tier A first);
- targeted hits are kept apart from the main search and are not cut by its card limit;
- specs for every card except apparel variant cards (no spec page); seller offers for 8 cards, 4 targeted + 4 main, a pool short of its half leaves the rest to the other;
- no budget cap at search time (the Kaspi adapter has none).

**Ozon**
- one search with the budget as a server-side price cap;
- no city;
- only cards whose title matches the category; specs for the top 4 of 8.

**OLX**
- search by category, by the jargon-free variant and by the head noun, in the query's city;
- keep only cards that match the category, read as used, and are not "for parts".

Module: `services/collect`, `services/matching`, `ranking/used_defects`

### 9. Normalize

Decides: one `Candidate` per card.
- Price = cheapest seller offer (stock is not checked), else the search-card price.
- Available = has a usable price.
- Brand resolved through the brand book (spec field first, then title) with its tier; `brand_from_title` marks a brand read only from the title.
- Brand mimicry ("J BL") and official seller are flagged here.
- A device after a compatibility word ("для", "на", "к", "for", "совместим с") is what the item fits: it is neither the brand nor a mimic ("Чехол для Apple iPhone 15"). The device is a Latin word, plus the next one after a brand of the book, plus model numbers and line words; a brand after it is still read ("для iPhone Apple AirPods").
No I/O.
Module: `normalize/candidate`

### 10. Official stores

Decides: extra candidates from brand sites named in the brand book. The official_stores capability's `candidates` hook does it (on unless the category switched it off); the step is a no-op when it is off.
Each store page is read as schema.org product data; offers a capability's listing check rejects (another audience, another tyre or mattress size) are dropped before the per-store cut, then the cheapest in-budget items are added.
No brand book or no store URLs → nothing added. A store that blocks us → nothing from it.
Module: `services/official_stores`, `capabilities/official_stores`, `capabilities/flow`

### 11. Price history and review velocity

Decides: 30-day price history and how fast reviews grow, per candidate, from earlier runs.
Fails: store unavailable → no history; the price axis works without it.
Module: `services/collect`, `ranking/velocity`, `db/repo`

### 12. Relevance filter

Decides: which candidates are the product at all (drops a battery in a robot-vacuum search).
It judges the search phrase without excluded-substance phrases ("шампунь", not "шампунь без сульфатов"); the same for OLX ads (step 14).
1. Protected: candidates a capability's `relevance` hook names skip the filter (composition: a title naming a required substance).
2. Heuristic: accessory markers in the title → drop.
3. LLM pass (only with LLM and more left than the floor still needs): a title naming the queried model skips it (`names_query_model`); Jev scores each other title; below 0.25 → drop. If Jev fails, a fallback LLM returns a plain keep list with no threshold; if that fails too, everything stays.
4. Floor: if fewer than 3 remain, protected ones included, the best-rated dropped ones come back.
Every drop and restore is logged with its reason.
Fails: fully fail-open.
Module: `services/recommend`, `llm/relevance`, `llm/jev`, `capabilities/composition`, `capabilities/flow`

### 13. Dedupe and reference prices

Decides: same product → one candidate, the cheaper.
Only across sources: a listing never joins a group that already holds one from its source, so two Kaspi cards or two items of one official store stay apart; an official-store item still merges with its Kaspi or Ozon copy.
Same product = same canonical model in the model book, or the same brand string with heavy title overlap (shared tokens / the smaller token set ≥ 0.6).
A different model number, storage or line word (Pro, Max, Mini, Plus or "+", Ultra, Lite) forbids a merge on either path.
A merge with a 2x price gap marks the survivor, which bars it from roles (step 18).
A much pricier copy elsewhere becomes a warning later (step 21 notes).
Then each candidate gets a reference price: the model's official price, else the brand's cheapest genuine item.
Module: `services/matching`, `normalize/candidate`

### 14. Filter used listings

Decides: which OLX ads stay. Runs at the same time as step 12: the two LLM passes are one wait.
- a capability's listing check rejects it (another audience, another tyre or mattress size) → drop;
- relevance threshold 0.35, stricter than step 12, and no floor: an empty section is fine, a wrong one is not.
- a title naming the queried model skips the LLM pass (as in step 12);
- with at least 5 priced ads, an ad priced under 0.3 of the median is dropped as bait (`_drop_bait_prices`; logged);
- The threshold applies only on the Jev path; the fallback LLM keeps its own list. If both fail, only ads naming the queried model stay. Without LLM only the accessory heuristic runs.
Module: `services/recommend`, `llm/relevance`, `capabilities/flow`

### 15. Benchmark (capability enrich)

Decides: a performance index (feeds the specs axis) and model specs in Kaspi wording ("120 Гц").
The benchmark capability is on only with a provider (`capabilities/benchmark_providers`; today only Kimovil). The profile names it (with the provider as a parameter, or the rule picks one), else the rule alone, over the product type (the search phrase when the parse gave none): смартфон(ы), телефон(ы) or smartphone as a whole word before the first preposition (для, на, к, под, от, с), or iphone/айфон as the first word. "мобильный телефон" → yes; "чехол для телефона", "держатель телефона", "радиотелефон", "телефонный кабель", "кнопочный телефон", "стационарный телефон" → no. RAM and storage are not copied: the listing may be another variant.
The core averages `benchmark_index` into the specs axis without knowing its source.
Cached; fails open.
Module: `capabilities/benchmark`, `capabilities/benchmark_providers`, `services/phone_specs`

### 16. Ingredients (composition capability enrich)

Decides: which active substances and concentration each candidate has.
First the title: a candidate whose title names a required substance or its equivalent gets that substance and counts as checked (`ingredients_checked`).
Then, with LLM, one batch request over the pool reads the title and composition text; it may add substances and the concentration. Only a candidate that got an answer is checked: a failed request, or an index the LLM skipped, leaves it unchecked, never "absent".
Then the substance guard: if a required substance rejects every candidate, the run stops with `NoSubstanceMatch` and the 3 nearest alternatives. A product without the asked substance is never shown as the best pick. Only checked candidates can be rejected, so a failed extraction never stops the run.
No LLM: only the title check; the rest stay unchecked and the report says so (Output).
Module: `capabilities/composition`, `llm/ingredients`, `ranking/filters`

### 17. Rank

Decides: score, confidence, pros and cons for every candidate.
Pure: no DB, no LLM, no network.

**Hard filters** (first failing check names the reason):
over budget → excluded brand → unknown brand when trusted-only → not available → capability listing checks (other audience, another size in the title) → missing required feature (as capabilities adjust the list: composition drops a bare concentration) → capability content checks (required substance proven absent; an excluded substance named in the composition text; the size specs — width, length, profile, diameter, never "Размер упаковки" — say another size).
Required features are checked only when the candidate has specs. Ozon cards past the top 4, official-store candidates and apparel variant cards skip the check (they get an "unverified" con). Benchmark (step 15) fills specs for phones, which turns the check on.
"Proven absent" is decided per candidate: checked, the substance not detected, and a composition text present. Unchecked or no composition text is a data gap, not a rejection.
Ingredient axis: substance present 25 (0.7 of it with an unknown or other concentration); unchecked 0.3 × 25 = 7.5 for every candidate, with the con "состав не проверен: <substance>?"; checked and absent 0.
Nothing passes → `NoNewPick` (a `ValueError`: "no candidates passed hard filters") with the rejection reasons and the OLX ads (step 21). CLI: exit 0 with ads, else 1. Worker: `no_match` with `rejected_reasons` and `used_listings`. The same path runs when `original_only` hid every candidate (step 19).

**Market context**: price reference and review maximums are computed from the passed pool, so price is judged relative to the pool.

**Axes and weights**

| Axis | No capability axis | Composition |
|---|---|---|
| brand | 25 | 10 |
| price | 25 | 25 |
| specs | 20 | 15 |
| reviews | 12 | 12 |
| popularity | 8 | 3 |
| availability | 10 | 10 |
| ingredient | – | 25 |

Brand tier factors: A 1.0, B 0.8, C 0.55, unknown 0.15.
Reviews and popularity are trusted less for unknown brands.

**Adjustments**
- price history: a real dip is rewarded;
- suspicious price (far under the genuine item) → penalty and a con;
- model code in the query ("F300") missing from the title → score cut; with a fit the device replaces the codes and must be named whole ("iPhone 15" is not in "iPhone 15 Pro", is in "iPhone 15/15 Pro"); a device no passed listing names cuts nothing;
- low confidence for a brand read only from the title, a risky price, or a pool with fewer than 3 tiered prices.

Capability axes come before the base axes; weights follow the rule in Capabilities.

**Pros and cons**: official seller, unknown brand, price risk, price history verdict, unverified features, then each capability's notes (size: unverified size; fit: "подходит к <device>"; dims: unverified size; exclude: composition not checked for the substance, or "без X" in the title; composition: ingredient equivalence, and it replaces "weak ingredient"), model mismatch, spec conflicts in the title (a size chain "160x200x22 см" gives the unit to every side), fast delivery.
Module: `ranking/engine`, `ranking/models`, `ranking/filters`, `ranking/price_risk`, `capabilities/composition`, `capabilities/size`, `capabilities/fit`, `capabilities/dims`, `capabilities/exclude`, `capabilities/flow`, `capabilities/weights`, `ranking/model_tokens`, `ranking/spec_conflicts`, `ranking/delivery`, `ranking/flags`

### 18. Roles

Decides: the best pick and the alternatives (best value, budget, premium).
Best = highest score that is not barred: suspicious price, big duplicate price gap, brand mimicry, counterfeit verdict (also "у части продавцов").
All barred → the top score anyway.
Best value counts only candidates close to the best score, so cheap junk cannot win on price.
Budget pick must be cheaper than best and score at least half of it.
Premium comes from tier A/B in the top quarter only.
Alternatives prefer listings with an identified brand; best does not.
Module: `ranking/selection`

### 19. Review pass

Decides: whether reviews change the ranking.
1. Kaspi only, top 6 Kaspi candidates: fetch reviews; discount review axes that look fake; penalize a high share of defect complaints; check counterfeit signals.
2. With LLM and summaries on: summaries for the top 3 of any source, chosen before penalties. A summary needs at least 5 review texts, else `review_summary_note` says why. The LLM writes the text (pros, cons, complaints, verdict, red flags); Jev decides counterfeit, reliability and the required-feature verdicts (Jev fails → none of them this run). Counterfeit or serious reliability concerns cut the score; review verdicts on required features apply.
3. No LLM with required features: stem search over fetched reviews instead.
With LLM and summaries off (`--no-review-summary`) required features are not checked in reviews at all.
After each change the roles are picked again (step 18), so the best pick may change.
With `original_only` the core then hides every candidate barred from roles (`ranking/selection.without_barred`): they leave the ranking, add to `rejected_count` and to the reason "suspected counterfeit" (shown as "подозрение на подделку"), and go to `RecommendResult.hidden`. All of them barred → nothing passed: the run takes the no-new-pick path with the OLX ads (step 21).
The summary shown is the final best pick's.
Module: `services/review_pass`, `services/recommend`, `ranking/selection`, `capabilities/original_only`, `ranking/review_trust`, `ranking/review_requirements`, `ranking/penalties`, `llm/review_summary`

### 20. Persist

Decides: what the next run knows.
Each Kaspi/Ozon product, its offers and one price snapshot are saved; the search phrase is added to the tracked list for `refresh`.
Always runs: no entry point passes `persist=False`; CLI `--no-save` skips only the markdown report.
Runs only after a successful ranking: `NoSubstanceMatch` or "no candidates" skips it.
The snapshot price is min(offers + card price); the candidate price is min(offers).
A store failure is logged and ignored.
Module: `services/collect`, `db/repo`

### 21. Notes and used market

Decides: the final warnings and the OLX section. A used query (`condition` "used") shows the section before the best pick, in every surface (`cli/report.used_first`); otherwise it comes last.
- Cross-source notes from step 13 are attached ("также на ozon за …"); a big gap becomes a con.
- First, with LLM and knowledge, OLX titles the model book missed are mapped to model names: Jev's confident picks only (unsure → no name), the LLM only when Jev fails; a name whose model number, line or storage contradicts the title is dropped. Learned aliases are saved to the profile that owns the model book (its own, the parent or the related profile); only the model book is written, and a borrowed book never creates a record under the query.
- OLX ads are matched to every ranked candidate (same model or brand + title); an ad shows only if its price is under 0.7 of the matched new product's.
- Then notable ads of trusted brands, only when a budget is set.
- Then no-name ads cheaper than a no-name or tier-C best pick.
Module: `services/matching`, `llm/canonicalize`, `knowledge/store`

### Output

`RecommendResult`: the ranking, the constraints used, used listings, and flags the UI shows as warnings:
`restricted_note` (first line of the report), `assumed` (the option searched on the last clarification round), `city_defaulted`, `knowledge_missing`, `skipped_llm_steps`, `unavailable_sources`, `failed_sources` (marketplaces whose search failed: "Ozon: ошибка — его предложений нет в отчёте" in the markdown and console reports, «Не удалось проверить» in the GUI), `review_summary_note` (with `--no-llm` / the app's AI checkbox off the console and the GUI say "AI выключен" instead, `cli/report.summary_note`), `hidden` (the suspected fakes "только оригинал" hid: "Скрыто подозрительных предложений: N" and their titles in the markdown and console reports, a group in the GUI, `RecommendOutcome.hidden` in the API, empty by default), `profile_found` (the related profile the lookup found), `capabilities` (below), `ingredients_unchecked` (set by the composition capability's report section: a ranked candidate whose composition was not checked, "Состав не проверен у N из M — вещество не подтверждено").
Every surface renders the same result. `capabilities` (each registry capability: switch, active, who set it) gives the line "Как оценивали: состав — обучение; бенчмарк Kimovil — Jev." (sources: обучение, Jev, правило, запрос, включено/выключено вами), built once in `capabilities/provenance` and shown by the markdown and console reports, the GUI and the API (`RecommendOutcome.assessed_by`, empty by default so an old stored result still validates); no line when no capability took part and none was switched off by the user. A category capability that is on by default (used_market) is named only once learning or the user decided about it ("рынок б/у — выключено: обучение"). `RecommendOutcome` carries `hidden` and `assessed_by`.
Module: `services/recommend`, `capabilities/composition`, `capabilities/provenance`, `cli/report`, `cli/console`, `jobs`, `gui/screens/report`

## Side flows

### Learning a category

Builds category knowledge: brand book, model book, category type, ingredient book, and the benchmark provider (only one from the registry, an enum in the answer schema). Composition and benchmark are written as learned capabilities, merged by name: a user's choice stays. From notes (`learn --file`) benchmark is written only when the notes name a provider.
- `learn-category` (CLI) or the button on the report screen (GUI); both run `learn_category`.
- One strong-model call, grounded by web search when the `claude` CLI is present.
- Official store prices are checked on real pages and kept as brand price floors.
- Merged into the local store under the category name (other keys of the record stay); step 6 finds it next time.
- `learn --file` merges with the category's own record only, never a parent's or a related one.
- Automatic runs go through `services/auto_learn.auto_learn` (mark started, `learn_category`, mark done; a failure is marked, logged, returned as `False`). `should_auto_learn` decides: no profile with books, intent `product`/`need`, parsed with the LLM, the `claude` CLI present, a product type of at most 60 letters/digits/spaces/hyphens, no `learning_started_at` mark younger than 15 minutes, no `learn_failed_at` younger than 24 hours, and fewer than `auto_learn_per_hour` (default 10; `BUYSMARTER_AUTO_LEARN_PER_HOUR=0` turns it off) starts in the last hour in this process. The search never waits for it.
- Who starts it: MCP after a `parse_with_llm=True` recommend (a background task; the report says to repeat in a few minutes); CLI before the search (`recommend(learn_first=True)` raises `NeedsLearning`) asks on a terminal "Категория «…» новая. Изучить её перед поиском?" (default yes; no searches now while the category learns in the background, then waits for it and asks "Повторить поиск?"; `--learn` learns without asking, off a terminal without it nothing waits), then searches again without the flag (`cli/learning`); API/worker only with `BUYSMARTER_AUTO_LEARN=true` and `use_llm=true`, after the job finishes, as the taskiq task `buysmarter.learn_category` (600 s limit, one Redis `SET NX` slot per category); the desktop app before the search (`gui/screens/search` → `gui/learning.learn_now`).
- Variant: extract the same knowledge from the user's own notes; notes with more than 10 list items go in chunks of 10, each headed by the notes' intro, merged in code.
Feeds: step 6, and through it steps 7, 8, 10, 13, 16, 17.
Module: `knowledge/generate`, `knowledge/store`, `services/official_stores`, `services/auto_learn`, `gui/learning`

### Price tracking

`refresh` re-collects every tracked category and reports the biggest price drops.
It runs all sources without category knowledge, ranking or LLM.
`history` shows one product's stored snapshots.
Feeds: step 11.
Module: `services/price_tracking`

### Doctor

Checks the environment: LLM route, browser for Ozon, store, sources.
Module: `services/doctor`

## Known gaps

Short list; details and fixes are in the plan (number in brackets).
- City: an unknown city silently searches Almaty; Ozon gets no city; MCP ignores the city in the query (#3).
- Dedupe still merges FE/SE variants, chip generations (M2/M3) and RAM-only variants; overlap is asymmetric (#13).
- Capabilities are switched on by stored profiles, the Jev classifier on the first sight of a product type (`ensure_profile`) and fallback rules. `recommend()` never learns a category itself; entry points may start learning after the report (Learning a category). Findings #1 and #2 are fixed.
- MCP and the HTTP API run without LLM by default (#4).
- Without an LLM there is no intent detection: only an unreadable query and capabilities ask, so MCP and the API (no LLM by default) search "подарок для мамы" as it is (phase 2).
- Kaspi search has no budget cap (#5).
- `refresh` collects without category knowledge (#6).
- After review penalties a new best pick may have no summary (#14).
- A query's own record of a general type ("крем") hides the closer related profile's books ("средства-от-акне") (#25).
