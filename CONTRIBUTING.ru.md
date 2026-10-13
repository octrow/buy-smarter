**[English](CONTRIBUTING.md)** | **Русский**

# Как помочь проекту

Приветствуются баг-репорты, знания о брендах для новых категорий и починка источников.

## Подготовка

```bash
uv sync
make check   # ruff check + ruff format --check + pytest
```

Тестам сеть не нужна. Тесты базы запускаются, когда поднят Postgres (`make up && uv run alembic upgrade head`),
иначе пропускаются.

## Pull request

- Одно изменение на PR и тест, который без него падает.
- Изменения парсера идут вместе с сохранённым ответом в `tests/samples/` (без персональных данных).
- Сообщения коммитов: `<type>: <description>`, где type — feat, fix, refactor, docs, test, chore, perf или ci.
- Адаптеры площадок живут в [market-sources](https://github.com/octrow/market-sources); сломанный парсер
  Kaspi/Ozon/OLX обычно чинится там.
- Изменения в `README.md` дублируются в `README.ru.md`, и наоборот.

Бережно относитесь к площадкам: не ускоряйте запросы и не добавляйте массовый обход.
