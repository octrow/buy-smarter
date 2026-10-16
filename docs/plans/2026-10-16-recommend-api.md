# План: HTTP API подбора — `POST /recommend` (асинхронно, очередь в Redis) ✅ (2026-10-16, v0.2.0)

Продолжение `2026-10-16-deploy.md`: закрывает оба оставшихся риска оттуда. Во-первых, в API только пробы и `/metrics`, поэтому
доменные метрики пустые, а Helm-деплой показывает сервис без бизнес-логики. Во-вторых, Redis заявлен в стеке, но код
его не использует. Здесь Redis получает три реальные задачи: очередь, состояние задач и дедупликацию с rate limit.

## Что есть сейчас (2026-10-16)

- `services.recommend.recommend(query, *, city, budget, use_llm, trusted_only, sources, …) -> RecommendResult` — весь
  пайплайн; идёт **десятки секунд** (поиск по площадкам, отзывы, опционально LLM). `RecommendResult` — Pydantic-модель.
- Исходы: успех; `NoSubstanceMatch` (нормальный ответ «нужного вещества нет ни в одном товаре», CLI/MCP рендерят его
  отдельно); `ValueError("no candidates passed hard filters")` — пустой пул после фильтров; прочие исключения.
- `cli/report.py`: `render_markdown(result, top)` и `render_no_substance_markdown(exc)` — тот же отчёт, что видят CLI и MCP.
- `services.recommend.product_history(term) -> list[ProductHistory]` — история цен, `[]` без Postgres.
- MCP-тул `recommend` валидирует `sources` по `_ALL_SOURCES` и подставляет город из предпочтений — та же логика нужна API.
- В контейнере нет `claude` CLI и (в `runtime`) Chromium: без ключа провайдера только `use_llm=False`, Ozon — только
  в образе `browser`.
- Метрики `buysmarter_recommend_*`, `buysmarter_source_requests_total`, `buysmarter_llm_calls_total` пишутся там, где
  выполняется подбор. Сейчас это CLI и MCP, где экспорта нет.
- Runtime-образ 346 МБ при цели ≤350.

## API

```
POST /recommend            → 202 {"job_id", "status": "queued", "status_url"}   + Location: /recommend/{id}
GET  /recommend/{job_id}   → 200 {"job_id", "status", "created_at", "started_at", "finished_at", "result" | "error"}
GET  /history?q=…          → 200 [ProductHistory]
```

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| 1 | Синхронно или нет | Асинхронно: 202 + опрос `GET /recommend/{id}` | Подбор идёт десятки секунд; держать HTTP-соединение столько — таймауты ingress и балансировщиков, и API-под занят чужой работой |
| 2 | Тело запроса | `RecommendRequest`: `query` (1–500 символов), `city`, `budget` (>0), `sources` (enum `kaspi`/`ozon`/`olx`), `trusted_only`, `use_llm` (по умолчанию `false`), `top` (1–20) | Те же параметры, что у MCP-тула. `use_llm=false` по умолчанию: в контейнере LLM работает только по ключу |
| 3 | Доступные площадки | `BUYSMARTER_SOURCES` (по умолчанию `kaspi,olx`; в образе `browser` — с `ozon`); запрос с выключенной площадкой → 422 | Ozon без Chromium гарантированно падает: лучше честный 422, чем тихий пропуск |
| 4 | Статусы | `queued` → `running` → `done` \| `no_match` \| `failed` | `no_match` (`NoSubstanceMatch`) — нормальный ответ, а не ошибка. `ValueError` пустого пула — тоже `no_match`, с причиной |
| 5 | Результат | `{"report_markdown", "best", "alternatives", "constraints", "used_listings"}` — отдельная публичная схема `RecommendOutcome`, собранная из `RecommendResult` | Отдавать `RecommendResult` как есть — значит сделать внутреннюю модель публичным контрактом; маленький маппинг развязывает их. Markdown — тот же отчёт, что в CLI/MCP |
| 6 | Ошибки | `failed` + `error` с коротким кодом (`timeout`, `internal`) без трейсбека; детали — в лог с `job_id` | Не светить внутренности наружу (правило core.md) |
| 7 | Город | Пусто → предпочтение `city` → «Алматы», как в MCP | Одна логика для всех входов — вынести из MCP в `services` и переиспользовать |
| 8 | История | `GET /history?q=` (`q` ≥ 2 символов), обёртка над `product_history` | Готовый сервис, второй читающий эндпоинт почти бесплатно |
| 9 | Аутентификация | Если задан `BUYSMARTER_API_KEYS` (через запятую), нужен заголовок `X-API-Key`, иначе 401. Не задан — открыто (локально, kind) | API запускает скрейпинг чужих площадок: открытый публичный эндпоинт — прямой путь к бану IP. Полноценный OAuth — вне плана |

