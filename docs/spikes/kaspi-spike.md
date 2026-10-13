# Kaspi.kz internal JSON endpoints — live spike test

Date: 2026-07-18. Machine IP: home KZ residential (not datacenter). Reference: `external_examples/kaspi-mcp-free/shop.py`.
Query used: `ирригатор`. All 4 target endpoints returned `200` on the first try — **no 429/403 from datacenter-style throttling observed**; the only 403s seen were the deliberate minimal-header experiments below.

## Results per endpoint

| Endpoint | Method | Status | Worked | Notes |
|---|---|---|---|---|
| `yml/product-view/pl/filters` (search) | GET | 200 | yes | Both cityId 750000000 and 710000000 returned 200, `total≈1275/1277`, 12 cards/page |
| `yml/offer-view/offers/{id}` (offers) | POST | 200 | yes | product_id `106935673` (Aulandi HF-6), 1 offer/seller returned |
| `yml/review-view/api/v1/reviews/product/{id}` (reviews) | GET | 200 | yes | `limit=5, withAgg=true` |
| `yml/content/item/product/{id}` (specs) | GET | 200 | yes | `c=750000000` |

Raw samples (prettified, arrays truncated to first 3–5 items) saved to `tests/samples/kaspi/`:
`search.json`, `offers.json`, `reviews.json`, `specs.json`.

## Almaty cityId

**Confirmed: `750000000` = Almaty**, `710000000` = Astana/Nur-Sultan. Both are valid/live cityIds (both returned 200, slightly different `total` counts: 1275 vs 1277, i.e. city affects region-specific availability/assortment but not gross reachability). The search JSON payload itself carries no explicit city label per card — confirmation came from static evidence, not response content: `external_examples/kaspi-api-wrapper` (comments/docstrings) explicitly states `750000000` = Almaty (e.g. `KaspiMerchantProfileClient.cs:71`, `KaspiProductDetailExtractor.cs:44`, `RegionModels.cs:13`). `shop.py`'s `DEFAULT_CITY = "710000000"` is Astana, not Almaty — **use `750000000` as the default cityId for this project**, not the value hardcoded in the reference script.

## Minimal working header set (tested on `search` endpoint only, one endpoint per instructions)

Baseline (full shop.py header set) → 200. Then tested reduced sets:

| Header set | Status |
|---|---|
| `user-agent` + `accept` only (bare) | **403** |
| bare + `referer: https://kaspi.kz/shop/search/` | **200** |
| bare + `cookie: kaspi.storefront.cookie.city=...` (no referer) | 403 |
| bare + `x-requested-with: XMLHttpRequest` (no referer) | 403 |

**Conclusion: `referer` is the load-bearing header** — `user-agent` + `accept` + correct `referer` is sufficient to get 200 on search. `cookie` and `x-requested-with` alone do not substitute for referer. This matches the comment already in `shop.py`: "Защита Kaspi — только заголовки referer + user-agent." Not independently re-tested per-header on offers/reviews/specs (instructed to test on search only) — recommend keeping the full `shop.py` header recipe (referer + UA + `x-ks-city` + city cookie) for those three endpoints since they need city context that search embeds in the URL query (`c=`) instead.

## Response shape summary

**Search** (`data.cards[]`): `id`, `title`, `brand`, `unitSalePrice`/`unitPrice`, `priceFormatted`, `rating`, `reviewsQuantity`, `deliveryDuration`, `category`, `bestMerchant`, `previewImages[]`, `shopLink`. Top-level `data`: `total`, `limit`, `title`, `categoryId`, plus facet/filter metadata (`filters`, `filterCategory`, `tabs`, `sortSelector`, etc.) not needed for MVP.

**Offers** (`POST offers/{id}`): top-level `offers[]`, `total`, `offersCount`, plus facet flags (`highRatingPresent`, `brandOfficialPartnerPresent`, `excellentMerchantPresent`). Each offer: `merchantId`, `merchantName`, `merchantRating`, `merchantReviewsQuantity`, `price`, `priceBeforeDiscount`, `discount`, `delivery`/`pickup` timestamps, `deliveryType`, `deliveryDuration`, `kaspiDelivery` (bool), `deliveryOptions` keyed by type (`POSTOMAT`, `EXPRESS`, ...) each with its own `delivery`/`deliveryDays`.

**Reviews**: top-level `data[]` (each: `author`, `date`, `rating`, `comment.text/plus/minus`), `summary` (`global` rating, `statistic[]` star breakdown), `groupSummary[]` (`id`: `ALL`/`COMMENT`/`PICTURE`/`POSITIVE`/`NEGATIVE` → `total`), `imagesSummaryCount`.

**Specs**: `card` (title/name, category, `promoConditions.brand`, `shopLink`), `specifications[]` (grouped: `name` + `features[]`, each feature has `name`, `featureValues[]` with `.value`, `important`, `visible`), `galleryImages[]`, `description`, `videoId`/`videos`, `weight`, `masterCategoryCode`/`Name`, `restrictedAge`, `hasVariants`, `certificate`, `aiGenerated` (interesting new field — Kaspi flags AI-generated content).

## Anti-bot observations

- No rate limiting or CAPTCHA/429 encountered across 9 total requests (5 legit + 4 header-ablation) from this home KZ IP.
- Anti-bot gate is purely `referer`-based on search; did not need UA rotation or jitter beyond the requested 1.5s delay to get consistent 200s.
- Confirms the risk note in `docs/PLAN.md` ("Kaspi 429-ит датацентровые IP") — home IP works fine; datacenter IPs likely still need the residential-proxy fallback already planned (`KASPI_PROXY` env var in `shop.py`).

## Caveat

Only 9 requests were sent (well under the 10-request budget), single query (`ирригатор`) and single product id (`106935673`). Header minimality was validated only for `search`; extrapolating the same minimal set to `offers`/`reviews`/`specs` is not confirmed empirically in this spike — recommend a follow-up micro-spike before removing headers from the production `kaspi/` source module.
