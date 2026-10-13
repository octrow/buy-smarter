# kaspi-mcp-free (mkhamzanov) и kaspipulse (gzharassov) — Study

Клоны лежат в `external_examples/`, код не запускался. Ниже `MS` — наш `pylibs/market-sources`.

## kaspi-mcp-free

`triage: step 3 · close 0.67 · verdict: → Inspect → эндпоинты уже у нас, новое только в полях отзывов`

FastMCP-обёртка над `shop.py` (httpx). Город по умолчанию Астана, 710000000. С датацентровых IP Kaspi отвечает 429, поэтому предусмотрен `KASPI_PROXY`.

| Эндпоинт | `shop.py` | У нас |
|---|---|---|
| `GET /yml/product-view/pl/filters?text&page&c&ui=d&sort` (sort: relevance/price-asc/price-desc/rating/created-desc, 12 шт/стр, до 8 стр) | L79-92 | есть |
| `POST /yml/offer-view/offers/{id}` `{cityId,id,merchantUID:"",limit:32,page:0,sort:true,installationId:"-1"}` | L134-144 | есть, то же тело |
| `GET /yml/review-view/api/v1/reviews/product/{id}?filter&sort&limit&withAgg=true` | L172-212 | есть, но только `COMMENT`/`POPULARITY` |
| `GET /yml/content/item/product/{id}?c=` | L222-229 | есть |

**Что новое для нас** находится в ответе отзывов, который мы уже получаем и наполовину выбрасываем (`MS kaspi/parse.py` L136-154):
- `summary.statistic[{rate,count}]` — распределение по звёздам;
- `summary.global` — общий рейтинг;
- `groupSummary[{id,total}]` — счётчики ALL, COMMENT, PICTURE, POSITIVE, NEGATIVE;
- `imagesSummaryCount`;
- у каждого отзыва `comment.{text, plus, minus}`.

Фильтры `NEGATIVE` и `PICTURE` и сортировку `DATE` мы не используем.

## kaspipulse

`triage: step 3 · close 0.68 · verdict: doubt → полезна только формула velocity`

Open-data проект по одной нише Kaspi.

**Сбор** (`scraper/fetch.py` L24-85):
- запрос `GET /yml/product-view/pl/results?q=:category:<CODE>:availableInZones:<ZONE>&c=<city>&...`, до 30 страниц;
- пауза 2-5 с, 3 ретрая;
- зоны: Алматы Magnum_ZONE1, Астана Magnum_ZONE5.

**Формула** (`pipeline/estimate.py` L66-97, повторена в BigQuery `sql/02`, `sql/04`):

```
delta      = reviews_count - reviews_count(ближайший снапшот ≥7 дней назад)   # <0 → 0, флаг review_reset
weekly     = delta / (days_diff / 7)
est_sales  = weekly / 0.02     # REVIEW_RATE, не откалиброван, взят «как у Amazon»
```

Окно не меньше 7 дней обязательно: при сравнении день к дню один новый отзыв даёт около 350 «продаж» в неделю.

**Выбросы** (`scraper/clean.py` L192-240): берутся только положительные дельты. Флаг ставится при `delta > Q3 + 3·IQR`, но не раньше чем при 8 точках. Выбросы помечаются, не удаляются.

**Можно ли посчитать у нас.** Пока нет. `PriceSnapshot` (`db/models.py` L69-80) хранит только `min_price`, `offer_count` и `captured_at`. `Product.review_count` перезаписывается на месте, и история теряется.

## Что портировать (по ценности и затратам)

1. **Сохранять `summary.statistic`, `groupSummary` и `plus/minus`** из уже существующего вызова отзывов: ноль новых запросов. Распределение звёзд идёт в `ranking/review_trust.py` (J-образная кривая — признак накрутки). `plus/minus` идут в суммаризацию готовыми.
2. **`review_count` и `rating` в `PriceSnapshot`** (маленькая миграция). Заполнять там, где вызывается `add_price_snapshot`: `db/repo.py` L89, `services/recommend.py` L1206 и L1859.
3. **Velocity**: дельта к снапшоту не моложе 7 дней, отрицательные обнулять, нормировать на неделю. ~15 строк. Это сигнал спроса для оси популярности, а не «продажи». Есть оговорка: снапшоты пишутся только при `recommend` и `refresh`, интервалы неровные.
4. **Отдельный вызов `filter=NEGATIVE` и `sort=DATE`** для майнинга жалоб и свежести: один лишний запрос на финалиста.
5. Отложить: IQR-флаг (имеет смысл только на категорийных батчах), обход категорий и BigQuery.
