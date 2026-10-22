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
  ├─ 4  clean constraints .... capabilities read the query (audience, size); implied features out
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
optional hooks the core calls at fixed points, in registry order (audience, size, fit, benchmark, composition).
A hook a capability does not implement is not called.
`ranking/` never imports capabilities: the flow hands `rank()` plain weights, predicates and functions (`RankHooks`).

| Capability | Level | Switched on by | Hooks it uses (steps) |
|---|---|---|---|
| audience | request | `constraints.gender`: the parser, or a regex over the query text (step 4) | `query` (4), `reject_listing` on the main pool, OLX ads and official-store offers (10, 14, 17) |
| size | request | `constraints.size`: the parser, or a regex over the query text for apparel (step 4) | `query` (4: the size leaves the search phrase and apparel features), `notes` (17: "size unverified") |
| fit | request | `constraints.fit_target`: the parser, or "для/на/под/к <device>" in the query text (step 4) | `query` (4: the device joins the search phrase and leaves the product type), `notes` (17: "подходит к <device>"); the model penalty (17) looks for the whole device model |
| benchmark | category | the profile, only with a provider from `BENCHMARK_PROVIDERS` (its parameter, else the provider whose rule takes the product type); without a profile setting, the providers' rule (`set_by: fallback`) | `enrich` (15: Kimovil specs and `benchmark_index`) |
| composition | category | the profile: the composition capability with an ingredient book (old records: `category_type: composition_driven`) | `searches` (7), `relevance` (12), `hard_features`, `reject_content` (17), `enrich` (16, may stop the run), `prepare` + `axis` + `notes` (17), `section` (Output) |

Weights: base weights plus each capability's axis and shifts, clamped at zero, normalized to 100 (`capabilities/weights`).
Composition gives the old composition table exactly (ingredient +25, brand −15, specs −5, popularity −5).

Examples:
- "крем с адапаленом" → composition (if the profile has it): searches by substance, rejects products whose composition proves the substance absent.
- "мужские кроссовки 42 размер" → audience and size; gender is a listing check everywhere, size an unverified con.
- "смартфон iPhone 13" → benchmark by the providers' rule (Kimovil); "чехол на iPhone 15", "держатель телефона" → none.
- A category with no profile at all → no category capability, every brand scores as unknown.

Module: `capabilities/base`, `capabilities/flow`, `capabilities/weights`, `capabilities/audience`, `capabilities/size`, `capabilities/fit`, `capabilities/benchmark`, `capabilities/composition`

## Entry points

All entry points call the same `recommend()`.
They differ in defaults.
`use_llm=False` switches off every LLM step of the run, not only parsing.

| Entry | Sources by default | LLM by default | Notes |
|---|---|---|---|
| CLI `recommend` | kaspi, ozon, olx | on | on a terminal asks a clarification first (number or own text, next round), then a missing budget; off a terminal a question exits with code 3, nothing to search with 1; `--no-clarify` searches the first option |
| Desktop GUI | user picks | on | calls `recommend()` in-process; merges excluded brands from settings into the parse; a clarification shows as a card under the form (option buttons, own answer: an option goes back as `answer`, own text joins the query; next round); `restricted_note` heads the report; "Изучить категорию" learns the product type, and the search it reruns keeps the answer and round; after a report with no brand data, the GUI starts learning the category on its own (kept per category in `gui/learning`, the search never waits; closing the window asks while one runs; when the on-screen category is learned the button becomes "Повторить поиск"); every report carries the group "Как оцениваем эту категорию" (`gui/capability_toggles`): a checkbox per category capability with its source in the text and a reset to automatic, written to the request's own profile on the core loop (the related profile found becomes its `parent`, never written to); a change only offers "Повторить поиск" |
| MCP `recommend` | kaspi, ozon, olx | **off** (`parse_with_llm=False`) | passes `city or preference or Алматы` as an override: a city in the query is ignored, `city_defaulted` is always false; a clarification comes back as markdown telling the host to ask the human and call again with `answer` and `clarify_round` + 1 |
| HTTP API + worker | `api_sources`, default kaspi + olx (no Ozon) | **off** (`use_llm=False`) | job queue in Redis, timeout per job, dedupe by request; a clarification is `no_match` with `reason="needs_clarification"` and `clarification`, the client posts again with `answer` and `clarify_round` + 1; `constraints` is the public `ParsedRequest` (`category` kept, deprecated) |
| direct call | kaspi only | on | used by tests |

Module: `cli/main`, `cli/clarify`, `gui/screens/search`, `gui/screens/clarify`, `gui/capability_toggles`, `mcp_server/server`, `api/recommend`, `worker`, `jobs`

