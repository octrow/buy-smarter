# Playbook: от CLI-проекта до деплоя, API на очереди и витрины

Процедуры, отработанные на buy-smarter 2026-10-16 (v0.1.2 → v0.2.0, 31 коммит). Это сырьё для skill или шаблона:
у каждой процедуры есть вход, шаги, проверка «готово» и грабли, на которые мы уже наступили. Примеры ссылаются на
файлы этого репозитория — их можно брать как образец.

Порядок, в котором процедуры выполнялись: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Сквозные правила (раздел 0) действуют
на каждом шаге.

## 0. Сквозные правила

### Цикл одной фазы

1. **План** — раздел «В работе» в `docs/PLAN.md`, все планы в одном файле (формат ниже). Пользователь смотрит и говорит «реализуй».
2. **Тест первым** для нового поведения (RED), потом код (GREEN). Инфраструктура проверяется не юнит-тестом, а
   живым прогоном.
3. **Локальная проверка до CI**: собрать образ, поднять стек, прогнать реальную задачу, kind-кластер. В CI уходит
   только то, что уже прошло локально.
4. **Коммиты по шагам плана**, формат `<type>: <description>`, тело объясняет «почему». Push в main.
5. **Дождаться CI**: `gh run watch <id> --exit-status`, затем проверить логи ключевых шагов (не только зелёный значок).
6. **Документы**: README EN и RU одновременно, CHANGELOG, отметка в плане (✅ + раздел «Отличия реализации»).
7. **Отчёт пользователю**: что сделано, что проверено вживую (с числами), отличия от плана, что нужно знать.

### Формат плана

```markdown
# План: <тема>
Шаг N из цепочки … (ссылка на общий план). Этот документ — только про …

## Что есть сейчас (<дата>)        ← факты из кода, не предположения
## Решения                           ← таблица: # | Вопрос | Решение | Почему
## Файлы                             ← что создаётся/меняется
## Порядок работы                    ← нумерованные шаги = будущие коммиты
## Готово, когда                     ← чекбоксы с командой проверки и числом
## Вне этого шага                    ← явно отложенное
## Риски                             ← с рекомендацией по каждому
```

После реализации: заголовок с ✅ и датой, чекбоксы `[x]` с фактическими числами, раздел «Отличия реализации».
Если цель не достигнута (размер образа 335 > 300), не подгонять молча: сообщить, предложить варианты, менять
цель только по решению пользователя и записать причину в план.

### Зависимости и supply chain

- Экшены GitHub — по SHA с комментарием версии: `uses: org/action@<sha> # vX.Y.Z`. SHA брать так:
  `gh api repos/<org>/<action>/commits/<tag> -q .sha`.
- Базовые образы — по digest: `docker inspect --format '{{index .RepoDigests 0}}' <image>`.
- Версия не моложе двух недель, если нет причины. Перед выбором — история инцидентов: у `aquasecurity/trivy-action`
  в марте 2026 подменяли теги (видно по дублям релизов), поэтому Trivy запускается образом по digest.
- В публикующем workflow нет кэша (`type=gha`, `actions/cache`): отравленный кэш попал бы в артефакт.
- `zizmor --min-severity medium .github/workflows` после каждой правки workflow.
- Бинарники для локальной проверки (kind, kubeconform) — в scratchpad, с проверкой контрольной суммы, без установки
  в систему.
