# Покрытие тестами: промпты для compact и продолжения

## 1. Для `/compact`

```
/compact Сохрани только то, что нужно для следующей задачи — покрытие тестами в buy-smarter:
- репо buy-smarter, работа прямо в main, коммит+пуш без вопросов, формат `<type>: <description>` + Co-Authored-By;
- сделано: code-health закрыт (ruff, vulture, radon/complexipy 0 находок, C90 max 10 + complexipy в CI, mypy strict на весь src), services/ и ranking/ разбиты на модули, все файлы < 800 строк, 668 passed / 6 skipped;
- CI (.github/workflows/ci.yml, job check): uv sync --locked → alembic → ruff check → ruff format --check → mypy src → complexipy src → pytest -q; dev-зависимости пинуются точной версией в [dependency-groups].dev;
- правила: экономно по токенам, точечное чтение, только затронутые тесты, полный прогон в конце; короткие отчёты; preflight: python3 octrow-skills/plugins/repo-ready/skills/repo-code-health/engine/preflight.py . --scenario code-health.
Остальную историю (рефакторинги, ревью, api-autotests/conversation_flow) можно отбросить.
```

## 2. После compact

```
Покрытие тестами в buy-smarter, цель 80%+.
1. Добавь pytest-cov как dev-зависимость с пином, конфиг в pyproject ([tool.coverage.run] source=["src/buysmarter"], branch=true; [tool.coverage.report] show_missing, skip_covered). Прогони `uv run pytest -q --cov` и покажи итог + 10 худших модулей по покрытию.
2. Предложи, какие модули добирать первыми: чистая логика (ranking/, normalize/, services/matching, knowledge) раньше I/O-обвязки (db/, cli/, mcp_server/); то, что требует сети или Postgres, — кандидаты в omit с причиной, а не в тесты. Спроси, что берём.
3. Тесты на выбранное: Arrange-Act-Assert, имя описывает поведение, без новых фикстур-фреймворков, поведение кода не меняем; если тест вскрыл баг — остановись и скажи.
4. Когда общий процент дойдёт до цели — `--cov-fail-under=<достигнутое, округлённое вниз>` в CI (шаг pytest) и в pyproject, коммит+пуш, дождись зелёного CI.
Отчёт по формату: Before → after (общий %, по модулям), Verified, Not done, Next step.
```