## Steps

### 1. LLM route

Decides: whether this run can use an LLM at all.
Branches:
- a route exists (API key in env or keyring, or the `claude` CLI) → LLM steps on;
- no route → run goes no-LLM and reports the skipped steps (query parsing, review summaries, ingredient extraction);
- user opted out (`--no-llm`, MCP/API defaults) → no-LLM, nothing reported as skipped.
Without LLM these also stop: the LLM relevance pass, related-category matching by LLM, OLX title canonicalization. The skipped-steps list names only three of them.
Module: `llm/adapter`, `llm/keys`

### 2. Parse the request

Decides: the request intent, the search phrase, the product type, budget, city, required features, excluded brands, condition.
In: query text. Out: `QueryConstraints`.
Branches:
- LLM: one call extracts all fields: intent (product, need, open, multi, service, unclear), `search_phrase` (what the marketplaces get), `product_type` (the profile key without audience, size or theme), `options` to choose from, `restricted`, `condition`, budget range and note, `fit_target` (the device the goods are for). The phrase is forced into Russian; a named model stays in it ("смартфон iPhone 13"), unless the goods are for it ("чехол на iPhone 15": phrase and type "чехол", fit target "iPhone 15"). An empty product type falls back to the naive one; no budget and no budget note from the LLM means the budget rules below decide ("до 50 штук" the LLM missed is still 50 000).
- No LLM or LLM error: intent is product (unclear when the phrase has fewer than two letters, an empty phrase included: «хочу до 20к» is never searched as «хочу»); regex takes budget, city and condition (б/у, новый); the rest of the text becomes the search phrase, and the product type is that phrase without audience and size words; no required features on purpose.
Budget rules (no LLM): another currency ($, ₽, €) gives no budget, only a note; tenge (and Kazakh "теңгеге"), thousands slang ("тыс", "косарь", "мың", "мыңға", "штук" after "до/за"), "млн" (×1 000 000) and "N k" from 10 up are money; Kazakh "дейін" after the amount means "до"; a bare "до N" is money only without a unit after it (м, кг, лет, МЕ, Вт, шт...) and under 1000 means thousands; a fraction is never money ("до 18.5 кг"), "15.000" is 15 000; "30-40 тыс" and "от 30 до 40 тыс" are ranges, a bare "N до M" is not ("iPhone 15 до 500 тыс": 15 stays in the phrase). The money words leave the search phrase.
The search phrase drives searches, relevance and the substances read from the query; the product type keys the category profile (step 6). The old `category` field is gone from the parse; the HTTP API still shows it as a deprecated copy of the search phrase.
Module: `llm/prompts`, `llm/budget`, `services/recommend`

### 2a. Clarify before searching

Decides: whether the request is searched, or answered with a question (`NeedsClarification`) or a final "nothing to search" (`NothingToSearch`).
In: the parse, the caller's `answer`, `clarify_round` (0, 1, 2) and `no_clarify`.
- An `answer` equal to one of the parse's options narrows the request to it: intent product, search phrase and product type from the answer; budget, city and audience stay; no second parse. Any other answer is added to the query, which is parsed again.
- Restricted goods are flagged here (see step 4), so a question carries the warning too.
- Asks on (blank options dropped first): open with options, need with two or more options, multi with two or more products, service, unclear. Open or need without options and without a phrase count as unclear. A need with a phrase and no options ("шампунь от перхоти") is searched.
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
  - fit: "для/на/под/к <device>" read from the query when the parser missed it (the device is Latin with a digit or a known device word such as айфон, Xiaomi, PS5; never a number, a unit or "64GB"); the device joins the search phrase and leaves the product type ("чехол на iPhone 15" → search "чехол iPhone 15", type "чехол").
- Features already implied by the search phrase are dropped ("TWS" in "TWS наушники").
- Condition words (б/у, новый) are dropped: used goods are OLX's job. They set `condition`; "used" adds a report note pointing to the OLX section.
Then CLI/API overrides apply: budget, trusted brands only.
Module: `services/recommend`, `services/understanding`, `capabilities/flow`, `capabilities/audience`, `capabilities/size`, `ranking/audience`

### 5. Pick the sources

