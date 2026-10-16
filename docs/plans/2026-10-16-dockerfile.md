# План: Dockerfile для buy-smarter

Шаг 1 из цепочки флагмана: **Dockerfile** → compose «всё в одном» → образ в GHCR → Helm-чарт → kind-smoke в CI → OpenTelemetry/Prometheus
(общий план — `~/dev/jobs/docs/portfolio-priorities.md`). Этот документ — только про Dockerfile и всё, без чего его нельзя проверить.

## Что есть сейчас (2026-10-16)

- `docker-compose.yml` поднимает только Postgres 16 (:5434) и Redis 7 (:6380); приложение запускается с хоста через `uv`.
- Точек входа три: CLI `buysmarter` (Typer), MCP-сервер `python -m buysmarter.mcp_server` (stdio), FastAPI `buysmarter.api.main:app`, в котором есть **только `/health`**.
- Зависимости `market-sources` и `claude-subprocess` в `[tool.uv.sources]` тянутся из git, поэтому builder без `git` не соберёт `uv.lock`.
- Для Ozon нужен Chromium (`market-sources[browser]` → Playwright). Kaspi и OLX работают без браузера.
- Основной LLM-провайдер — локальный `claude` CLI через OmniRoute. В контейнере его нет, работает только API-фолбэк по ключам из env (`llm/adapter.py`).
- Кэши лежат в `~/.cache/buysmarter/` (`llm_cache.db`, `kimovil.db`), настройки — в `~/.config/buysmarter/.env`.

## Решения

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| 1 | База | `python:3.12-slim-bookworm`, `uv` копируется из `ghcr.io/astral-sh/uv:<pin>` | `requires-python >=3.12`; slim вместо alpine, потому что у `asyncpg` и `curl_cffi` есть manylinux-колёса под glibc, а под musl их нет |
| 2 | Стадии | `builder` (git + uv, `uv sync --locked --no-dev --no-editable`) → `runtime` (копирует `.venv`, без uv и git) → `browser` (`FROM runtime` + `playwright install --with-deps chromium`) | В runtime-образе нет компиляторов и git. Chromium добавляет ~600 МБ, а нужен только Ozon |
| 3 | Кэш слоёв | Сначала `uv sync --no-install-project` только по `pyproject.toml` и `uv.lock` (bind-mount), потом копируется `src/` и выполняется второй `uv sync`. `--mount=type=cache,target=/root/.cache/uv` | При правке кода зависимости не переустанавливаются. Это стандартный приём из документации uv для Docker |
| 4 | Пользователь | `app` (uid 10001), `HOME=/home/app`, `WORKDIR /app` | Не root. Kubernetes `runAsNonRoot` на шаге Helm заработает без правок |
| 5 | Процесс по умолчанию | `CMD ["uvicorn", "buysmarter.api.main:app", "--host", "0.0.0.0", "--port", "8000"]`, `EXPOSE 8000` | Это то, что будет деплоиться в k8s. CLI вызывается так: `docker run … buysmarter recommend "…"` (`ENTRYPOINT` не задаём, чтобы команда легко переопределялась) |
| 6 | Миграции | В entrypoint их **нет**. Запускаются отдельной командой `alembic upgrade head` из того же образа (в compose — сервис `migrate`, в Helm — Job) | Если несколько реплик стартуют одновременно, они не начнут гонять миграции параллельно. Для этого в образ входят `alembic.ini` и `alembic/` |
| 7 | LLM в контейнере | `claude` CLI и Node в образ **не ставим**. Модель задаётся через `DEFAULT_LLM_MODEL` и ключ провайдера в env. Без ключа `/health` и все пути без LLM работают | Нет смысла держать в образе подписочную авторизацию и лишние ~200 МБ Node. Ключи передаются только через env или секреты k8s |
| 8 | Конфиг и секреты | Только env (`DATABASE_URL`, `REDIS_URL`, ключи). `.env` в образ не попадает (`.dockerignore`) | 12-factor. `pydantic-settings` и так читает env первым |
| 9 | Кэши | `VOLUME /home/app/.cache/buysmarter` | SQLite-кэш LLM переживает пересоздание контейнера |
| 10 | Healthcheck | `HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health').status!=200)"` | В slim нет curl. Python уже есть в образе |
| 11 | Метаданные | `LABEL org.opencontainers.image.source=https://github.com/octrow/buy-smarter`, плюс `version` и `licenses=MIT` | GHCR по этому label привязывает пакет к репо (следующий шаг) |
| 12 | Переменные Python | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `UV_COMPILE_BYTECODE=1` (только builder), `UV_LINK_MODE=copy`, `PATH=/app/.venv/bin:$PATH` | Логи без буфера, байткод компилируется при сборке, а не при старте |

