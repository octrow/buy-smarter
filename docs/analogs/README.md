# Аналоги buy-smarter (repo-scout, 2026-10-13)

Поиск: `repo-scout --project buy-smarter`, два прохода.
1. AI shopping assistant / парсеры маркетплейсов / MCP (6 запросов + round two + Index).
2. Kaspi.kz / OLX.kz / детекция фейковых отзывов / альтернативы Keepa.

Вердикты сохранены в локальный индекс repo-scout.

## Итог

**Готовой замены buy-smarter нет.** Ни один найденный проект не делает всё сразу: Казахстан (Kaspi + OLX б/у), тиры брендов и моделей, анти-накрутку отзывов, историю цен и объяснимый скор. Ближайшие аналоги покрывают одну-две оси. Брать у них стоит отдельные приёмы, а не основу.

Ниша при этом заметно заполнилась за 2026 год: вышло много MCP-серверов и Agent Skills для маркетплейсов РФ и KZ. Получается, что форма «MCP/скилл поверх площадки» стала стандартной. Отличие buy-smarter в слое знаний и ранжирования, а не в доступе к данным.

## Топ: что брать

| Репо | Что это | Что взять | Вердикт |
|---|---|---|---|
| [sw1pp3r/kaspi-skill](https://github.com/sw1pp3r/kaspi-skill) | Agent Skill + stdlib CLI для Kaspi: поиск, точный матч модели, проверка продавца, доставки и спеков | 1) проверка финалистов через seller-offers: цена конкретного продавца и **абсолютная дата доставки**; 2) поиск **противоречий в спеках** (не выбирать одно значение молча); 3) QR в приложение Kaspi | → Study |
| [d3c0r1x/smart-shopper](https://github.com/d3c0r1x/smart-shopper) | AI-ассистент для Ozon/WB/YM: текст + фото, Telegram + Mini App, LLM gateway с fallback | 1) **проверка требований по отзывам**: каждое требование пользователя получает ✅ подтверждено / ❌ опровергнуто / ⚠️ мало данных (прямо ложится на идею «жалобы из отзывов → скор/confidence»); 2) поиск по фото; 3) `docs/DECISIONS.md` | → Study |
| [prikotov/shop-scout](https://github.com/prikotov/shop-scout) | PHP-скилл: Ozon/WB/YM, CSV + MD-отчёт | эвристики **мусорных отзывов** («ещё не пользовался», «курьеру 5★», «норм»); честное предупреждение «искомый бренд не найден, всё ниже аналоги»; отдельно «дешевле-оригинал» и «рекомендую» | → Inspect (только идеи, PHP) |
| [mkhamzanov/kaspi-mcp-free](https://github.com/mkhamzanov/kaspi-mcp-free) | MCP для Kaspi на httpx: search / offers / reviews | эндпоинт отзывов Kaspi с фильтрами `COMMENT/PICTURE/POSITIVE/NEGATIVE` и сортировкой: дёшево вытянуть **только негатив** для суммаризации жалоб | → Inspect |
| [Vladimir-Human/ru-marketplace-mcp](https://github.com/Vladimir-Human/ru-marketplace-mcp) | 11 площадок РФ/CN как MCP, 146★, активен | анти-бот через **собственный Chrome по CDP** с challenge handoff (альтернатива Playwright-сессии для Ozon); `docs/ADDING_A_SOURCE.md` как шаблон для `market-sources` | → Inspect |
| [neosheps/ozon-shopping-mcp](https://github.com/neosheps/ozon-shopping-mcp) | Ozon для покупателя: поиск, карточки, отзывы (плюсы/минусы) | как держат headless-сессию Ozon после разовой проверки; разбор плюсов/минусов из отзывов | → Inspect |
| [gzharassov/kaspipulse](https://github.com/gzharassov/kaspipulse) | open-data по нише Kaspi, оценка продаж по review-velocity | **review-velocity** (прирост отзывов во времени) как оценка продаж: возможная замена оси «популярность». Уже есть снапшоты в `refresh`, считать можно из них | doubt |

Отброшены: `d3c0r1x/marketplace-price-compare` (подмножество smart-shopper), `lerdem/olx-parser` (мёртв с 2021, у нас уже есть OLX REST), Ozon/WB **seller**-MCP (`ozon-mcp-server`, `marketplaces-mcp-ru` и т.п.): это кабинет продавца, а не покупателя. Kaspi-репрайсеры (`kaspi-dumping`, `kaspi-ads-autopilot`) решают задачу продавца, но в них можно подсмотреть рабочие эндпоинты конкурентных офферов. Общие инструменты (crawl4ai, browser-use, Scrapegraph-ai) в ядро не нужны: источники уже работают на JSON-эндпоинтах.

## Коммерческие аналоги (по памяти, не проверено поиском)

- **Ассистенты внутри площадок и поисковиков** (ChatGPT shopping, Perplexity, Google AI Mode, Amazon Rufus, нейро-поиск на Яндекс Маркете): хорошо понимают запрос, но Kaspi/OLX.kz почти не видят и не умеют штрафовать накрутку отзывов.
- **Fakespot / ReviewMeta**: анализ подлинности отзывов, в основном для Amazon. Fakespot, насколько помню, закрыт в 2025 году. Ниша свободна, особенно для KZ.
- **Keepa / CamelCamelCamel**: история цен для Amazon. Для Kaspi публичного аналога в поиске не нашлось, так что `refresh`/`history` в buy-smarter пока уникальны.

## Предложения после Study (сводный план)

Детали и ссылки на строки: [smart-shopper.md](smart-shopper.md), [kaspi-skill.md](kaspi-skill.md), [kaspi-mcp-free_kaspipulse.md](kaspi-mcp-free_kaspipulse.md), [shop-scout_ozon-mcp.md](shop-scout_ozon-mcp.md).

Поправка к первичному отбору: seller-offers, спеки и отзывы Kaspi у нас **уже** берутся тем же эндпоинтом (`market-sources/kaspi/client.py`), а `review_trust` подключён (`recommend.py:1437`). Брать стоит обработку данных, а не доступ к ним.

**Пакет A — отзывы, без новых запросов и почти без LLM:**
1. Расширить `RawReview` полями `pros`, `cons`, `useful`, `purchased`, `has_photos`. Kaspi отдаёт `comment.plus/minus`, Ozon — `positive/negative`, `isItemPurchased`, `photos`. Сейчас всё это склеивается или выбрасывается. *(shop-scout, ozon-shopping-mcp, kaspi-mcp-free)*
2. Сохранять агрегаты Kaspi из того же ответа: `summary.statistic` (распределение звёзд), `groupSummary` (число негативных). Отдать их в `review_trust`.
3. `filter_junk()` — регэкспы мусорных отзывов из shop-scout. Доля мусора идёт в trust, в суммаризацию — только содержательные тексты.
4. Регэксп брака по минусам и тексту: штраф и строка «жалобы на брак: N».

**Пакет B — проверка требований по отзывам** *(smart-shopper)*:
5. Вердикт по каждой непроверенной фиче (✅/❌/⚠️ + цитата) полями в существующем вызове `ReviewSummary`. В коде проверять, что цитата — подстрока отзыва и mentions ≥ 2. Отзывы подбирать так: сначала негатив, затем совпадения по ключевым словам. Применять в `_review_pass`: confirmed снимает «не удалось проверить», refuted ставит ×0.8 и con.
6. Детерминированный fallback по совпадению основ слов для `--no-llm`.

**Пакет C — Kaspi-обработка** *(kaspi-skill)*:
7. Доставка: `deliveryOptions[*].delivery` → дата в Asia/Almaty → «привезут завтра, бесплатно» на оси «наличие».
8. Релевантность: токены модели (с цифрами) ×3, компактное сравнение `ipx-7 = ipx7`, отсев чужих вариантов.
9. Дедуп листингов по каноническому ключу модели.

**Пакет D — данные во времени и устойчивость:**
10. `review_count` и `rating` в `PriceSnapshot` → review-velocity (окно ≥ 7 дней) как сигнал спроса *(kaspipulse)*.
11. Ozon: `storage_state` после разового прохождения проверки в видимом браузере и явное «сессия истекла» *(ozon-shopping-mcp)*. CDP-ступень — только если и это будет блокироваться *(ru-marketplace-mcp)*.

Рекомендация: начинать с A (1-3) и B (5). Это прямо закрывает пункт «обратная связь жалоб из отзывов в скор/confidence» из статуса README.
