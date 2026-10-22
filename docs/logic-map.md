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
  ├─ 2  parse ................ text → constraints (category, budget, city, features)
  ├─ 3  city ................. override > query > stored preference > Алматы
  ├─ 4  clean constraints .... audience, size, drop features the category already implies
  ├─ 5  sources .............. Kaspi / Ozon (needs a browser) / OLX
  ├─ 6  category knowledge ... brand book, model book, category type
  ├─ 7  substance plan ....... composition-driven only
  ├─ 8  collect .............. three marketplaces in parallel
  ├─ 9  normalize ............ raw card → Candidate
  ├─ 10 official stores ...... brand sites as extra candidates
  ├─ 11 history .............. price history, review velocity
  ├─ 12 relevance filter ..... drop accessories and off-category items
  ├─ 13 dedupe ............... same product → keep the cheaper
  ├─ 14 used filter .......... OLX ads: audience + strict relevance
  ├─ 15 Kimovil .............. smartphones only
  ├─ 16 ingredients .......... composition-driven only; may stop the run
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

## Where the flow splits by category

Four different signals decide category-specific behaviour.
They are independent of each other.

| Signal | Decided by | Switches on |
|---|---|---|
| Category type | stored category knowledge (step 6) | substance searches, ingredient extraction, ingredient axis, composition weights |
| Smartphone | first word of the category contains a phone stem as a substring ("радиотелефон" too) | Kimovil specs and index (step 15) |
| Audience | regex over the query text, any category ("детская зубная щётка" too) | gender hard filter on title + category path (steps 4, 10, 14, 17) |
| Apparel | regex over the query text | size phrases leave the required features (step 4); the size itself is read for any category and reported as unverified (step 17) |

Examples:
- "крем с адапаленом" → composition-driven (if knowledge says so): searches by substance, rejects products whose composition proves the substance absent.
- "мужские кроссовки 42 размер" → spec-driven; gender becomes a hard filter, size becomes an unverified con.
- "смартфон iPhone 13" → spec-driven + Kimovil.
- A category with no knowledge at all → spec-driven, every brand scores as unknown.

## Entry points

All entry points call the same `recommend()`.
They differ in defaults.
`use_llm=False` switches off every LLM step of the run, not only parsing.

| Entry | Sources by default | LLM by default | Notes |
|---|---|---|---|
| CLI `recommend` | kaspi, ozon, olx | on | may ask for a budget before the run |
| Desktop GUI | user picks | on | calls `recommend()` in-process; merges excluded brands from settings into the parse |
| MCP `recommend` | kaspi, ozon, olx | **off** (`parse_with_llm=False`) | passes `city or preference or Алматы` as an override: a city in the query is ignored, `city_defaulted` is always false |
| HTTP API + worker | `api_sources`, default kaspi + olx (no Ozon) | **off** (`use_llm=False`) | job queue in Redis, timeout per job, dedupe by request |
| direct call | kaspi only | on | used by tests |

Module: `cli/main`, `gui/screens/search`, `mcp_server/server`, `api/recommend`, `worker`, `jobs`

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

Decides: the category, budget, city, required features, excluded brands.
In: query text. Out: `QueryConstraints`.
Branches:
- LLM: one call extracts all fields. Category is forced into Russian; a named model stays in the category ("смартфон iPhone 13"); condition words (б/у) go to usage notes.
- No LLM or LLM error: regex takes budget and city; the rest of the text becomes the category; no required features on purpose.
The category string is the key for everything below: knowledge lookup, searches, relevance.
Module: `llm/prompts`, `services/recommend`

### 3. Resolve the city

Decides: which city the search and delivery dates are for.
Order: explicit override → city in the query → stored preference → "Алматы".
MCP always passes an override (see Entry points), so this order never reaches the query there.
The city reaches Kaspi only (as a city id). Ozon and OLX get no city; OLX searches Almaty by default.
`city_defaulted` is set when neither the query nor the override named one.
Fails: a dead store means no preference, never an error.
Module: `services/recommend`, `db/repo`