## Файлы

- `Dockerfile` — стадии `builder`, `runtime` и `browser`; базовые образы закреплены по digest (Renovate/Dependabot будет их обновлять).
- `.dockerignore` — `.venv`, `.git`, `.env*` (кроме `.env.example`), `tests/`, `docs/`, `assets/`, кэши, `dist/`, `alembic/__pycache__`.
- `.github/workflows/ci.yml` — новый job `docker`: `docker build --target runtime`, затем smoke (поднять контейнер, дождаться `/health` → 200, сделать `docker run … buysmarter --help`). Пуш в реестр в этот шаг не входит, он появится на шаге GHCR.
- `README.md` и `README.ru.md` — раздел «Docker» в 5 строк: build, run, migrate, CLI.

## Порядок работы

1. `.dockerignore` и `Dockerfile` (`builder` → `runtime`), затем локально `docker build --target runtime -t buysmarter:dev .`.
2. Проверить `docker run --rm -p 8000:8000 buysmarter:dev` → `curl :8000/health`; `docker run --rm buysmarter:dev buysmarter --help`.
3. Проверить миграции против compose-Postgres: `docker run --rm --network host -e DATABASE_URL=…@localhost:5434/… buysmarter:dev alembic upgrade head`.
4. Добавить стадию `browser`, проверить `docker run … buysmarter:browser python -c "from playwright.sync_api import sync_playwright; …launch()"`.
5. Добавить CI-job `docker`, прогнать на ветке; кэш слоёв — через `actions/cache` или `type=gha`.
6. Обновить README (EN и RU), сделать запись в CHANGELOG и коммит `feat: container image (runtime + browser targets)`.

## Готово, когда

- [x] `runtime` весит **≤ 350 МБ** (базовый `python:slim` ~131 МБ + `.pyc` ~65 МБ; факт 335 МБ) (проверка: `docker image ls`), `browser` — ≤ 1,2 ГБ.
- [x] Контейнер работает не от root (`docker run … id -u` → 10001).
- [x] `/health` отвечает 200 и Docker показывает статус `healthy`.
- [x] `alembic upgrade head` из образа применяется к чистой БД.
- [x] CLI работает из образа (`buysmarter --help`).
- [x] Правка в `src/` не пересобирает слой зависимостей (видно по `CACHED` в выводе сборки).
- [x] CI-job `docker` зелёный; в образе нет `.env` и `.git` (`docker run … ls -a /app`).
- [x] `hadolint Dockerfile` без ошибок; `security.yml` при желании сканирует образ (Trivy) — это опционально (Trivy пока не добавлен).

## Вне этого шага

- Compose «всё в одном» (сервисы `app` и `migrate` поверх postgres и redis) — шаг 2.
- Публикация в GHCR, теги и подпись (cosign/attestations) — шаг 3.
- MCP-сервер в контейнере: stdio-транспорт неудобен в Docker; если понадобится, это отдельный HTTP-транспорт fastmcp.

## Риск, который надо закрыть до шага Helm

**В API есть только `/health`.** Флагман продаётся как «FastAPI-бэкенд», но HTTP-поверхности у него нет: вся логика доступна через CLI и MCP. Если задеплоить в k8s сервис с одним эндпоинтом, интервьюер это заметит. Рекомендую между шагами 3 и 4 добавить `POST /recommend` и `GET /history` поверх существующих `services/`, с Pydantic-схемами, тестами и OpenAPI. Это сделает Helm-деплой осмысленным.