- Документацию библиотек сверять через Context7 до кода (OpenTelemetry, taskiq, taskiq-redis). Выбор библиотеки —
  с проверкой статуса: arq оказался в maintenance-only режиме (README, issue #510).

### Ревью после кода

- Хук CYBERNET REVIEW требует прогнать файлы через `code-conventions:clean-code` и `code-conventions:gof-patterns`.
  Читать конкретный reference-файл (functions.md, single-responsibility.md, code-smells.md, singleton.md), а не
  индекс; называть skill у каждой правки и прямо говорить «чисто», если чисто.
- Что реально находилось: функция > 20 строк со смешанными уровнями (вынести `_install_providers`), метод без общих
  данных с классом (rate limit из `JobStore` → функция), дублирование инструментации (`_instrument_clients`).

### Осознанные упрощения

- Комментарий `# ponytail: <предел>; <событие на реальном прогоне, после которого переделать>`.
- `/ponytail-debt` собирает ledger → `PONYTAIL-DEBT.md`. Пометка без триггера = `no-trigger`, такие гниют.
- Общий триггер для порогов: «retune after 50 real runs or at the first miss seen in one». У остальных — своё
  наблюдаемое событие (первый дубль товара на реальном прогоне, треть задач без бюджета и т. п.).

## 1. Dockerfile (multi-stage)

**Вход:** Python-проект на uv, зависимости из git, опциональный Playwright. Образец: `Dockerfile`.

**Шаги:**
1. `builder` (python-slim + git + uv из `ghcr.io/astral-sh/uv:<ver>@<digest>`):
   `uv sync --locked --no-dev --no-editable --no-install-project` с bind-mount `pyproject.toml`/`uv.lock` и
   cache-mount, затем `COPY src` и второй `uv sync`. `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`.
2. `runtime`: только `.venv` + `alembic.ini` + `alembic/`, пользователь uid 10001, `USER 10001:10001` (числом),
   `HEALTHCHECK` через `python -c urllib…` (в slim нет curl), `VOLUME` для кэшей, OCI-labels.
3. `browser` (`FROM runtime`): `playwright install --with-deps --only-shell chromium` под `USER 0`, обратно 10001.
4. `.dockerignore`: `.git`, `.venv`, `.env*` (кроме `.env.example`), `tests`, `docs`, `assets`, кэши.
5. Миграции — не в entrypoint, отдельной командой `alembic upgrade head` из того же образа.

**Готово:** размер (`docker image ls`), `id -u` → 10001, `healthy`, `alembic upgrade head` на чистой БД,
`<cli> --help`, `CACHED` у слоя зависимостей при правке `src/`, `hadolint` чистый, нет `.env`/`.git` в `/app`.

**Грабли:**
- Node-драйвер Playwright — 131 МБ и без Chromium бесполезен: в builder `mv …/playwright/driver /opt/…`, в runtime
  его нет, в browser — `COPY --from=builder` обратно. После этого CLI-проверка Chromium должна сначала смотреть,
  есть ли драйвер, иначе Playwright печатает трейсбек из своей future.
- `--only-shell` (headless shell) вместо полного Chromium: −400 МБ. Headed нужен только интерактивному логину, а в
  контейнере дисплея нет.
- `USER app` по имени ломает k8s `runAsNonRoot` (kubelet не может проверить) — только числовой uid.
- hadolint: DL3008 (pin apt) для `git` в builder подавлять явно `# hadolint ignore=DL3008`; `USER root` → `USER 0`.

## 2. Compose «всё в одном»

**Образец:** `docker-compose.yml`, `docker-compose.browser.yml`.

**Шаги:**
1. Якоря: `x-env: &env` (адреса сервисов внутри сети, `${VAR:-default}`) и `x-app: &app` (build target, `image:
   ghcr.io/…:${TAG:-latest}`, `env_file: [{path: .env, required: false}]`, `environment: *env`).
2. Сервисы: `postgres`, `redis` (порты на `127.0.0.1`), `migrate` (`command: alembic upgrade head`,
   `restart: "no"`), `app` (`depends_on: migrate: service_completed_successfully`), `worker`.
3. Варианты (Chromium) — override-файлом `-f docker-compose.browser.yml`, а не профилем-дублем сервиса.
4. Профиль `observability` для Prometheus/Grafana/Jaeger; Makefile-цель `make observe`.
5. `make up` оставить только для БД (разработка с хоста).

**Готово:** `docker compose up -d --build --wait` с нуля → все `healthy`, миграции идемпотентны, CI-smoke поднимает
стек `TAG=ci docker compose up -d --no-build --wait`.

**Грабли:**
- YAML-merge `<<:` поверхностный: свой `environment:` у сервиса заменяет весь словарь. Нужен `<<: *env` внутри.
- Переменная из шелла не попадает в контейнер, пока её нет в `environment` (так тест с `BUYSMARTER_JOB_TIMEOUT_S`
  молча шёл на дефолте).
- `HEALTHCHECK` образа бьёт в порт API; у воркера его надо переопределить (проверка его `/metrics`).
- Bind-mount одного файла держит inode: файл, переписанный редактором заново, контейнер не видит →
  `docker compose up -d --force-recreate <svc>`.
- Чужие порты на машине (3000 занят) → `${GRAFANA_PORT:-3000}`. Локальные проверки — под отдельным
  `COMPOSE_PROJECT_NAME`, чтобы не задеть dev-стек; в конце `down -v`.

## 3. Образ в GHCR

**Образец:** `.github/workflows/image.yml`.

**Шаги:**
1. Отдельный workflow на `push: main` и `tags: v*`; PR только собирает (job в `ci.yml`).
2. `permissions: {}` на уровне workflow, на job — `contents: read, packages: write, id-token: write,
   attestations: write`; логин `GITHUB_TOKEN`.
3. Матрица целей (`runtime`, `browser` с `suffix: -browser`); `docker/metadata-action`: `type=sha,prefix=sha-`,
   `type=ref,event=branch`, semver `{{version}}`/`{{major}}.{{minor}}`, `latest` только на тегах.
4. `build-push-action` с `provenance: mode=max`, `sbom: true`, `build-args: VERSION=<meta.version>`.
5. `actions/attest-build-provenance` с `push-to-registry: true`.
6. Trivy образом по digest: HIGH печатать, падать на CRITICAL с фиксом (`--ignore-unfixed --exit-code 1`).
   Перед включением — прогнать локально, чтобы не уронить первый же релиз.

**Готово:** анонимный `docker pull` (пакет публичный), `gh attestation verify oci://… -R <repo>`, SBOM в
`docker buildx imagetools inspect --format '{{json .SBOM}}'`, после тега есть `X.Y.Z`, `X.Y`, `latest`.

**Грабли:** сетевой сбой логина в GHCR (`context deadline exceeded`) — не код: `gh run rerun <id> --failed`.

## 4. Helm-чарт и kind-smoke в CI

**Образец:** `deploy/helm/buysmarter/`, `deploy/kind/`, job `helm` в `ci.yml`.

**Шаги:**
1. Приложение до чарта: `/health` без зависимостей (liveness), `/ready` с проверкой БД и Redis (readiness),
   логи в поток (`BUYSMARTER_LOG_FILE=-`), версия из метаданных пакета.
2. Чарт: Deployment, Service, Secret или `secret.existing`, ServiceAccount (`automountServiceAccountToken: false`),
   Job миграций как хук `pre-install,pre-upgrade`, `helm test`, опционально Ingress/HPA/PDB/ServiceMonitor/PodMonitor.
3. PSS `restricted`: `runAsNonRoot`, uid 10001, `readOnlyRootFilesystem`, `drop: [ALL]`, `seccompProfile:
   RuntimeDefault`, `emptyDir` на кэш и `/tmp`. Метка namespace `pod-security.kubernetes.io/enforce=restricted`.
4. БД и Redis чарт не ставит; в kind — простые манифесты в отдельном namespace.
5. `deploy/kind/smoke.sh`: deps → `helm upgrade --install --wait` → `helm test --logs` → port-forward → curl проб и
   `/metrics` → одна задача до терминального статуса → повторный upgrade → `doctor` в поде. `trap dump ERR`.
6. CI: `helm lint` + `helm template | kubeconform -strict` (с CRD-схемами datreeio для Monitor-ресурсов),
   сборка образа, `helm/kind-action`, `kind load docker-image`, `smoke.sh`.

**Готово:** локальный прогон smoke в чистом kind, нарушений PSS нет, миграции в БД, CI-job зелёный.

**Грабли:**
- Pre-install хук стартует до обычных ресурсов → Secret, который он читает, тоже хук (weight -10). Цена: Secret
  остаётся после `helm uninstall`.
- `helm lint` не проверяет `required` — проверять `helm template` без значений (должен упасть).
- `helm test --logs` не прочитает под с `hook-delete-policy: hook-succeeded` → только `before-hook-creation`.
- Официальные образы postgres/redis стартуют от root → в отдельный namespace без PSS.
- `kind load docker-image` иногда молча не грузит → `ErrImagePull` у Job миграций; повторить загрузку.
- Хелпер env: переменные, нужные только воркеру, класть в общий словарь, а не дописывать в конец списка `env:`.

## 5. OpenTelemetry и Prometheus

**Образец:** `src/buysmarter/telemetry.py`, `deploy/observability/`.

**Шаги:**
1. Доменные инструменты на глобальном meter на уровне модуля (в CLI без провайдера это no-op).
2. `instrument_app(app)`: MeterProvider + `PrometheusMetricReader`, `GET /metrics` маршрутом
   (`generate_latest()`), FastAPI/httpx/SQLAlchemy-инструментация, OTLP-трейсы только при
   `OTEL_EXPORTER_OTLP_ENDPOINT`. Выключатель `BUYSMARTER_METRICS=0`.
3. Метрики пишутся в сервисном слое (обёртка над главной функцией, точка вызова источников, LLM-адаптер), не в CLI.
4. Метки только низкой кардинальности: исход, площадка, модель. Свободный текст запроса — никогда.
5. Compose-профиль: Prometheus, Grafana с provisioned-датасорсом и дашбордом, Jaeger v2 (принимает OTLP сам,
   коллектор не нужен, пока бэкенд один).
6. Тест: хелпер `_sample(name, **labels)` парсит `/metrics` через `prometheus_client.parser`; проверять приращение.

**Грабли:**
- `OTEL_SEMCONV_STABILITY_OPT_IN=http` читается один раз первым инструментором. Если другой инструментор (taskiq)
  импортируется раньше — конвенции старые и `http_route` пропадает. Ставить `setdefault` на уровне модуля telemetry.
- Пробы и сам `/metrics` исключать: `excluded_urls="health,ready,metrics"` (и тест не должен проверять HTTP-метрики
  на `/health`).
- Панель без данных: `or vector(0)` + `axisSoftMax: 1`; нулевому ряду дать подпись через `label_replace`.
  Отношение при нулевом знаменателе не рисовать нулём — менять панель на счётчик.
- Метрики подбора живут там, где идёт подбор: после переноса в воркер у него свой `/metrics` на отдельном порту.

## 6. Асинхронный HTTP API на очереди Redis

**Образец:** `src/buysmarter/jobs.py`, `worker.py`, `api/recommend.py`, план `docs/PLAN.md, 2026-10-16-recommend-api`.

**Шаги:**
1. Общая валидация входа для всех точек входа (`select_sources`) — рефакторинг отдельным коммитом.
2. `jobs.py`: модель запроса, публичная схема результата (маппинг внутренней модели), `JobStore` (hash задачи с TTL,
   дедуп-слот `SET NX EX`), функция rate limit (фиксированное окно, `INCR`+`EXPIRE`).
3. `worker.py`: `RedisStreamBroker`, задача идемпотентна (терминальный статус → выход), свой таймаут через
   `asyncio.wait_for`, исходы `done`/`no_match`/`failed` без внутренностей наружу, `TaskiqInstrumentor` до брокера.
4. API: `POST` → 202 + `Location` (повтор того же запроса → 200 с той же задачей; лимит → 429 + `Retry-After`;
   Redis недоступен → 503 через exception handler), `GET /…/{id}`, опциональный `X-API-Key` (`secrets.compare_digest`),
   зависимости через `Annotated[..., Depends()]`, 401 описан в `responses` роутера.
5. Воркер в compose и Helm (второй Deployment, liveness на его метрики, PodMonitor), kind-smoke с задачей.
6. Метрики очереди: итоги задач, время в очереди, отказы по лимиту; ряд «Queue» на дашборде; сквозной трейс.

**Готово:** реальная задача до `done`, повтор → тот же id, 11-я → 429, убитый посреди подбора воркер
(`docker compose kill worker`) → задачу добирает новый без дубля, один трейс от `POST` до HTTP к площадкам.

**Грабли:**
- `--ack-type when_executed`, иначе задача упавшего воркера потеряна.
- taskiq-redis 1.2.4 делает `XAUTOCLAIM` только после непустого `XREADGROUP` → на пустой очереди зависшая задача
  ждёт следующего сообщения. Обход: воркер раз в `idle_timeout/2` шлёт пустую задачу. `idle_timeout` = 2 × таймаут
  задачи. Апстрим: taskiq-redis PR #129.
- `InMemoryBroker.startup()` вызывает и воркерные события → сервер метрик стартует в тестах повторно (порт занят).
  Гард `if not broker.is_worker_process: return`.
- Задача, поставленная через `TestClient`, живёт в его event loop: `broker.wait_all()` из pytest падает. Тест
  опрашивает `GET`, как настоящий клиент.
- Тестовый брокер выбирать env-переменной, выставленной в начале `conftest.py` до импорта модулей.
- `pydantic-settings`: свои переменные с префиксом — `Field(..., validation_alias="BUYSMARTER_…")`.
- Если зависимость (taskiq + aiohttp) выводит образ за цель — решать на шаге добавления, а не в конце.

## 7. Релиз

1. `uv version X.Y.Z` (обновит и `uv.lock`), `ARG VERSION` в Dockerfile, `version`/`appVersion` в `Chart.yaml`.
2. CHANGELOG: `[Unreleased]` → `[X.Y.Z] - <дата>` (Added / Changed / Fixed / Security).
3. Полный pytest, коммит `chore: release X.Y.Z`, `git tag -a vX.Y.Z -m vX.Y.Z`, `git push origin main vX.Y.Z`.
4. Дождаться `release` (PyPI + GitHub Release), `image` по тегу и `CI`.
5. Проверить: `curl -s https://pypi.org/pypi/<pkg>/X.Y.Z/json` → 200 (общий JSON кэшируется CDN), теги образа
   через `imagetools inspect`, версия внутри образа.
6. Публичная поверхность → minor (0.1.x → 0.2.0). Релиз публикует в PyPI — подтверждать у пользователя.

## 8. Витрина в README

**Образец:** раздел «How it runs» в `README.md`, `assets/readme/`.

**Шаги:**
1. Схема Mermaid (`flowchart TD`): GitHub рисует сама, светлая и тёмная тема. Горизонтальная схема с длинными
   подписями становится нечитаемой, а кнопки зума GitHub закрывают правый край.
2. Поднять стек с наблюдаемостью и наполнить реальными задачами (6–9 штук с паузами), чтобы графики были живыми.
3. GIF через VHS (`assets/readme/api-demo.tape` хранить рядом): ожидание вырезать `Hide`/`Show` + `Wait@120s /regex/`.
4. Скриншоты headless Chromium из Playwright: Grafana `?kiosk&theme=dark&from=now-30m`, Jaeger
   `/trace/<id>` самого большого трейса (id через `/api/v3/traces`).
5. Посмотреть каждый кадр и скриншот глазами (ffmpeg → PNG → Read). Пустые панели — чинить дашборд, не прятать.
6. Каждое число в тексте и alt сверить с источником (Prometheus API, Jaeger): «9 задач», «p95 4,75 с».
7. Картинки друг под другом во всю ширину; alt — фактическое описание того, что на картинке.
8. После push открыть страницу GitHub в Playwright (`wait_until="domcontentloaded"`, full_page + clip по заголовкам)
   и проверить рендер. Картинки GitHub кэширует через camo — обновлённая появится не сразу.

**Грабли VHS:** строки с `"` внутри — в обратных кавычках; `Wait` по умолчанию ждёт `>` — задать свой regex
(последняя строка обрезается, `$ ` превращается в `$`); дедупликация API вернёт готовую задачу — на каждый дубль
записи менять запрос.

## 9. Апстрим-проблема в зависимости

1. Подтвердить по исходникам (`grep` по site-packages, чтение функции) и воспроизвести вживую.
2. Искать дубли: `gh search issues --repo <r> "<ключ>"` по нескольким формулировкам, плюс `gh search prs`; читать
   тела и комментарии ближайших (проблема может быть описана в комментарии к чужому PR).
3. Обход в коде с `# ponytail:` и прямой ссылкой на issue/PR.
4. Не плодить дубль: комментарий к существующему PR. Публичное действие — только по слову пользователя.
5. Текст комментария: humanizer, строки ≤ 68 символов, абзацы ≤ 5 строк, ≤ 800 символов, без преувеличений
   («production use», если это не продакшн, — убрать).

## 10. Обновление связанных документов

После крупной работы пройтись по всему, что описывает проект:
- README EN/RU: вступление, структура (`Layout`), разделы новых возможностей;
- CONTRIBUTING EN/RU: новые соглашения (ponytail, как тестировать без внешних сервисов);
- старые планы: закрытые риски отметить ✅ со ссылкой на план, который их закрыл;
- внешние документы (портфельные приоритеты в соседнем репо) — отметить статус.

## Инструменты и мелочи окружения

- `rtk` сжимает вывод команд: если результат подозрительно пустой, проверять напрямую (лог в файл, `grep -c`).
- Длинные команды > 2 мин уходят в фон — ждать уведомления, не поллить.
- Pre-push хук в соседнем репо может гонять проверки минутами — это не зависание (`pgrep -af "git push"`).
- Числа «за пределами» (порог 50 прогонов, лимиты) выбирать по масштабу данных и прямо говорить, что выбрал сам.