### 4. Clean the constraints

Decides: final constraints before search.
- Audience ("мужские", "детские") is read from the query text by regex if the parser missed it, for any category.
- Size ("42 размер", "XL") is read out and removed from the category.
- Size phrases are removed from required features for apparel.
- Features already implied by the category are dropped ("TWS" in "TWS наушники").
- Condition words (б/у, новый) are dropped: used goods are OLX's job.
Then CLI/API overrides apply: budget, trusted brands only.
Module: `services/recommend`, `ranking/audience`

### 5. Pick the sources

Decides: which marketplaces this run queries.
Branches:
- Ozon needs a real browser; none found → Ozon is left out and listed in `unavailable_sources`.
- Kaspi may go through a proxy (`kaspi_proxy`).
Module: `services/collect`, `services/ozon_browser`

### 6. Resolve category knowledge

Decides: brand book (brands and trust tiers), model book (models, reference prices), category type, ingredient book.
In: constraints.category. Out: books, or None.
Branches:
- knowledge stored for this exact category → use it;
- not stored → a related stored category: plain substring match first (no word boundary: "чай" matches "чайник"), then Jev or one cached LLM call ("солнцезащитный крем" → "косметика");
- still nothing → static seed books if any; category type = spec-driven;
- ingredient book is kept only when the type is composition-driven.
Books and category type are resolved by two separate lookups with different conditions: books need a non-empty brand or model book, type takes any stored record.
No LLM: substring match only.
Fails: never blocks. Missing knowledge sets `knowledge_missing` and weakens the brand axis.
The run does not learn a whole category; it logs a hint to run `learn-category`.
It does write knowledge in one case: OLX model aliases learned in step 21 are saved under the query's category (see step 21).
Module: `knowledge/store`, `knowledge/brands`, `knowledge/models_book`, `knowledge/ingredients`

### 7. Substance plan (composition-driven only)

Decides: required active substances and extra searches for them.
The substances come from the category and required features, mapped to canonical names by the ingredient book.
Each substance gets searches by its name and its equivalent product names.
Spec-driven or no substance named → skip.
Module: `services/recommend`, `services/collect`

### 8. Collect

Decides: the raw pool.
All sources run in parallel; one source failing returns nothing for it and the run continues.

**Kaspi**
- main search by category; plus a search without Latin jargon if the category has it;
- substance searches (step 7) first, then brand searches from the brand book (tier A first);
- targeted hits are kept apart from the main search and are not cut by its card limit;
- specs for every card except apparel variant cards (no spec page); seller offers for 4 targeted + 4 main hits, an unused half is not passed to the other;
- no budget cap at search time (the Kaspi adapter has none).

**Ozon**
- one search with the budget as a server-side price cap;
- no city;
- only cards whose title matches the category; specs for the top 4 of 8.

**OLX**
- search by category, by the jargon-free variant and by the head noun; no city, so Almaty;
- keep only cards that match the category, read as used, and are not "for parts".

Module: `services/collect`, `services/matching`, `ranking/used_defects`

### 9. Normalize

Decides: one `Candidate` per card.
- Price = cheapest seller offer (stock is not checked), else the search-card price.
- Available = has a usable price.
- Brand resolved through the brand book (spec field first, then title) with its tier; `brand_from_title` marks a brand read only from the title.
- Brand mimicry ("J BL") and official seller are flagged here.
No I/O.
Module: `normalize/candidate`

### 10. Official stores

Decides: extra candidates from brand sites named in the brand book.
Each store page is read as schema.org product data; the cheapest in-budget items for the query's audience are added.
No brand book or no store URLs → nothing added. A store that blocks us → nothing from it.
Module: `services/official_stores`

### 11. Price history and review velocity

Decides: 30-day price history and how fast reviews grow, per candidate, from earlier runs.
Fails: store unavailable → no history; the price axis works without it.
Module: `services/collect`, `ranking/velocity`, `db/repo`

