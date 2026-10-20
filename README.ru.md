**[English](README.md)** | **Русский**

<p align="center">
  <img src="./assets/readme/hero.ru.svg" width="100%" alt="buy-smarter: опишите, что нужно, и получите предложения с Kaspi, Ozon и OLX под ваш бюджет и город. Лучший вариант из реального запуска набрал 66 из 100, с полосой по каждой оси: ingredient 25/25, brand 1.5/10, price 11/25, reviews 3.4/12, specs 15/15, popularity 0.3/3, availability 10/10.">
</p>

[![CI](https://github.com/octrow/buy-smarter/actions/workflows/ci.yml/badge.svg)](https://github.com/octrow/buy-smarter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

Помощник по покупкам для Казахстана, который объясняет свой выбор. Вы описываете, что нужно, обычными словами
(«портативный ирригатор, IPX7, до 40 000 тг, Алматы»). buy-smarter ищет на **Kaspi.kz**, **Ozon.kz** и
**OLX.kz** (б/у) и ранжирует предложения под ваш бюджет, город и обязательные функции. Для каждого варианта
видно, по каким осям он набрал баллы и почему. Работает как CLI, как MCP-сервер для Claude и как HTTP API
с очередью задач.

Он не ищет «лучший товар вообще». Он ищет лучший для вас, сегодня, в ваших рамках, и прямо говорит, что
проверить не удалось.

## Как выглядит отчёт

Фрагмент реального запуска (сохранённый отчёт, запрос: крем с адапаленом 0,1% до 10 000 тг, Алматы):

```text
🥇 ELEGANT COSMED гель ЭЛЕКЛИН-А для лица, 15 г   [kaspi]
Скор 66/100 · Уверенность 70% · 7 700 ₸

| Ось          | Балл    | Почему                                                   |
| ingredient   | 25/25   | действующее вещество присутствует (0,1%)                 |
| brand        | 1.5/10  | неизвестный бренд, нет данных о доверии                  |
| price        | 11/25   | 7 700 при медиане 5 238 (отношение 1.47)                 |
| reviews      | 3.4/12  | 4.9/5 по 31 отзыву; доверие к отзывам 0.5: 96% пятёрок   |
| availability | 10/10   | в наличии                                                |

+ аналог по действующему веществу: Kormesic Adapalene гель, 50 г — в 2.6 раз дешевле
- неизвестный бренд · слабые отзывы · слабая популярность
```

Дальше в отчёте: краткая выжимка отзывов, альтернативы (лучшее соотношение, самый дешёвый), топ-N со ссылками
и раздел «Б/у на OLX».

## Почему не спросить чат-бота или не отсортировать по рейтингу?

- **Казахстанские площадки.** Универсальные ассистенты (ChatGPT shopping, Perplexity, Google AI Mode) почти
  не видят Kaspi и OLX.kz. buy-smarter читает их JSON-эндпоинты напрямую, и падение одного источника не
  ломает запрос.
- **Рейтинги накручивают.** Доверие к отзывам учитывает долю пятёрок по всему распределению, всплески отзывов
  в одни и те же даты, мусорные тексты («ещё не пользовался», «курьер быстрый»), непроверенные покупки и
  жалобы на брак. Ось отзывов соответственно дисконтируется.
- **Требования проверяются, а не предполагаются.** Если спеки не подтверждают обязательную функцию, она
  проверяется по отзывам. Цитата должна дословно найтись в отзыве и встретиться минимум дважды, иначе вердикт
  остаётся «мало данных».
- **Знания о брендах и моделях.** Тиры брендов и алиасы моделей заложены для ирригаторов. Любая другая
  категория получает их через `learn-category`, поэтому «me 705» с OLX превращается в «Xiaomi Water Flosser 2».
  Без этого каждый бренд считается неизвестным: перед запросами про телефоны один раз запустите
  `learn-category "смартфон"`. Знания пишутся в локальное хранилище.
- **Характеристики смартфонов из Kimovil.** Для запросов про смартфоны каждая модель ищется на Kimovil: её общий
  индекс (0–100) идёт в ось specs («Kimovil 83/100»), а частота экрана, аккумулятор, быстрая зарядка, процессор,
  AnTuTu и защита IP добавляются в формулировках Kaspi, так что обязательная фича «120 Гц» или «5000 мАч» находится.
  Результаты кэшируются в `~/.cache/buysmarter/kimovil.db` (30 дней, промахи 7 дней).
- **История цен.** `refresh` и `history` пишут снапшоты цены и числа отзывов по каждому товару. Публичного
  аналога Keepa для Kaspi нет.

Ближайшие open-source проекты и что из них взято: [docs/analogs](docs/analogs/README.md).

## Как это работает

За всеми тремя входами один и тот же пайплайн. Подбор идёт десятки секунд, поэтому HTTP API кладёт его в Redis
Stream, выполняет воркер, а клиент опрашивает результат.

```mermaid
flowchart TD
    client([client]) -->|"POST /recommend, GET /recommend/{id}"| api[FastAPI]
    api -->|"job, dedupe, rate limit"| redis[(Redis)]
    redis -->|"stream, ack after the run"| worker[taskiq worker]
    worker -->|result| redis
    worker -->|"search, offers, reviews"| markets[Kaspi · Ozon · OLX]
    worker -->|price history| pg[(Postgres)]
    api -.->|metrics, traces| obs[Prometheus · Grafana · Jaeger]
    worker -.-> obs
```

<p align="center">
  <img src="./assets/readme/api-demo.gif" width="100%" alt="Терминал: POST /recommend на портативный ирригатор IPX7 до 38 000 тенге возвращает job id со статусом queued. Опрос задачи позже показывает статус done, лучший вариант Xiaomi Mijia Electric Flusher 2 MEO705 за 15 999 тенге с Kaspi с оценкой 65.78 и начало markdown-отчёта.">
</p>

`make observe` добавляет к compose-стеку Prometheus, Grafana и Jaeger. Скриншоты ниже сняты после девяти настоящих
задач: дашборд с панелями HTTP, площадок, ранжирования и очереди и одна задача одним трейсом, от `POST /recommend`
через воркер до запросов в Postgres и HTTP-запросов к площадкам.

<p align="center">
  <img src="./assets/readme/grafana.png" width="100%" alt="Дашборд Grafana buy-smarter за последние 30 минут: запросы по маршрутам, p95 задержки, поиски по площадкам Kaspi и OLX по исходу, вызовы LLM (в этом прогоне их нет), p95 длительности подбора около 45 секунд, около 12 кандидатов в рейтинге и ряд Queue с завершёнными задачами и p95 ожидания в очереди около 5 секунд.">
</p>
<p align="center">
  <img src="./assets/readme/jaeger.png" width="100%" alt="Трейс Jaeger buysmarter: POST /recommend, 43,8 секунды, 426 спанов. Спан POST (202) ведёт к спану отправки taskiq и спану выполнения в воркере, внутри которого спаны подключения и SELECT к Postgres, а за ними ряд HTTP GET 200 по 350–730 миллисекунд.">
</p>

Каждый PR гоняет тесты, собирает образ (не root, healthcheck), поднимает compose-стек и ставит, тестирует и
обновляет Helm-чарт в kind с одной задачей от начала до конца. Подробнее — в разделах [HTTP API](#http-api),
[Kubernetes](#kubernetes) и [Наблюдаемость](#наблюдаемость).

## Быстрый старт

Для Kaspi нужен **казахстанский IP**: датацентровым IP он отвечает HTTP 429 (можно задать `KASPI_PROXY` с
резидентным прокси).

macOS / Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/octrow/buy-smarter/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/octrow/buy-smarter/main/install.ps1 | iex"
```

Установщик ставит [uv](https://docs.astral.sh/uv/) (он сам принесёт Python), последнюю версию из релиза,
предлагает браузер для Ozon и запускает `buysmarter doctor`: он скажет, чего не хватает и что от этого
отключится. Повторный запуск обновляет, `sh -s -- --uninstall` удаляет. Хотите сначала прочитать? Скачайте
`install.sh`, прочтите и запустите `sh install.sh`. API-ключи и прокси кладутся в `~/.config/buysmarter/.env`
(образец в [.env.example](.env.example)).

Из исходников, для разработки ([uv](https://docs.astral.sh/uv/); Docker нужен только для серверного стека):

```bash
git clone https://github.com/octrow/buy-smarter && cd buy-smarter
uv sync
uv run buysmarter doctor      # скажет, чего не хватает и что от этого отключится

# По желанию
uv sync --extra server && make up        # стек HTTP API: Postgres :5434 + Redis :6380 (см. .env.example)
uv run playwright install chromium       # источник Ozon
uv run buysmarter ozon-setup             # один раз пройти антибот Ozon в видимом браузере
cp .env.example .env                     # необязательные API-ключи и прокси
```

История цен, предпочтения и выученные категории лежат в **локальном хранилище** — файле SQLite в папке данных
пользователя (`~/.local/share/buysmarter/buysmarter.db` в Linux), он создаётся при первом запуске. Где он, показывает
`doctor`. Остальное по желанию: без `claude` CLI режим `--no-llm` использует простой парсер. В клоне перед командами ниже добавляйте `uv run`.

## Использование

```bash
# LLM-парсинг запроса через библиотеку claude-cli (API-ключ не нужен)
buysmarter recommend "нужен ирригатор, портативный, IPX7, до 40000 тг, Алматы"

# Все три площадки (по умолчанию) или выборочно; больше кандидатов; без md-отчёта
buysmarter recommend "ирригатор до 40000 тг" --sources kaspi,olx --top 10 --no-save

# Только проверенные бренды; без LLM (простой парсер, фичи не фильтруются)
buysmarter recommend "ирригатор" --city Алматы --budget 40000 --trusted-only --no-llm

# Знания о категории: сгенерировать (opus) или извлечь из своих заметок (sonnet)
buysmarter learn-category "робот-пылесос"
buysmarter learn-category "смартфон"      # тиры брендов и алиасы моделей для телефонов
buysmarter learn ~/notes/robot-vacuum.md --category "робот пылесос"

# Смартфоны: индекс Kimovil в оси specs, «120 Гц» / «5000 мАч» как обязательные фичи
buysmarter recommend "смартфон до 250000 тг, 120 Гц, 5000 мАч" --city Алматы

# История цен: обойти отслеживаемые категории, затем посмотреть один товар
buysmarter refresh
buysmarter history "F300"
```

Вывод: лучший вариант со скором из 100, уверенность, раскладка по осям (бренд, цена с трендом за 30 дней,
отзывы, спеки, популярность, наличие с датой доставки), выжимка отзывов (плюсы, минусы, частые жалобы,
проверка требований), альтернативы, топ-N со ссылками и раздел б/у с OLX. Отчёты сохраняются в
`~/.local/share/buysmarter/reports/` (`--no-save` отключает), логи — в `~/.cache/buysmarter/logs/buysmarter.log`
(`BUYSMARTER_LOG_FILE` меняет путь). В папку, из которой вы запускаете, ничего не пишется.

Источники: **Kaspi** (JSON-эндпоинты), **Ozon.kz** (браузерная сессия Playwright), **OLX.kz** (открытый REST,
б/у). Аксессуары и нерелевантные объявления отсекаются эвристическим фильтром с LLM как запасным вариантом.
В разделе б/у дефекты, которые продавец сам называет в описании (трещины, «не включается», залит, слабый
аккумулятор, был в ремонте), выводятся в колонке Con, а объявления «на запчасти» отбрасываются.
Ранжирование также дисконтирует отзывы неизвестных брендов и штрафует подозрительно низкую цену.

## LLM: модели и кэш

Все LLM-вызовы идут через [claude-cli](https://github.com/octrow/claude-cli): через шлюз OmniRoute, если он
настроен, иначе через локальный `claude` CLI. У каждой задачи своя модель: дешёвая для парсинга запроса,
выжимки отзывов и запасных классификаций; sonnet для извлечения знаний из файлов; opus для генерации знаний
о категории. Каждая пара запрос/ответ кэшируется, повторный вызов бесплатный:

```bash
sqlite3 ~/.cache/buysmarter/llm_cache.db \
  "SELECT model, substr(cache_key,1,8), cost_usd, created_at FROM llm_cache ORDER BY created_at DESC LIMIT 10;"
grep "llm cache" ~/.cache/buysmarter/logs/buysmarter.log   # hit/miss каждого вызова
```

## MCP-сервер

buy-smarter работает и как MCP-сервер (fastmcp, stdio). Подключённый Claude ведёт разговор и передаёт
структурированные аргументы (город, бюджет). Инструменты — тонкие обёртки над теми же слоями service и
render, что и CLI.

```bash
uv run python -m buysmarter.mcp_server   # блокируется на stdio; обычно его запускает клиент
```

**Claude Code** подхватит `.mcp.json` из корня репозитория. Для **Claude Desktop** добавьте в
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "buy-smarter": {
      "command": "uv",
      "args": ["run", "python", "-m", "buysmarter.mcp_server"],
      "cwd": "/path/to/buy-smarter"
    }
  }
}
```

| Инструмент | Назначение |
|---|---|
| `recommend` | Полный пайплайн → markdown-отчёт (лучший выбор с осями, альтернативы, топ-N, б/у на OLX). Параметры: `query`, `city`, `budget`, `sources`, `trusted_only`, `top`, `parse_with_llm` (по умолчанию `False`: Claude сам разбирает запрос). |
| `search_products` | Сырой поиск по одной площадке без ранжирования (title, price, rating, url). |
| `compare` | 2–4 товара через запятую → сравнение спецификаций (Kaspi). |
| `get_preferences` / `set_preference` | Чтение и запись предпочтений (город, бюджет…). Хранятся в локальном хранилище. |

## HTTP API

API ставит подбор в очередь, а выполняет его воркер (taskiq поверх Redis Streams): один подбор идёт десятки секунд.
OpenAPI — на `/docs`.

```bash
curl -s -X POST localhost:8000/recommend -H 'content-type: application/json' \
  -d '{"query": "ирригатор до 40000", "sources": ["kaspi"]}'
# 202 {"job_id": "…", "status": "queued", "status_url": "/recommend/…"}
curl -s localhost:8000/recommend/<job_id>
# {"status": "done", "result": {"report_markdown": "…", "best": {…}, "alternatives": {…}, …}}
curl -s 'localhost:8000/history?q=wp-100'
```

- Статусы: `queued` → `running` → `done`, `no_match` (нужного вещества нет ни в одном товаре или фильтры никого не
  пропустили) или `failed` (`timeout`, `internal`). Задачи хранятся 24 часа.
- Тот же запрос в течение 15 минут возвращает ту же задачу (200). Клиент может запустить 10 новых задач за 10 минут,
  дальше 429 с `Retry-After` (`BUYSMARTER_RATE_LIMIT_JOBS`, `BUYSMARTER_RATE_LIMIT_WINDOW_S`).
- `BUYSMARTER_API_KEYS=k1,k2` требует заголовок `X-API-Key`. `BUYSMARTER_SOURCES` — какие площадки принимает API:
  по умолчанию `kaspi,olx`, `ozon` требует воркер с Chromium.
- Воркер, упавший посреди подбора, ничего не теряет: его задачу заберёт другой воркер через два таймаута задачи
  (`BUYSMARTER_JOB_TIMEOUT_S`, по умолчанию 300 с).
- С облачных IP площадки часто отказывают (Kaspi отвечает 429 адресам датацентров, см. `KASPI_PROXY`), поэтому там
  большинство задач закончится `no_match` или `failed`.

## Docker

```bash
docker compose up -d --build        # Postgres, Redis, миграции, API на :8000, воркер; ключи берутся из .env
docker compose run --rm app buysmarter recommend "…" --budget 30000
docker compose -f docker-compose.yml -f docker-compose.browser.yml up -d --build   # воркер с Chromium: Ozon
docker compose down                 # с -v удалит и данные
```

Готовые образы: `ghcr.io/octrow/buy-smarter:latest` (релизы), `:main` (каждый коммит в main), суффикс `-browser`
для Chromium. `TAG=main docker compose pull && docker compose up -d` запускает их без сборки.
Происхождение образа: `gh attestation verify oci://ghcr.io/octrow/buy-smarter:latest -R octrow/buy-smarter`.

В образе нет `claude` CLI: LLM работает через API-фолбэк (`DEFAULT_LLM_MODEL` и ключ провайдера в `.env`).
Без compose: `docker build -t buysmarter .` (`--target browser` для Chromium), затем `alembic upgrade head` из того же
образа перед запуском API.

## Kubernetes

Helm-чарт лежит в [`deploy/helm/buysmarter`](deploy/helm/buysmarter): API, воркер (`worker.browser=true` для Ozon) и
Job миграций. Postgres и Redis свои (managed или в кластере):

```bash
helm install buysmarter deploy/helm/buysmarter -n buysmarter --create-namespace \
  --set secret.data.DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/buysmarter \
  --set secret.data.REDIS_URL=redis://host:6379/0
helm test buysmarter -n buysmarter
```

Миграции идут Job'ом-хуком pre-install/pre-upgrade, один раз на релиз. Пробы: `/health` (liveness, без зависимостей)
и `/ready` (readiness, 503, пока не отвечает БД). Поды проходят Pod Security Standard `restricted`. Ключи — в
`secret.data` или в своём Secret (`secret.existing`). CI ставит и обновляет чарт в kind на каждом PR
и прогоняет одну задачу через API → Redis → воркер ([`deploy/kind/smoke.sh`](deploy/kind/smoke.sh)); проверяется
проводка, а не рейтинг: площадки могут не отвечать трафику из CI.

## Наблюдаемость

API отдаёт метрики Prometheus на `GET /metrics`, воркер — на `:9000/metrics` (OpenTelemetry SDK с Prometheus-экспортером): частота и задержка
HTTP-запросов по маршрутам, а также `buysmarter_recommend_duration_seconds` (по исходу),
`buysmarter_source_requests_total` (по площадке и исходу), `buysmarter_llm_calls_total` (по модели, попадание в кэш или
промах) и `buysmarter_recommend_offers_ranked`, а также очередь: `buysmarter_jobs_total` (по статусу),
`buysmarter_job_wait_seconds` и `buysmarter_rate_limited_total`. Один трейс ведёт задачу от `POST /recommend` через воркер. С `OTEL_EXPORTER_OTLP_ENDPOINT` уходят ещё и трейсы (FastAPI, httpx-запросы
к площадкам, SQL) по OTLP/HTTP; `BUYSMARTER_METRICS=0` выключает `/metrics`.

```bash
make observe   # compose-стек + Prometheus :9090, Grafana :3000 (дашборд «buy-smarter»), Jaeger :16686
```

В чарте — аннотации `prometheus.io/scrape`, опциональный `ServiceMonitor` (`metrics.serviceMonitor.enabled`) и
`otel.endpoint` для трейсов.

## Разработка

```bash
make check   # ruff + pytest (не нужны ни сеть, ни сервер БД)
make test
make up / make down
```

Структура: `src/buysmarter/{normalize,db,llm,ranking,services,cli,knowledge,mcp_server,api}`, а также `jobs.py`
(задачи в Redis), `worker.py` (воркер taskiq) и `telemetry.py`; в `deploy/` — Helm-чарт, kind-smoke и конфиги
наблюдаемости. Адаптеры площадок
вынесены в отдельную библиотеку [market-sources](https://github.com/octrow/market-sources)
(`kaspi`/`olx`/`ozon` поверх вежливого HTTP-клиента и сессии Playwright). Тесты парсеров идут на сохранённых
JSON-сэмплах в `tests/samples/`. Заметки по разведке источников — в [docs/spikes](docs/spikes).

## Ограничения

- Kaspi нужен казахстанский резидентный IP или `KASPI_PROXY`.
- Ozon всё равно может показать антибот-страницу; `ozon-setup` обновляет сохранённую сессию.
- Скорость прироста отзывов показывается, но в скор не входит, пока снапшоты не собираются регулярно.
- Kimovil: после всплеска запросов Cloudflare может на время поставить IP под челлендж; пока блок не снят,
  используются только закэшированные характеристики телефонов. Страница Kimovil описывает базовую комплектацию
  модели, поэтому RAM и память берутся из объявления, а заголовок без «5G» может попасть на 4G-версию.
- Эндпоинты площадок неофициальные и могут поменяться без предупреждения.

## Лицензия

[MIT](LICENSE). Сторонний код перечислен в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