Decides: which marketplaces this run queries.
Branches:
- Ozon needs a real browser; none found → Ozon is left out and listed in `unavailable_sources`.
- Kaspi may go through a proxy (`kaspi_proxy`).
Module: `services/collect`, `services/ozon_browser`

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
The run does not learn a whole category; it logs a hint to run `learn-category`.
Whether a request may learn its category on its own is decided by `should_auto_learn` (`services/auto_learn`): no profile with books, intent `product`/`need`, parsed with the LLM, the `claude` CLI present, a product type of at most 60 letters/digits/spaces/hyphens, no `learning_started_at` mark younger than 15 minutes, no `learn_failed_at` younger than 24 hours, and fewer than `auto_learn_per_hour` (10) starts in the last hour in this process.

The search never waits for the learning; `auto_learn` (mark started, `learn_category`, mark done; a failure is marked, logged and returned as `False`) is the one runner. MCP: after a `parse_with_llm=True` recommend that meets the condition, a background task, the report says to repeat the request in a few minutes. CLI: after the report on a terminal it asks "Изучить категорию сейчас?" (`--learn` learns without asking, off a terminal it never asks) and then offers to repeat the search. API/worker: off unless `BUYSMARTER_AUTO_LEARN=true`; then, after the job is finished, a separate taskiq task `buysmarter.learn_category` (600 s limit), deduplicated by a Redis `SET NX` slot per category. The desktop app is separate.
It writes knowledge in four cases: aliases learned in step 21 (to the owner of the model book), healing, moving an old slug, and the classifier's capabilities.
Module: `knowledge/store`, `db/repo`, `knowledge/profile`, `knowledge/brands`, `knowledge/models_book`, `knowledge/ingredients`, `capabilities/classify`, `capabilities/flow`, `services/auto_learn`, `mcp_server/server`, `cli/main`, `worker`

### 7. Extra searches (capabilities)

Decides: extra Kaspi searches the active capabilities ask for.
Composition: the required active substances come from the search phrase and required features, mapped to canonical names by the ingredient book; each substance gets searches by its name and its equivalent product names (at most 5).
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

Decides: extra candidates from brand sites named in the brand book.
Each store page is read as schema.org product data; offers a capability's listing check rejects (another audience) are dropped before the per-store cut, then the cheapest in-budget items are added.
No brand book or no store URLs → nothing added. A store that blocks us → nothing from it.
Module: `services/official_stores`

### 11. Price history and review velocity

Decides: 30-day price history and how fast reviews grow, per candidate, from earlier runs.
Fails: store unavailable → no history; the price axis works without it.
Module: `services/collect`, `ranking/velocity`, `db/repo`

### 12. Relevance filter

Decides: which candidates are the product at all (drops a battery in a robot-vacuum search).
1. Protected: candidates a capability's `relevance` hook names skip the filter (composition: a title naming a required substance).
2. Heuristic: accessory markers in the title → drop.
3. LLM pass (only with LLM and more left than the floor still needs): Jev scores each title; below 0.25 → drop. If Jev fails, a fallback LLM returns a plain keep list with no threshold; if that fails too, everything stays.
4. Floor: if fewer than 3 remain, protected ones included, the best-rated dropped ones come back.
Every drop and restore is logged with its reason.
Fails: fully fail-open.
Module: `services/recommend`, `llm/relevance`, `llm/jev`

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
- a capability's listing check rejects it (another audience) → drop;
- relevance threshold 0.35, stricter than step 12, and no floor: an empty section is fine, a wrong one is not.
- The threshold applies only on the Jev path; the fallback LLM keeps its own list. If both fail, only ads naming the queried model stay. Without LLM only the accessory heuristic runs.
Module: `services/recommend`, `llm/relevance`

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
over budget → excluded brand → unknown brand when trusted-only → not available → capability listing checks (other audience) → missing required feature (as capabilities adjust the list: composition drops a bare concentration) → capability content checks (required substance proven absent).
Required features are checked only when the candidate has specs. Ozon cards past the top 4, official-store candidates and apparel variant cards skip the check (they get an "unverified" con). Benchmark (step 15) fills specs for phones, which turns the check on.
"Proven absent" is decided per candidate: checked, the substance not detected, and a composition text present. Unchecked or no composition text is a data gap, not a rejection.
Ingredient axis: substance present 25 (0.7 of it with an unknown or other concentration); unchecked 0.3 × 25 = 7.5 for every candidate, with the con "состав не проверен: <substance>?"; checked and absent 0.
Nothing passes → error "no candidates passed hard filters".

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