### 12. Relevance filter

Decides: which candidates are the product at all (drops a battery in a robot-vacuum search).
1. Protected: composition-driven candidates whose title names a required substance skip the filter.
2. Heuristic: accessory markers in the title → drop.
3. LLM pass (only with LLM and more than 3 left): Jev scores each title; below 0.25 → drop. If Jev fails, a fallback LLM returns a plain keep list with no threshold; if that fails too, everything stays.
4. Floor: if fewer than 3 remain, the best-rated dropped ones come back. Protected candidates are not counted toward the floor.
Every drop and restore is logged with its reason.
Fails: fully fail-open.
Module: `services/recommend`, `llm/relevance`, `llm/jev`

### 13. Dedupe and reference prices

Decides: same product → one candidate, the cheaper.
Source is not compared: duplicates inside Kaspi and official-store candidates merge too.
Same product = same canonical model in the model book, or the same brand string with heavy title overlap.
Overlap = shared tokens / the smaller token set, so "Note 13" and "Note 13 Pro" merge; the model book does not separate memory variants.
A merge with a 2x price gap marks the survivor, which bars it from roles (step 18).
A much pricier copy elsewhere becomes a warning later (step 21 notes).
Then each candidate gets a reference price: the model's official price, else the brand's cheapest genuine item.
Module: `services/matching`, `normalize/candidate`

### 14. Filter used listings

Decides: which OLX ads stay.
- other audience → drop;
- relevance threshold 0.55, stricter than step 12, and no floor: an empty section is fine, a wrong one is not.
- The threshold applies only on the Jev path; the fallback LLM and a total LLM failure keep what they keep (all, on failure). Without LLM only the accessory heuristic runs.
Module: `services/recommend`, `llm/relevance`

### 15. Kimovil (smartphones only)

Decides: Kimovil index (feeds the specs axis) and model specs in Kaspi wording ("120 Гц").
Trigger: the first word of the category contains a phone stem (смартфон, телефон, айфон, iphone, smartphone).
RAM and storage are not copied: the listing may be another variant.
Cached; fails open.
Module: `services/phone_specs`

### 16. Ingredients (composition-driven only)

Decides: which active substances and concentration each candidate has.
First the title: a candidate whose title names a required substance or its equivalent gets that substance and counts as checked (`ingredients_checked`).
Then, with LLM, one batch request over the pool reads the title and composition text; it may add substances and the concentration. Only a candidate that got an answer is checked: a failed request, or an index the LLM skipped, leaves it unchecked, never "absent".
Then the substance guard: if a required substance rejects every candidate, the run stops with `NoSubstanceMatch` and the 3 nearest alternatives. A product without the asked substance is never shown as the best pick. Only checked candidates can be rejected, so a failed extraction never stops the run.
No LLM: only the title check; the rest stay unchecked and the report says so (Output).
Module: `services/recommend`, `llm/ingredients`, `ranking/filters`

### 17. Rank

Decides: score, confidence, pros and cons for every candidate.
Pure: no DB, no LLM, no network.

**Hard filters** (first failing check names the reason):
over budget → excluded brand → unknown brand when trusted-only → not available → other audience → missing required feature → required substance proven absent.
Required features are checked only when the candidate has specs. Ozon cards past the top 4, official-store candidates and apparel variant cards skip the check (they get an "unverified" con). Kimovil (step 15) fills specs for phones, which turns the check on.
"Proven absent" is decided per candidate: checked, the substance not detected, and a composition text present. Unchecked or no composition text is a data gap, not a rejection.
Ingredient axis: substance present 25 (0.7 of it with an unknown or other concentration); unchecked 0.3 × 25 = 7.5 for every candidate, with the con "состав не проверен: <substance>?"; checked and absent 0.
Nothing passes → error "no candidates passed hard filters".

**Market context**: price reference and review maximums are computed from the passed pool, so price is judged relative to the pool.

