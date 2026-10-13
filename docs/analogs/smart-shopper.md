# smart-shopper (d3c0r1x) — Study

`triage: step 3 · close 0.72 · score 3 (fresh 3, pop 0) · verdict: → Study (проверка требований по отзывам)`

Репо: https://github.com/d3c0r1x/smart-shopper, клон: `external_examples/smart-shopper` (код не запускался). AI-ассистент для Ozon/WB/YM: Telegram + Mini App, текст + фото, LLM gateway с fallback.

## Проверка требований по отзывам

- **Требования** — `SearchConstraints.must_have: list[str]` (`models.py:74-90`), свободный текст. Извлекаются LLM (`llm/prompts.py:28-34`) плюс детерминированный regex-парсер цены, рейтинга и бренда `search/structfilter.py:213 parse_structural`.
- **Подача отзывов** — `orchestrator.py:447-471 _load_reviews`: первые 40 отзывов (`REVIEWS_PER_PRODUCT`), каждый обрезан до 500 символов, кэш 24 ч. Отбора нет, негатив вперёд не ставится, поля pros/cons игнорируются.
- **Промпт** — `REVIEW_PROMPT` (`prompts.py:40-46`): «по каждому требованию вердикт confirmed / rejected / no_data — с числом упоминаний и 1-2 дословными цитатами». Перед ним стоит `GROUNDING_RULE` (`prompts.py:10-22`) против prompt injection.
- **Схема** — `RequirementVerdict{requirement, verdict, mentions:int, quote:str}` (`models.py:54-71`) внутри `ReviewAnalysis` вместе с pros, cons и summary.
- **«Мало данных»** решает только LLM, порога упоминаний в коде нет. **Цитаты не сверяются с текстами отзывов**: защита от галлюцинаций держится только на промпте и pydantic.
- **Влияние на ранжирование** — `rank_by_verdicts` (`review/intelligence.py:85-106`): confirmed +3, rejected −4, плоская сумма без весов. Rejected опускается вниз, но не выкидывается. Подключение: `orchestrator.py:282-308`, параллельно через `asyncio.gather`, в выдачу идёт топ-3.
- **Fallback без LLM** — `providers.py:420-432 _scan_reviews_for`: совпадение по основе из 4 символов плюс грубое отрицание («не», «нет»), первое совпавшее предложение идёт в цитату. Цитата заведомо настоящая.

## Прочее

- **Rerank** — `search/rerank.py:36-126`: 0.45 семантика (bge-m3) + 0.35 лексика (0.65 покрытие токенов + 0.35 difflib) + 0.20 структура (−0.5 за нарушение, без жёсткого отсева). Без эмбеддингов вес семантики переходит к лексике.
- **Сопоставление между площадками** — `matcher/matcher.py:59-90`: EAN, иначе difflib ≥ 0.55, затем LLM-арбитр. Без EAN товары не склеиваются (DECISIONS §7).
- **Gateway** — дневной бюджет считается по вызову, а не по каждой попытке fallback. Есть общий дедлайн `STRUCTURED_MAX_SECONDS` и throttle 20 запросов в минуту. Локальная Ollama используется только для rank и freeform, анализ отзывов идёт в облако (DECISIONS §5).
- **Фото** — data URI → vision-вызов со схемой `VisionDescription` (категория, цвет, материал, детали, 2-3 поисковых запроса) → поиск по `search_queries[0]` с `must_have=details`.

## Сравнение с buy-smarter

| | smart-shopper | buy-smarter |
|---|---|---|
| Суммаризация отзывов | pros/cons/summary | `llm/review_summary.py:summarize_reviews`: sonnet, ≤30 текстов, плюс red_flags, counterfeit, reliability |
| Штрафы по отзывам | нет | `_apply_summary_penalties` (`services/recommend.py:1393`), `ranking/review_trust.py` |
| Вердикт по каждому требованию + цитата | **есть** | нет |
| Фичи без спеков | — | только con «не удалось проверить: f» (`ranking/engine.py:538 _unverified_feature_cons`) |

## Что портировать (по ценности и затратам)

1. **Вердикт по требованию плюс цитата** полями в существующем вызове `ReviewSummary`, без лишнего LLM-вызова. Проверять только фичи, где `_feature_present` (`engine.py:90`) вернул False.
2. **Проверка в коде, которой нет у них**: цитата должна быть подстрокой текста отзыва, иначе вердикт сбрасывается. При mentions < 2 вердикт понижается до insufficient.
3. **Применение в `_review_pass`** (`recommend.py:1424`): confirmed снимает con «не удалось проверить» и даёт частичный кредит по спекам, refuted ставит множитель ×0.8 и con. Вместо их +3/−4 брать веса фич.
4. **Отбор отзывов**: сначала негатив (rating ≤ 3), затем выборка с совпадениями по ключевым словам фич, каждый отзыв обрезать до 500 символов. Сейчас берутся просто первые 30.
5. **Детерминированный fallback** `_scan_reviews_for` для режима `--no-llm`.
6. Под вопросом: гибридный rerank (имеет смысл только с семантическим поиском), дневной бюджет и дедлайн в gateway.
