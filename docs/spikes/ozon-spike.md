# Ozon.kz composer-api — live spike test

Date: 2026-07-18. Machine IP: home KZ residential. Reference (ozon.**ru**):
`external_examples/ozon-mcp-server/src/{browser.js,parse.js,ozon.js}` (MIT).
Query used: `ирригатор`. Goal: verify the ozon.ru composer-api browser-session
pattern works on **ozon.kz** before building the module.

## Verdict: WORKS on ozon.kz — with one transport change

Headless Chromium (reference stealth args) passes the ozon.kz anti-bot (Variti)
challenge on the **first** attempt. Home page title after a ~15 s wait was the
normal `OZON Казахстан маркетплейс – миллионы товаров...` — no antibot block.
All three composer-api pages (search, PDP, reviews) returned **HTTP 200** and
valid JSON. Total live requests during the spike: ~4 legit navigations + a few
diagnostic attempts, well under a gentle budget.

## THE key difference vs the ozon.ru reference: transport

The reference issues requests as an **in-page `fetch()`** from the challenged
main page (`page.evaluate(fetch(API+path))`). On ozon.kz this **fails**:

```
Page.evaluate: TypeError: Failed to fetch
```

It fails the same way with `credentials: 'include'` and after a longer challenge
wait — it is not a timing or cookie issue. The composer-api simply refuses the
programmatic same-origin XHR/fetch on the .kz property.

**What works instead: `page.goto()` navigation** directly to the composer-api
URL, then read the JSON off `document.body.innerText`:

```
https://www.ozon.kz/api/composer-api.bx/page/json/v2?url=<url-encoded path>
```

This returns HTTP 200 and the full composer-api JSON body. The module
(`browser.py`) therefore uses navigation as its transport and serializes
navigations behind an `asyncio.Lock` (navigation mutates the shared page, unlike
the reference's concurrency-safe fetch). Everything else from the reference
design is kept: single long-lived context, challenge-once, no image/css route
blocking, 403/307-or-dead → relaunch + 1 retry, 10-min idle teardown.

## Widget key names on .kz — identical to .ru

Same widget names, so the `parse.js` widget-name-prefix matching ports directly
(split key on first `-`):

- **search**: `tileGridDesktop` → `items[]`, each with `mainState[]`
  (`priceV2` / `textDS` name / `labelListV2` rating).
- **PDP**: `webPrice`, `webProductHeading`, `webCurrentSeller`,
  `webShortCharacteristics`, `webSingleProductScore`, `webReviewProductScore`,
  `webGallery`, `webBrand`, `webSale`, etc.
- **reviews**: `webListReviews` → `reviews[]`, each with
  `content.{comment,positive,negative,score}`, `author.{firstName,lastName}`,
  `publishedAt` (unix), `isItemPurchased`.

## Currency & price format

Currency is **KZT**, symbol `₸` (reference was `₽`). `price_to_int` strips all
non-digits, so `"14 589 ₸"` → `14589` works unchanged.

**Gotcha unique to .kz search tiles:** each tile carries **two** `priceV2`
blocks — an **installment** price first (`"1 216 ₸ × 12 мес"`, color
`textWarning`), then the real price (`"14 589 ₸"` + optional `ORIGINAL_PRICE`
`"45 903 ₸"`). Naive "first priceV2" (the reference approach) yields the
installment and `price_to_int` mangles it to `121612`. `parse._search_price`
skips any PRICE text matching `мес|×|/` and takes the first real price block.
(PDP `webPrice` has a single clean `price`/`originalPrice`, no installment.)

## Verified data (sample: PECHAM Trip PC-117-1 irrigator, sku 1710439815)

| Field | Value |
|---|---|
| search results | 8 tiles, prices 8 122–29 205 ₸ |
| PDP price / original | 14 589 ₸ / 45 903 ₸, `isAvailable: true` |
| seller (`webCurrentSeller`) | `PECHAM - LLC Wellne...` (Ozon truncates it), rating 4.9 |
| product score | `webSingleProductScore` = `"4.9 • 33 836 отзывов"` |
| short characteristics | 5 keys (Тип ирригатора, Емкость…, etc.) |
| reviews | `webListReviews` → 30 items; unix `publishedAt` → ISO date |

## Other differences vs ozon.ru reference

- `.kz` search adds `categoryBrandList`; PDP adds `webAspects`, `webBestSeller`,
  `webPriceDecreasedCompact`, `bigPromoPDP` — none needed for MVP, and
  name-prefix matching keeps `webPrice` from matching `webPriceDecreasedCompact`.
- No Kaspi-style multi-seller offer list: a product = one buy-box offer
  (`webPrice` + `webCurrentSeller`). `OzonSource.offers` returns a single
  `RawOffer`.
- ozon.kz serves a single region; there is no `city` param, so `OzonSource`
  accepts `city` for interface parity but ignores it.

## Samples saved

Prettified, top-level arrays truncated (widget JSON strings kept intact) to
`tests/samples/ozon/`: `search.json`, `product.json`, `reviews.json`. Parse
tests (`tests/sources/test_ozon_parse.py`) run against them — no network.

## Config

Added `OZON_BASE_URL` (default `https://www.ozon.kz`) to `Settings`.