**Axes and weights**

| Axis | Spec-driven | Composition-driven |
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
- model code in the query ("F300") missing from the title → score cut;
- low confidence for a brand read only from the title, a risky price, or a pool with fewer than 3 tiered prices.

**Pros and cons**: official seller, unknown brand, price risk, price history verdict, unverified features, unverified size, ingredient equivalence, model mismatch, spec conflicts in the title, fast delivery.
Module: `ranking/engine`, `ranking/filters`, `ranking/price_risk`, `ranking/ingredient_axis`, `ranking/model_tokens`, `ranking/spec_conflicts`, `ranking/delivery`, `ranking/flags`

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
2. With LLM and summaries on: summaries for the top 3 of any source, chosen before penalties; counterfeit or serious reliability concerns cut the score; review verdicts on required features apply.
3. No LLM with required features: stem search over fetched reviews instead.
With LLM and summaries off (`--no-review-summary`) required features are not checked in reviews at all.
After each change the roles are picked again (step 18), so the best pick may change.
The summary shown is the final best pick's.
Module: `services/review_pass`, `ranking/review_trust`, `ranking/review_requirements`, `ranking/penalties`, `llm/review_summary`

### 20. Persist

Decides: what the next run knows.
Each Kaspi/Ozon product, its offers and one price snapshot are saved; the category is added to the tracked list for `refresh`.
Always runs: no entry point passes `persist=False`; CLI `--no-save` skips only the markdown report.
Runs only after a successful ranking: `NoSubstanceMatch` or "no candidates" skips it.
The snapshot price is min(offers + card price); the candidate price is min(offers).
A store failure is logged and ignored.
Module: `services/collect`, `db/repo`

### 21. Notes and used market

Decides: the final warnings and the OLX section.
- Cross-source notes from step 13 are attached ("также на ozon за …"); a big gap becomes a con.
- First, with LLM and knowledge, OLX titles the model book missed are mapped to model names. Learned aliases are saved under the query's category with the borrowed brand book; a new record gets type spec-driven and no ingredient book.
- OLX ads are matched to every ranked candidate (same model or brand + title).
- Then notable ads of trusted brands, only when a budget is set.
- Then no-name ads cheaper than a no-name or tier-C best pick.
Module: `services/matching`, `llm/canonicalize`

### Output

`RecommendResult`: the ranking, the constraints used, used listings, and flags the UI shows as warnings:
`city_defaulted`, `knowledge_missing`, `skipped_llm_steps`, `unavailable_sources`, `review_summary_note`, `ingredients_unchecked` (composition-driven category with an unchecked ranked candidate: "Состав не проверен у N из M — вещество не подтверждено").
Every surface renders the same result.
Module: `services/recommend`, `cli/report`

## Side flows

### Learning a category

Builds category knowledge: brand book, model book, category type, ingredient book.
- `learn-category` (CLI) or the button on the report screen (GUI).
- One strong-model call, grounded by web search when the `claude` CLI is present.
- Official store prices are checked on real pages and kept as brand price floors.
- Saved to the local store under the category name; step 6 finds it next time.
- Variant: extract the same knowledge from the user's own notes.
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
- Saving OLX aliases turns a borrowed composition-driven category into a spec-driven record (#10).
- City: Kaspi knows Almaty and Astana only; Ozon and OLX get no city; MCP ignores the city in the query (#3).
- Dedupe merges different models and variants, and listings inside one source (#13).
- A query for an unknown category does not learn it (#1).
- Category-specific behaviour hangs on four independent signals (#2).
- MCP and the HTTP API run without LLM by default (#4).
- Kaspi search has no budget cap (#5).
- `refresh` collects without category knowledge (#6).
- Books and category type look up a related category separately (#7).
- Substring category match has no word boundary (#15).
- After review penalties a new best pick may have no summary (#14).
- The used-ads filter keeps everything when the LLM fails (#16).