## Очередь: чем выполнять задачи

| Вариант | Плюсы | Минусы |
|---|---|---|
| FastAPI `BackgroundTasks` | ноль зависимостей | Задача живёт в API-поде: рестарт или раскатка теряет её, масштабировать воркеры отдельно нельзя, тяжёлый подбор конкурирует с запросами |
| Своя очередь на `redis-py` (списки или streams) | зависимость уже есть | Ack, повторная доставка после падения воркера, таймауты, graceful shutdown — всё писать и тестировать самим |
| arq | асинхронный, на Redis, простой | **В maintenance-only режиме** (README, issue #510) — плохой выбор для нового кода |
| **taskiq + taskiq-redis** (`RedisStreamBroker`) | Асинхронный, живой (taskiq 0.13.0 — 2026-09-26, taskiq-redis 1.2.4 — 2026-09-28); Redis Streams с consumer group и ack: задача упавшего воркера перезабирается через `XAUTOCLAIM` после `idle_timeout`; есть `InMemoryBroker` для тестов | Две новые зависимости; брокер из 0.x-ветки — версии закрепить в `uv.lock` |

**Рекомендация: taskiq + taskiq-redis.** Надёжность (ack и повторная доставка) достаётся готовой, а это самая
дорогая часть своей очереди. Параметры брокера сверены с документацией taskiq-redis в Context7.

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| Q1 | Брокер | `RedisStreamBroker(url=REDIS_URL, queue_name="buysmarter:recommend", idle_timeout=…, unacknowledged_lock_timeout=…)` в `buysmarter/worker.py` | Streams + ack вместо списков: задача не теряется, если воркер умер посреди подбора |
| Q2 | Состояние задачи | Свой хеш `buysmarter:job:{id}` в Redis (статус, времена, результат JSON) с TTL 24 ч; result backend taskiq **не** используем | taskiq хранит только итог, а нужны `queued`/`running` и время ожидания; один источник правды проще двух |
| Q3 | Таймаут | `timeout` задачи 300 с → `failed: timeout`; `idle_timeout` брокера 10 мин > таймаута | Подвисшая площадка не держит слот воркера вечно; перезабор не стартует раньше, чем истёк бы таймаут |
| Q4 | Повторы | Автоповторов нет. Перезабор после падения воркера — да, но задача сначала проверяет статус: `done`/`no_match`/`failed` → ack и выход | Повтор подбора — снова десятки запросов к площадкам; идемпотентность по статусу защищает от двойного выполнения |
| Q5 | Параллельность | Воркер `taskiq worker buysmarter.worker:broker --workers 1 --max-async-tasks 2` | Подбор и так параллелит площадки внутри; больше двух одновременно на под — риск 429 от Kaspi |
| Q6 | Процесс | Тот же образ, отдельная команда; в compose — сервис `worker`, в Helm — второй Deployment | API и воркер масштабируются независимо; воркеру не нужен Service |
| Q7 | Ozon | `worker.image.suffix: -browser` в values (по умолчанию пусто) | Chromium только там, где он нужен: в воркере, а не в API |

## Redis: дедупликация и rate limit

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| R1 | Одинаковые запросы | Ключ `buysmarter:dedupe:{sha256(нормализованный запрос)}` → `job_id`, `SET NX EX 900`; повторный POST в течение 15 минут возвращает тот же `job_id` (200 вместо 202) | Двойной клик или ретрай клиента не запускает второй скрейпинг; цены за 15 минут не меняются |
| R2 | Rate limit | Фиксированное окно: `INCR buysmarter:rl:{client}:{window}` + `EXPIRE`, по умолчанию 10 задач за 10 минут на клиента (API-ключ, иначе IP); превышение → 429 + `Retry-After` | Защищает площадки и наш IP от бана; фиксированное окно — 3 строки и честно помечается `ponytail:`-комментарием (скользящее окно, если понадобится) |
| R3 | Redis недоступен | `POST /recommend` → 503; `/ready` проверяет и Redis (`PING`) | Без Redis задачу некуда положить; под без Redis не должен получать трафик |

## Метрики и наблюдаемость

- Воркер поднимает `/metrics` на своём порту (`BUYSMARTER_WORKER_METRICS_PORT=9000`, `prometheus_client.start_http_server`)
  с тем же `telemetry`-провайдером: именно там появятся живые `buysmarter_recommend_*`, `source_requests`, `llm_calls`.
- Новые метрики: `buysmarter_jobs_total{status}` (итоги задач), `buysmarter_job_wait_seconds` (от `queued` до
  `running`), `buysmarter_rate_limited_total`.
- Трейсы: контекст из запроса `POST /recommend` передаётся в задачу (traceparent в labels сообщения), чтобы в Jaeger был
  один трейс от HTTP до запросов к площадкам.
- Grafana: панели «очередь» (ожидание, итоги задач, 429) к существующим; Prometheus в compose скрейпит и `worker:9000`.
- Helm: аннотации и `ServiceMonitor` для воркера (порт `metrics`); headless Service не нужен — `PodMonitor`, если
  включён оператор.

## Тесты

- Схемы и эндпоинты: `TestClient` + `taskiq.InMemoryBroker` + `fakeredis` (новая dev-зависимость):
  202 и `Location`; 422 на выключенную площадку и пустой `query`; дедупликация возвращает тот же `job_id`; 429 после
  лимита; 404 на неизвестный `job_id`; 401 без ключа, когда `BUYSMARTER_API_KEYS` задан; 503 без Redis.
- Задача: с подменённым `recommend` — `done` с `report_markdown`; `NoSubstanceMatch` и пустой пул → `no_match`;
  исключение → `failed: internal` без трейсбека в ответе; таймаут → `failed: timeout`; повторная доставка
  завершённой задачи ничего не делает.
- Метрики: `buysmarter_jobs_total{status="done"}` растёт после задачи (тот же хелпер `_sample`, что в
  `tests/test_telemetry.py`).
- kind-smoke: `POST /recommend` → опрос до терминального статуса за 2 минуты. Из CI площадки могут не ответить (Kaspi
  режет IP датацентров), поэтому проверяется **проводка** (API → Redis → воркер → статус), а не содержимое: годится любой
  из `done`/`no_match`/`failed`, кроме `failed: internal`. Redis в kind — простой манифест рядом с Postgres.

## Порядок и коммиты

1. `refactor: shared request resolution for MCP and API` — город и проверка площадок из MCP в `services`.
2. `feat: job store and recommend task` — `worker.py` (брокер, задача, состояние в Redis), тесты задачи.
3. `feat: POST /recommend, GET /recommend/{id}, GET /history` — схемы, эндпоинты, `X-API-Key`, тесты API.
4. `feat: dedupe and rate limit on Redis` — R1–R3, `/ready` с Redis.
5. `feat: worker in compose and helm` — сервис `worker`, Deployment, values (`worker.*`), kind-smoke с Redis и задачей.
6. `feat: queue metrics, worker /metrics and dashboard panels`.
7. README (EN/RU: раздел «HTTP API» с примером `curl`), CHANGELOG, отметка в этом плане; релиз 0.2.0 (новая
   публичная поверхность — minor).

## Готово, когда

- [x] `curl -X POST :8000/recommend -d '{"query":"ирригатор до 40000"}'` → 202; опрос `status_url` даёт `done` с отчётом
  (локально, с доступом к Kaspi: `done` за ~48 с).
- [x] Повтор того же запроса за 15 минут возвращает тот же `job_id`; 11-я задача за окно — 429 с `Retry-After`.
- [x] Убитый посреди подбора воркер (`docker compose kill worker`) — задача перезабирается и завершается, без дубля
  (проверено с `BUYSMARTER_JOB_TIMEOUT_S=40`: `done` через 102 с, pending в стриме пуст).
- [x] OpenAPI (`/docs`) описывает все три эндпоинта со схемами и примерами.
- [x] В Grafana живые доменные панели и панели очереди; в Jaeger один трейс от `POST /recommend` до запросов к площадкам
  (362 спана: FastAPI → taskiq send/execute → 53 httpx-запроса → SQL).
- [x] kind-smoke: задача доходит до терминального статуса; CI зелёный.
- [x] Runtime-образ ≤ 365 МБ: 360 МБ; цель поднята с 350 — taskiq тянет обязательный aiohttp (~15 МБ).

## Отличия реализации

- **Город не переносился в `services`**: `recommend()` уже сам разрешает его (параметр > запрос > предпочтение >
  «Алматы»), API передаёт `city` как есть. Шаг 1 свёлся к общей `select_sources` для CLI, MCP и API.
- **Перезабор на пустой очереди.** taskiq-redis 1.2.4 делает `XAUTOCLAIM` только после непустого `XREADGROUP`, поэтому
  задача упавшего воркера ждала бы следующего сообщения. Воркер раз в половину `idle_timeout` шлёт пустую задачу
  `buysmarter.reclaim_nudge` (`ponytail:`-комментарий в `worker.py`); убрать, когда библиотека начнёт перезабирать
  и на пустом чтении. `idle_timeout` = 2 × `BUYSMARTER_JOB_TIMEOUT_S` (новая настройка, по умолчанию 300 с).
- **Ozon в compose** — override-файл `docker-compose.browser.yml` (воркер на образе с Chromium, API принимает ozon)
  вместо профиля `browser` с `app-browser`: API больше не выполняет подбор, Chromium ему не нужен.
- **Результат в Redis-хеше задачи**, без result backend taskiq (как и планировалось, Q2); ack — `when_executed`.
- Шаги 3 и 4 (эндпоинты и дедупликация с rate limit) — один коммит: общая поверхность и общие тесты.

## Вне плана

- Веб-интерфейс, вебхуки/SSE о готовности (опрос достаточен для API-клиентов), пользователи и OAuth.
- Перевод MCP-сервера на очередь: MCP вызывается локально по stdio, ему очередь не нужна.
- Хранение результатов в Postgres дольше 24 ч (сейчас истории цен хватает).

## Риски

1. **Скрейпинг из облака.** Kaspi отдаёт 429 на IP датацентров (`KASPI_PROXY` есть в настройках). В k8s без прокси
   большинство задач закончится `no_match` или `failed`. Это нужно написать в README и не обещать живой демо-URL
   (он и так отложен в `portfolio-priorities.md`).
2. **Размер образа.** taskiq и taskiq-redis небольшие, но запас всего 4 МБ. Если вылезем — поднять цель до 360 или
   убрать `.pyc` у тяжёлых пакетов; решение принять на шаге 2, не в конце.
3. **taskiq на 0.x.** Ломающие изменения между минорами возможны. Версии закреплены в `uv.lock`, а dependabot
   приносит обновления отдельными PR с CI.
4. **Рост scope.** Это самый большой из планов: семь коммитов. Если нужен быстрый результат, шаги 1–3 и 5 уже дают
   рабочий асинхронный API; дедупликацию, rate limit и метрики очереди (4, 6) можно отложить.
