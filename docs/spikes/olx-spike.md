# OLX.kz public JSON API — live spike test

Date: 2026-07-18. Machine IP: home KZ residential. Reference (ideas only, GPL-3, NOT
copied): `external_examples/olx-parser` (targets olx.ua). Query used: `ирригатор`.

**Result: OLX.kz exposes the same public REST API its own frontend uses — no HTML
scraping and no Playwright needed.** All probes returned `200` from a plain browser
UA with no cookies, no referer, no anti-bot gate encountered. 11 requests total.

## Working endpoints

| Endpoint | Method | Status | Notes |
|---|---|---|---|
| `/api/v1/offers/` (search) | GET | 200 | Query + filters + pagination. Returns `data[]`, `metadata`, `links`. |
| `/api/v1/offers/{id}/` (detail) | GET | 200 | Single offer, same shape as a search card under `data`. |

Endpoints that do **not** exist (404): `/api/v1/geo-encoder/cities/`,
`/api/v1/geo/regions/`, `/api/v2/friendly-links/query-params/...`. Geo lookups are
not needed — city/region ids come embedded in every offer's `location` block.

## Required headers

Minimal working set (no anti-bot gate, unlike Kaspi's referer requirement):

```
user-agent: <browser UA>
accept: application/json, text/plain, */*
accept-language: ru-RU,ru;q=0.9,en;q=0.8
```

No cookie, no referer, no `x-requested-with` required. Bare UA alone likely also
works but a full browser header set is kept for safety.

## Search params

| Param | Meaning | Example |
|---|---|---|
| `query` | full-text search | `query=ирригатор` |
| `offset` / `limit` | pagination (offset-based) | `offset=0&limit=40` |
| `city_id` | filter to one city | `city_id=1` (Almaty) |
| `region_id` | filter to one region/oblast | `region_id=8` (Алматинская обл.) |
| `filter_enum_state[0]` | condition | `filter_enum_state[0]=used` (or `new`) |
| `filter_float_price:from` / `:to` | price range (KZT) | `filter_float_price:to=40000` |

**Confirmed working:** `city_id=1` returns only Almaty offers (`total` 55 for query
`ирригатор`); `filter_enum_state[0]=used` returns only used items; combining
`used` + `:to=40000` narrowed to 37. `region_id=5` = Karaganda oblast (sanity check —
so region ids are oblast-level, not city-level; use `city_id=1` to pin Almaty city).

## Almaty ids (confirmed)

- **`city_id = 1`** = город Алматы.
- `region_id = 8` = Алматинская область (the surrounding oblast; broader than the city).

Both discovered from the `location` block embedded in offers. Other observed ids for
reference: Астана city_id=87 (region 13), Семей city_id=58 (region 14), Караганда
city_id=39 (region 5).

## Pagination

Offset-based. `metadata.total_elements` = total hits; `links.next.href` gives the
next page URL (increments `offset` by `limit`). Promoted ads are interleaved and
flagged in `metadata.promoted` / `metadata.source.{promoted,organic}` (index lists).

## Response shape

Top level: `data[]`, `metadata` (`total_elements`, `visible_total_count`,
`search_id`, `promoted`, ...), `links` (`self`/`next`/`first`).

Each offer in `data[]`:

- `id` (int), `url` (public html url), `title` (str), `description` (str)
- `created_time`, `last_refresh_time`, `valid_to_time` (ISO 8601, `+05:00`)
- `business` (bool — **the reliable private-vs-shop signal**; `True` = business seller)
- `offer_type` (`offer`), `status`
- `location`: `{ city: {id, name, normalized_name}, region: {id, name, normalized_name}, district? }`
  - **Note:** the top-level `city`/`region`/`district` fields are always `null`;
    real values live under `location.city.name` etc.
- `photos[]`: `{id, filename, width, height, link}` — `link` is a template with
  `{width}x{height}` placeholders (e.g. `.../image;s={width}x{height}`); substitute
  real dims to get a usable URL.
- `params[]`: list of `{key, name, type, value}`. Key ones:
  - `price` → `value.value` (int KZT), `value.currency`, `value.label` (`"20 000 тг."`),
    `value.negotiable`, `value.arranged`. **May be absent** (service/"договорная" ads).
  - `state` → `value.key` (`new` / `used`), `value.label` (`Новый` / `Б/у`). May be absent.
- `user`: `{id, name, seller_type, is_online, last_seen, company_name, ...}`
  (`seller_type` was `null` in all sampled offers — use `business` bool instead).
- `category`, `delivery`, `safedeal`, `shop`, `partner`, `contact`, `map`.

No ratings/reviews anywhere in the OLX payload — OLX has no product reviews.

## Samples saved

`tests/samples/olx/search.json` (metadata+links kept, `data[]` trimmed to 4 offers
chosen for variety: no-price/business, new+private, used+Almaty, new+Almaty; photos
arrays trimmed to 2). `tests/samples/olx/detail.json` (single offer detail).

## Anti-bot observations

No 429/403/CAPTCHA across 11 requests from a home KZ IP with plain headers. Much more
open than Kaspi. Datacenter IPs untested — if they get blocked, the same
`KASPI_PROXY`-style residential-proxy fallback pattern applies.

## Fallback plan (NOT built now)

If the JSON API is ever blocked or changes: OLX list pages embed a large HTML payload
(`/list/q-<query>/` returned 200, ~3 MB). A Playwright/selectolax HTML parser could be
added as a fallback source provider, mirroring the Ozon browser-session pattern. Not
needed today — the JSON API works cleanly.
