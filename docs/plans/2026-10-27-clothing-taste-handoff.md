# Передача сессии: модуль стиля одежды (2026-10-27)

## Промпт 1 — для `/compact`

```
/compact Сохрани для продолжения: проект ~/dev/buy-smarter, ветка main, git — /usr/bin/git. Идёт план
docs/plans/2026-10-27-clothing-taste-mvp.md (возможность `style`: бренды → доп. поиски Kaspi, ось «Стиль» по
фото/лайкам, «нет»-фразы и фото-дизлайки). Спайк — docs/spikes/clothing-taste-spike.md, проверка исследования —
docs/research/2026-10-27-clothing-taste-review.md. Шаг 1 написан, НЕ закоммичен: extra `style` в pyproject + uv.lock,
CI/release синхронизируют `--extra style`, mypy override onnxruntime; Candidate.image_url (ranking/models,
normalize/candidate); таблица style_vectors (db/models.StyleVector, миграция 7b2e4c9d1a05, repo.get/add_style_vectors);
пакет src/buysmarter/style/ (__init__.available, weights — Marqo-FashionSigLIP int8 ONNX, ревизия c56244cc…, SHA-256,
скачивание в ~/.cache/buysmarter/style; encoder — Encoder.load/images/texts, pixels; cache.card_vectors); строка
doctor «Clothing style» (services/doctor.style_check); тесты tests/style/*, tests/cli/test_doctor.py,
tests/normalize/test_candidate.py; conftest пропускает tests/style без extra; документы: logic-map EN+RU (Doctor),
README EN+RU, CHANGELOG [Unreleased] Added, план — шаг 1 ✅. Затронутые тесты, ruff, format, mypy — зелёные; полный
прогон не дошёл (ошибка инструмента). Веса уже скачаны, Encoder совпадает со спайком (cos 0.9999998, 81 мс/фото).
Фото-лайки пользователя: ~/style-test/like (4 подпапки, 164 фото), dislike пуст. Правила: коммит `<type>: …` с
Co-Authored-By и Claude-Session, сразу push в main; TDD; CLI = GUI; карта EN+RU в том же коммите; отвечать по-русски,
коротко, с рекомендациями.
```

## Промпт 2 — после `/compact`

```
Продолжаем план docs/plans/2026-10-27-clothing-taste-mvp.md. Сначала закрой шаг 1: `git status` (через
/usr/bin/git), полный прогон `QT_QPA_PLATFORM=offscreen uv run pytest -q`, ruff, `ruff format --check`, `mypy src`,
`complexipy src`; если зелёно — один коммит «feat: groundwork for the clothing-taste module (style extra, encoder,
vector cache)» и push. Затем шаг 2 (профиль: образцы-фото с `--no`, бренды, фразы «нравится»/«нет» с переводом через
LLM; CLI `buysmarter style …`; экран «Стиль» в GUI, скриншоты обеих тем и 960×640) — тест первым, по решениям
В3, В7, В9, В10 плана. Профиль (бренды, фразы) — в user_preferences под ключом "style", векторы — в style_vectors
(kind: like / dislike / text). Перед кодом коротко скажи, что делаешь.
```
