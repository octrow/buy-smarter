# План: Ф1 — профили категории и контракт возможностей

Фаза 1 по ADR-0005 (`docs/adr/0005-category-profiles-and-capabilities.md`). Этот документ — только про фундамент:
профиль категории, контракт Capability, правило весов и перенос сегодняшних **веток** ядра на возможности.
Intent, уточнение, классификатор Jev и новые возможности — Ф2–Ф4.

Цель: при **точном** совпадении категории рейтинг тот же, что до Ф1 (порядок, баллы, роли, причины отказов, плюсы
и минусы), а ядро не знает про состав и смартфоны. Поиск похожей категории меняется намеренно: без подстроки (#15),
одним вызовом (#7), синонимы OLX у владельца книги (#10), лечение старых записей.

Попутно закрываются отложенные шаги 6–8 плана `2026-10-22-composition-fixes.md`.

Версия 2: переписан по независимому ревью (Opus, 2026-10-22). Главные отличия от версии 1: used market и official
stores не переносятся (они не ветки); у `reject` две точки и предикат для всех пулов; `relevance` и `query`
реализуются уже в Ф1; `save_knowledge` сливает поля вместо пересоздания записи; лечение распознаёт копию книг;
характеризационных сценариев 12, а не 6.

## Что есть сейчас (2026-10-22)

**Знания о категории** — таблица `category_knowledge` (`db/models.py:116`): `category` (slug, unique) и `schema_json`
с ключами `brand_book`, `model_book`, `category_type`, `ingredient_book`, `generated_by`, `updated_at`
(`knowledge/store.py:53-58`, типы `:63-64`).
- Читают две независимые функции: `resolve_books` (`store.py:300`) и `resolve_composition` (`store.py:141`). Каждая сама ищет похожую категорию: подстрока без границы слова (`_substring_match`, `store.py:203`), потом Jev или LLM (`_match_category`, `store.py:276`). Какую нашли, наружу не возвращается.
- `save_knowledge` (`store.py:96-123`) собирает запись заново: сохраняет только книгу ингредиентов и тип из старой, остальные ключи теряются; `generated_by` перезаписывается.
- Писатели: `learn_category` (`knowledge/generate.py:219`, его вызывает GUI), `cli/main.py:472,519` (`learn`, `learn --file`), `_persist_learned_aliases` (`services/matching.py:543-549`) — пишет **обе** книги: `book or BrandBook()` и книгу моделей, слитую с синонимами. `learn --file` сливает знания с `resolve_composition(..., use_llm=False)` (`cli/main.py:509-518`). `refresh` знания не читает.
- Встроенные книги для «ирригатора» находятся подстрокой (`store.py:317`, `knowledge/brands.py:296-305`, `models_book.py:139-149`).

**Ветки по категории в ядре:**

| Что | Условие | Где |
|---|---|---|
| composition: поиск по веществам | книга ингредиентов | `recommend.py` `_substance_recall`, `collect.py` `substance_plan` |
| composition: защищённые от фильтра релевантности | то же | `recommend.py:441-449`, `_filter_relevant_candidates:707-716` |
| composition: извлечение, вещество из названия, проверка `NoSubstanceMatch` | то же | `recommend.py` `_verify_substances`, `_guard_substance_pool`; ловит `_measured` (`:326`); импортируют `cli/main.py:62`, `cli/report.py:32`, `mcp_server/server.py:27`, `worker.py:33`, `gui/screens/search.py:44` |
| composition: концентрация не идёт в жёсткие свойства | `required_substances` | `ranking/filters.py:79-85` |
| composition: «вещество доказанно отсутствует» | то же | `ranking/filters.py` `_content_rejection`, после свойств |
| composition: ось, контекст пула после фильтров, веса | `composition=True` | `ranking/engine.py` `rank`, `COMPOSITION_WEIGHTS`, `_score_axes:568` (ось первой), `ranking/ingredient_axis.py` |
| composition: замена минусов | `ingredient_ctx` | `engine.py` `_verdict_pros_cons` → `ingredient_cons`, `equivalence_verdicts` |
| composition: флаг результата | composition | `recommend.py:508` `ingredients_unchecked`; читают `cli/report.py:87`, `gui/screens/report.py:171` |
| benchmark: Kimovil | основа «телефон» в первом слове | `recommend.py`, `services/phone_specs.py:30,43-45`; пишет specs (их потом проверяет жёсткий фильтр); индекс подмешан в ось характеристик `engine.py:101,365-368` |
| audience: основной пул | `constraints.gender` | `ranking/filters.py:112-116`, в `_listing_rejection` до свойств; «первая причина побеждает» (`:69`) |
| audience: пул OLX | то же | `recommend.py:520-521` (`_filter_used`) |
| audience: официальные магазины | то же | `official_stores.py:254,263-266`, **до** среза `[:_PER_STORE]` |
| audience: чтение из текста | regex | `recommend.py` `_apply_overrides` (`query_audience`) |
| size: убрать размер из категории, т.е. из поисковой фразы | `query_size(category)` | `recommend.py:565-566` |
| size: убрать фразы размера из свойств для одежды | `is_apparel` | `recommend.py:567-571` |
| size: минус «размер не проверен» | `constraints.size` | `engine.py` `_verdict_pros_cons` (`has_size`) |

Не ветки и в Ф1 не переносятся: used market (включается выбором источников, `collect.py:727`; у `recommend()` по умолчанию только `["kaspi"]`, `recommend.py:420`), official stores (включаются данными книги, `official_stores.py:243-245`), counterfeit (работает всегда), отбрасывание слов «б/у» (`recommend.py:584,608`; это разбор запроса, Ф2).

Размеры файлов: `services/recommend.py` 716, `ranking/engine.py` 749, `services/collect.py` 759, `services/matching.py` 727 — все у предела в 800.

## Решения

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| 1 | Где хранить профиль | Тот же `schema_json`. Новые ключи: `capabilities: {name: {enabled, params, set_by}}`, `synonyms`, `parent`, `updated_by`, `heal_checked`. Старые `category_type` и `ingredient_book` в v1 **пишутся тоже** (двойная запись), читаются адаптером: `composition_driven` → `capabilities.composition = {enabled, params: {ingredient_book}, set_by: "learned"}`. | Без alembic-миграции; откат на старую версию приложения читает запись правильно. |
| 2 | Сохранение | `save_knowledge` **сливает**: читает запись, обновляет только переданные поля, остальное оставляет. `generated_by` берётся из существующей записи, если он есть; последний писатель — `updated_by`. | Иначе любое сохранение синонимов стирает `capabilities`, `parent`, `synonyms`. |
| 3 | Ключ профиля в Ф1 | Slug строки категории, как сейчас. `product_type` появится в Ф2; перенос длинных slug («мужские-кроссовки-42») в `synonyms` — тоже Ф2. | Ф1 не трогает разбор запроса. |
| 4 | Поиск профиля | Один вызов `resolve_profile(category, use_llm) -> ResolvedProfile`: точный slug → синонимы → выбор Jev, при неуверенности LLM (как `_match_category`) → нет. Подстрока удаляется. Встроенные книги «ирригатора» не трогаем (статический запасной путь, одна категория). | #7, #15. |
| 5 | Владелец полей | `ResolvedProfile` хранит профиль и **владельца каждой книги** (`brand_book_owner`, `model_book_owner`, `capabilities_owner`): свой slug или slug родителя или найденного похожего. | Синонимы пишутся только туда, где лежит книга моделей; книга брендов чужого профиля не перетирается. |
| 6 | Наследование | `parent`, одна ступень: поле, которого нет у профиля, берётся у родителя. Слияние в `learn --file` берёт **собственную** запись без наследования. | Не копировать возможности родителя в ребёнка. |
| 7 | Синонимы OLX | `_persist_learned_aliases` обновляет только `model_book` у `model_book_owner`. Книгу брендов не пишет. | #10. |
| 8 | Лечение старых записей (решено: лечим) | Кандидат на лечение: нет `ingredient_book`, нет `heal_checked`, и `brand_book` записи **совпадает** с книгой брендов профиля, найденного шагом 4 без точного совпадения (признак копии, которую делал `_persist_learned_aliases`), и тот профиль — composition. Лечение: `parent` = тот профиль; скопированная `brand_book` удаляется; синонимы из `model_book` ребёнка переносятся в книгу родителя, своя `model_book` удаляется. Любой итог проверки ставит `heal_checked: true`, чтобы не звать Jev снова. Лог `knowledge healed: <slug> -> parent <slug>`. | Распознаём именно копию, а не любую spec-driven запись; повторных вызовов LLM нет. |
| 9 | Контракт | `capabilities/base.py`: `Capability` (Protocol), `CapabilityContext` (профиль, ограничения, параметры, use_llm), `REGISTRY: dict[str, Capability]`. Хуки и их точки — в таблице ниже. Хуки необязательные; ядро вызывает только существующие. | Решения грилинга 5 и 10 с поправками ревью. |
| 10 | Чистота ranking | `ranking/` не импортирует `capabilities/`. `recommend()` собирает из включённых возможностей готовые данные — веса, функции осей, предикаты отказа, функции минусов — и передаёт их в `rank()`. | Направление зависимостей: «ranking без DB, LLM, сети» (`engine.py:1-5`). |
| 11 | Правило весов | `capabilities/weights.py`: база + `axis_weight` и `weight_shifts` включённых возможностей → сумма, обрезка до 0, нормировка к 100. `COMPOSITION_WEIGHTS` удаляется; тест: composition даёт ровно прежние числа. | Решение грилинга 6. |
| 12 | Что переносим | composition, benchmark, audience, size. | Только то, что сегодня ветвится. |
| 13 | Benchmark | `Candidate.kimovil_index` → `benchmark_index` (0–100). Возможность реализует только `enrich` (Kimovil пишет specs и индекс). Смешивание индекса с осью характеристик остаётся в ядре и не знает источника. `services/phone_specs.py` остаётся провайдером. | Своей оси у бенчмарка нет, поведение не меняется. |
| 14 | Кто включает в Ф1 | Категорийные (composition, benchmark): из профиля; без записи в профиле — запасные правила с `set_by="fallback"` (benchmark — нынешняя основа «телефон»). Запросные (audience, size): по полям `QueryConstraints` и нынешним regex. | Jev и обучение — Ф3. |
| 15 | `NoSubstanceMatch` | Класс переезжает в `capabilities/composition.py`, `services.recommend` его реэкспортирует. Пять импортёров не меняются. | Минимум правок вне ядра. |
| 16 | Флаги результата | `RecommendResult.ingredients_unchecked` и `knowledge_missing` остаются полями. Значение `ingredients_unchecked` даёт хук `section` composition (возвращает предупреждение и флаг). | Рендеры CLI/GUI не меняются. |
| 17 | Файлы у предела | Код composition уходит из `recommend.py` и `engine.py` в `capabilities/composition.py` (туда же переезжает `ranking/ingredient_axis.py`), так что оба файла становятся короче. `collect.py` и `matching.py` правятся точечно. Любой файл сверх 800 строк — остановиться и разрезать. | Правило проекта. |

### Хуки и точки вызова

| Хук | Точка в потоке (как сейчас) | Кто реализует в Ф1 |
|---|---|---|
| `query(ctx, constraints) -> constraints` | шаг 4, после разбора, до поиска | size (убрать размер из категории и из свойств одежды) |
| `searches(ctx) -> list[(label, queries)]` | шаг 7–8, доп. поиски Kaspi | composition (вещества) |
| `relevance(ctx, pool) -> protected_ids` | шаг 12, до фильтра релевантности | composition (вещество в названии) |
| `reject_listing(ctx, text) -> reason | None` | предикат: шаг 17 после проверок ядра бюджет/бренд/доверие/наличие и **до** свойств; ядро применяет его и к пулу OLX (шаг 14), и к предложениям официальных магазинов до среза | audience |
| `hard_features(ctx, features) -> features` | шаг 17, перед проверкой свойств | composition (убрать концентрацию) |
| `enrich(ctx, pool)` | шаги 15–16, после дедупа | benchmark (Kimovil), composition (вещество из названия, извлечение, проверка) |
| `reject_content(ctx, cand) -> reason | None` | шаг 17, **после** проверки свойств | composition (доказанно отсутствует) |
| `prepare(ctx, passed) -> state` + `axis(ctx, state, cand)` | шаг 17: контекст по прошедшим фильтры; оси возможностей идут **перед** базовыми | composition |
| `notes(ctx, state, cand, pros, cons) -> (pros, cons)` | шаг 17, там же, где сейчас блок ингредиентов; может заменять минусы | composition, size |
| `section(ctx, result) -> Section | None` | после ранжирования: предупреждения и флаги | composition |
| `clarify(ctx) -> Question | None` | — | никто (Ф2) |

`axis_weight`, `weight_shifts` — атрибуты возможности. Порядок вызова между возможностями — порядок реестра.

## Файлы

- Новые: `src/buysmarter/capabilities/__init__.py`, `base.py`, `weights.py`, `composition.py` (+ перенесённый `ranking/ingredient_axis.py`), `benchmark.py`, `audience.py`, `size.py`; `src/buysmarter/knowledge/profile.py` (модель, адаптер, наследование, владельцы полей).
- Меняются: `knowledge/store.py` (`resolve_profile`, слияние, `generated_by`, лечение, без подстроки), `knowledge/generate.py` (`learn_category`, `extract_category_knowledge` — новый формат), `cli/main.py` (`learn --file` сливает с собственной записью), `services/recommend.py`, `services/collect.py` (доп. поиски из `searches`), `services/official_stores.py` (предикат аудитории параметром), `services/matching.py` (`_persist_learned_aliases` по владельцу), `ranking/engine.py`, `ranking/filters.py`, `ranking/models.py` (`benchmark_index`), `services/phone_specs.py` (`benchmark_index`).
- Тесты: `tests/capabilities/` (новая папка: `test_characterization.py`, `test_weights.py`, по файлу на возможность), `tests/knowledge/test_profile.py`. **Намеренно меняются:** тесты подстрочного поиска в `tests/knowledge/test_store.py`; тесты `kimovil_index`; тесты, импортирующие `ranking.ingredient_axis` (путь). Остальные — только при смене сигнатуры.
- Документы: `docs/logic-map.md` и `.ru.md` («Где поток делится» → «Capabilities», шаги 6, 7, 12, 15, 16, 17, 21), `CONTEXT.md` при необходимости, план находок (✅ #7, #10, #15), план по составу (✅ шаги 6–8), CHANGELOG.

## Порядок работы

Каждый шаг — тест первым (RED), потом код, коммит. После каждого шага весь набор зелёный.

1. ✅ **Характеризационные тесты — до любых правок.** `tests/capabilities/test_characterization.py`, на подставных источниках из существующих тестов, каждый сценарий с `use_llm` True и False (LLM и Jev — фейки):
   1. spec-driven с точной записью;
   2. composition с веществом, извлечение удалось;
   3. composition без LLM (`ingredients_unchecked`, ось 7.5, вещество из названия 17.5);
   4. composition, `NoSubstanceMatch`;
   5. composition: защищённый по названию кандидат против фильтра релевантности;
   6. смартфон с Kimovil (specs и `benchmark_index`/`kimovil_index`);
   7. кроссовки с полом и размером в запросе;
   8. аудитория на OLX;
   9. аудитория у официального магазина (срез `_PER_STORE`);
   10. товар с двумя нарушениями (порядок причин отказа);
   11. путь «новых нет, только OLX» (`NoNewPick`);
   12. запрос с концентрацией («0,1%») при нужном веществе.
   Фиксируется: порядок `ranked` (равные баллы — по id в самом тесте), `total_score` и `confidence` (до 0.01), роли, `rejected_reasons`, плюсы и минусы лучшего **по порядку**, `explanation` осей, `used_listings`, `ingredients_unchecked`, `knowledge_missing`. Ожидаемые значения не меняются до конца фазы.
2. ✅ **Слияние при сохранении и `generated_by`.** Тесты: сохранение синонимов не стирает `parent` и `capabilities`; `generated_by` выученной записи сохраняется.
3. ✅ **Профиль и адаптер.** `knowledge/profile.py`. Тесты: старая composition-запись → возможность composition; двойная запись старых ключей; поле у родителя; владельцы полей.
4. ✅ **Один поиск профиля.** `resolve_profile`, без подстроки. Тесты: «чай» не находит «чайник»; похожая через Jev → владельцы полей; книги и возможности из одного профиля. Обновить тесты подстроки в `test_store.py`.
5. ✅ **Синонимы у владельца книги моделей.** Тест: запуск по наследующей категории → синоним в книге родителя, книга брендов родителя не тронута, новой записи под запросом нет.
6. ✅ **Контракт и веса.** `capabilities/base.py`, `weights.py`. Тест: composition → прежние `COMPOSITION_WEIGHTS`; две возможности со сдвигами → сумма 100. После этого шага сигнатуры контракта не меняются до конца фазы.
7. ✅ **composition → возможность.** Поиск по веществам, защищённые, свойства без концентрации, извлечение и проверка, отказ по составу, ось, минусы, флаг. Характеризационные тесты зелёные.
8. ✅ **benchmark → возможность**, `kimovil_index` → `benchmark_index`. Характеризационные тесты зелёные.
9. ✅ **audience и size → возможности.** Предикат аудитории для основного пула, OLX и официальных магазинов. Характеризационные тесты зелёные.
10. ✅ **Чистка ядра.** Проверка из «Готово, когда».
11. ✅ **Лечение старых записей** (решение 8). Тесты: копия под чужой категорией лечится и больше не проверяется; настоящая spec-driven запись с синонимами не лечится.
12. ✅ **Документы.** Карта (EN + RU), планы, CHANGELOG.
13. **Живой прогон** до и после: «ирригатор до 40000», «крем с адапаленом», «мужские кроссовки 42 до 50000». Сравнить топ-5 и баллы.

## Готово, когда

- [x] Характеризационные тесты шага 1 (12 сценариев × 2) зелёные без правки ожидаемых значений (`tests/capabilities/characterization.json` не менялся с коммита шага 1).
- [x] `QT_QPA_PLATFORM=offscreen uv run pytest -q` зелёный (1069 passed, 1 skipped); покрытие ≥ 90% (92.1%); `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src` чистые.
- [x] `grep -nE "\bis_smartphone_category\b|COMPOSITION_WEIGHTS|ingredient_ctx|kimovil_index|COMPOSITION_DRIVEN|required_substances|NoSubstanceMatch\(" src/buysmarter/services/recommend.py src/buysmarter/ranking/engine.py src/buysmarter/ranking/filters.py` — пусто (реэкспорт `NoSubstanceMatch` — строка импорта, не вызов).
- [x] `grep -rn "capabilities" src/buysmarter/ranking` — пусто.
- [x] `services/recommend.py` и `ranking/engine.py` короче 716 и 749 строк (579 и 708); ни один файл не больше 800 (самый большой — `services/collect.py`, 737; `services/matching.py` 732).
- [ ] Живой прогон: топ-5 и баллы по трём запросам совпадают (допуск 0.01; различия только из-за выдачи площадок — с объяснением).
- [x] Похожая категория не находится подстрокой; синонимы OLX пишутся в книгу владельца; сохранение не теряет ключи.
- [x] Карта и зеркало описывают возможности; `tests/test_logic_map.py` зелёный.

## Отличия реализации (2026-10-22)

- **`Capability` — базовый класс с нейтральными хуками, а не Protocol.** Необязательные хуки остаются типизированными;
  ядро вызывает только переопределённые (`base.overrides`). Атрибут имени оси — `axis_name`, хук — `axis`.
- **В контракт добавлен `setting(profile, constraints)`**: так каждая возможность сама решает, включена ли она
  (решение 14). Запросные возможности возвращают `set_by="fallback"`. `clarify` возвращает `str | None` (вопрос — Ф2).
- **`CapabilityContext.hard_filters`.** Проверке `NoSubstanceMatch` нужен полный набор жёстких фильтров ядра (с
  аудиторией), иначе пул «пустеет из-за вещества» иначе, чем раньше. Ядро кладёт его в контекст.
- **Хуки `query` идут до поиска профиля** (ключ профиля — категория, из которой size убирает размер), в контексте
  пустой профиль.
- **`REGISTRY` в `capabilities/__init__.py`, вызов хуков — в новом `capabilities/flow.py`** (`activate`, `read_query`,
  `ActiveCapabilities`), а не в `base.py`: иначе цикл импортов. Порядок реестра: audience, size, benchmark,
  composition — он повторяет сегодняшний порядок минусов и обогащения.
- **`searches` отдаёт `(запрос, иголки для названия)`**, как их уже потреблял `collect.py`; `substance_plan` там
  переименован в `extra_searches`.
- **`Candidate.benchmark_label`** («Kimovil»): характеризация фиксирует пояснение оси «Kimovil 85/100», а ядро не
  должно знать источник.
- **`RankHooks` и `PoolScoring` лежат в `ranking/models.py`**; `IngredientContext` и `EquivalenceGroup` переехали
  в `capabilities/composition.py`.
- **Сценарий 3 характеризации** («composition без LLM») с `use_llm=True` — это Jev извлечения и запасной LLM
  недоступны; вариант без LLM у каждого сценария и так есть. Ожидаемые значения сняты с кода до правок (коммит шага 1).
- **Лечение (решение 8)**: кандидат ещё и без ключа `capabilities` и без `parent`. Проверка без LLM, не нашедшая
  синонима, `heal_checked` не ставит — полной проверки не было. У вылеченной записи удаляются обе книги, старый
  `category_type` остаётся: старая версия приложения прочтёт её как spec-driven и пойдёт искать похожую категорию.
- **`learn --file`**: если в заметках нет веществ и у собственной записи нет composition, тип не пишется вовсе —
  иначе ключ `capabilities` оборвал бы наследование от родителя. Повторное обучение не трогает composition,
  включённую или выключенную пользователем (ревью, приоритет из ADR-0005).
- **Сигнатуры**: `canonicalize_used_titles(category, owner, ...)` — вторым параметром slug владельца вместо книги
  брендов (она нужна была только для сохранения); `used_market`/`used_market_without_pick` получили `model_owner`;
  `official_candidates(book, constraints, reject)` и `_filter_used(..., reject)` — предикат параметром (по умолчанию
  пропускает всё; `tests/services/test_recommend.py` не трогали, его правит параллельная сессия). Проверка аудитории
  в `verify_store_floors` (обучение) осталась своей.
- **`recommend()` разбит на этапы** (`_constraints`, `_collect`, `_rank`, `_used_listings`) над dataclass'ами
  `_Run` и `_Pool` — по ревью.
- Удалены мёртвые `store.get_brand_book`/`get_model_book`. Тесты оси и фильтра состава переехали в
  `tests/capabilities/test_composition_axis.py` и `test_composition_filter.py`; общие фейки — `tests/fakes.py`.
- Шаг 13 (живой прогон) не делался — за владельцем.

## Вне этого шага

- Разбор запроса: intent, restricted, `product_type`, `search_phrase`, бюджет, уточнение, перенос длинных slug в синонимы — Ф2.
- Классификатор Jev, автообучение, переключатели в GUI, benchmark только с провайдером — Ф3.
- fit, dims, exclude_substances, strict original (counterfeit как возможность с параметром строгости), «только б/у» (предупреждение и раздел OLX наверху), хук `sources` и перенос used market и official stores — Ф4.
- Цепочки родителей длиннее одной ступени.

## Риски

- **Характеризационные тесты хрупкие к порядку при равных баллах.** Сортировать равные по id в самих тестах, ядро не менять.
- **Параллельная сессия правит `recommend.py` (OLX-фолбэк, `:479-496`) и `matching.py`.** Делать Ф1 в worktree, ребейзить на main перед шагами 5, 7, 9 и держать правки в `recommend()` локальными. Ф1 не трогает used market, поэтому основная зона конфликта — `_persist_learned_aliases`.
- **Лечение заденет запись, книга брендов которой случайно совпала с книгой похожей composition-категории.** Совпадение целой книги брендов маловероятно; каждое лечение в лог, проверить логи первых прогонов.
- **Без LLM похожие категории больше не находятся** (подстроки нет). Принять: подстрока чаще ошибалась; в отчёте есть `knowledge_missing`.
- **Двойная запись старых ключей забудется после v1.** Записать в CHANGELOG и в ADR, когда её снимать (после Ф3).