**Pros and cons**: official seller, unknown brand, price risk, price history verdict, unverified features, then each capability's notes (size: unverified size; fit: "подходит к <device>"; composition: ingredient equivalence, and it replaces "weak ingredient"), model mismatch, spec conflicts in the title, fast delivery.
Module: `ranking/engine`, `ranking/filters`, `ranking/price_risk`, `capabilities/composition`, `capabilities/size`, `ranking/model_tokens`, `ranking/spec_conflicts`, `ranking/delivery`, `ranking/flags`

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
2. With LLM and summaries on: summaries for the top 3 of any source, chosen before penalties. The LLM writes the text (pros, cons, complaints, verdict, red flags); Jev decides counterfeit, reliability and the required-feature verdicts (Jev fails → none of them this run). Counterfeit or serious reliability concerns cut the score; review verdicts on required features apply.
3. No LLM with required features: stem search over fetched reviews instead.
With LLM and summaries off (`--no-review-summary`) required features are not checked in reviews at all.
After each change the roles are picked again (step 18), so the best pick may change.
The summary shown is the final best pick's.
Module: `services/review_pass`, `ranking/review_trust`, `ranking/review_requirements`, `ranking/penalties`, `llm/review_summary`

### 20. Persist

Decides: what the next run knows.
Each Kaspi/Ozon product, its offers and one price snapshot are saved; the search phrase is added to the tracked list for `refresh`.
Always runs: no entry point passes `persist=False`; CLI `--no-save` skips only the markdown report.
Runs only after a successful ranking: `NoSubstanceMatch` or "no candidates" skips it.
The snapshot price is min(offers + card price); the candidate price is min(offers).
A store failure is logged and ignored.
Module: `services/collect`, `db/repo`

### 21. Notes and used market

Decides: the final warnings and the OLX section.
- Cross-source notes from step 13 are attached ("также на ozon за …"); a big gap becomes a con.
- First, with LLM and knowledge, OLX titles the model book missed are mapped to model names: Jev's confident picks only (unsure → no name), the LLM only when Jev fails; a name whose model number, line or storage contradicts the title is dropped. Learned aliases are saved to the profile that owns the model book (its own, the parent or the related profile); only the model book is written, and a borrowed book never creates a record under the query.
- OLX ads are matched to every ranked candidate (same model or brand + title).
- Then notable ads of trusted brands, only when a budget is set.
- Then no-name ads cheaper than a no-name or tier-C best pick.
Module: `services/matching`, `llm/canonicalize`

### Output

`RecommendResult`: the ranking, the constraints used, used listings, and flags the UI shows as warnings:
`restricted_note` (first line of the report), `assumed` (the option searched on the last clarification round), `city_defaulted`, `knowledge_missing`, `skipped_llm_steps`, `unavailable_sources`, `review_summary_note`, `ingredients_unchecked` (set by the composition capability's report section: a ranked candidate whose composition was not checked, "Состав не проверен у N из M — вещество не подтверждено").
Every surface renders the same result. `capabilities` (each registry capability: switch, active, who set it) gives the line "Как оценивали: состав — обучение; бенчмарк Kimovil — Jev." (sources: обучение, Jev, правило, запрос, включено/выключено вами), built once in `capabilities/provenance` and shown by the markdown and console reports, the GUI and the API (`RecommendOutcome.assessed_by`, empty by default so an old stored result still validates); no active capability, no line.
Module: `services/recommend`, `capabilities/composition`, `capabilities/provenance`, `cli/report`

## Side flows

### Learning a category

Builds category knowledge: brand book, model book, category type, ingredient book, and the benchmark provider (only one from the registry, an enum in the answer schema). Composition and benchmark are written as learned capabilities, merged by name: a user's choice stays. From notes (`learn --file`) benchmark is written only when the notes name a provider.
- `learn-category` (CLI) or the button on the report screen (GUI); both run `learn_category`.
- One strong-model call, grounded by web search when the `claude` CLI is present.
- Official store prices are checked on real pages and kept as brand price floors.
- Merged into the local store under the category name (other keys of the record stay); step 6 finds it next time.
- `learn --file` merges with the category's own record only, never a parent's or a related one.
- Variant: extract the same knowledge from the user's own notes; notes with more than 10 list items go in chunks of 10, each headed by the notes' intro, merged in code.
Feeds: step 6, and through it steps 7, 8, 10, 13, 16, 17.
Module: `knowledge/generate`, `knowledge/store`, `services/official_stores`

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
- A query for an unknown category does not learn it (#1).
- Capabilities are switched on by stored profiles and fallback rules; no Jev classifier or learning on first query yet (#2, phase 3).
- MCP and the HTTP API run without LLM by default (#4).
- Without an LLM there is no intent detection: only an unreadable query and capabilities ask, so MCP and the API (no LLM by default) search "подарок для мамы" as it is (phase 2).
- Kaspi search has no budget cap (#5).
- `refresh` collects without category knowledge (#6).
- After review penalties a new best pick may have no summary (#14).
