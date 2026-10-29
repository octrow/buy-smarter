# Передача сессии: модуль стиля одежды (2026-10-27, после шага 4)

## Промпт 1 — для `/compact`

```
/compact Сохрани для продолжения: проект buy-smarter, ветка main, git — /usr/bin/git, одна простая
команда на вызов. Идёт план docs/plans/2026-10-27-clothing-taste-mvp.md (возможность `style`). Шаги 1–4 готовы и
запушены: d132a35 (ядро: extra `style`, ONNX Marqo-FashionSigLIP int8, кэш векторов style_vectors), af47375
(профиль: style/profile.py — бренды/фразы в user_preferences["style"], фото-копии без EXIF в
~/.local/share/buysmarter/style, векторы like/dislike/text; CLI `buysmarter style` show/add/remove/brands/like-text/
never/download/strength; экран «Стиль» gui/screens/style.py), 63ecb16 (capabilities/style.py: Jev-вопрос про
одежду, хук `prime` в base/flow/recommend и ADR-0005, до 6 поисков Kaspi по брендам, enrich → style/match.py →
Candidate.style_match, ось «Стиль» = процентиль ближайшего «нравится» минус 0.15 за z-единицу «нет», без фото —
половина, только бренды — оси нет; «Сила стиля» 0–50, по умолчанию 20, через current_axis_weight), 51b99f3
(👍/👎: gui/style_votes.py под лучшим выбором и для выбранной строки альтернатив, CLI `style like|dislike <ссылка>`
ищет товар в products по url, profile.add_card; подсказка в консольном отчёте, cli/style.votes_work), ba671e4
(добавление фото пачками по 16 с полосой «N из M» и «Отмена» в GUI, rich-полоса и Ctrl+C в CLI, Pillow draft,
encoder.shared() грузит модель один раз) и рефакторинги до bbaa26a. Полный прогон последний раз зелёный на
51b99f3 (1793); после — только затронутые тесты (зелёные). Пользователь уже импортировал ~/style-test/like:
124 фото «нравится» в своём настоящем профиле; бренды и фразы пусты. Замер: копирование 5 с, загрузка модели 1 с,
кодирование 25 с на 124 фото (~200 мс/фото против 82 мс в спайке — причина не выяснена). Открыто: шаг 5
(документы почти готовы — сверить план), шаг 6 (живой прогон), StyleScreen ~440 строк (пограничный Large Class),
8 уязвимостей Dependabot, плавающий краш Qt в полном прогоне (1 из 4), старые провалы complexipy
(recommend_pipeline, resolve_and_ask). Правила: коммит `<type>: …` с Co-Authored-By и Claude-Session, сразу push в
main; TDD; CLI = GUI; карта EN+RU в том же коммите; GUI — скриншоты обеих тем и 960×640; скриншоты пользователя
в docs/review/ не коммитить; фото пользователя никуда не уходят; отвечать по-русски, коротко, с рекомендациями.
```

## Промпт 2 — после `/compact`

```
Продолжаем план docs/plans/2026-10-27-clothing-taste-mvp.md. Сначала `git status` (через /usr/bin/git) и
`git log --oneline -8`; полный прогон `QT_QPA_PLATFORM=offscreen uv run pytest -q`, ruff, `ruff format --check`,
`mypy src` — если что-то красное, почини и закоммить. Потом шаг 5: сверь, что карта EN+RU, ADR-0005, README EN+RU
и CHANGELOG описывают возможность `style` целиком, отметь шаг 5 в плане. Затем шаг 6 — живой прогон на моём
профиле (пишет в ~/.local/share/buysmarter, ходит на Kaspi — перед запуском одной строкой скажи, что сделаешь):
1) `buysmarter style` — проверить, что 124 фото на месте;
2) бренды из личного дайджеста автора по menswear (Samba/adidas, New Balance, Salomon, Brooks,
   Helly Hansen, The North Face; Arc'teryx и Stone Island на Kaspi похожи на реплики — спроси, брать ли);
3) `buysmarter style never "glossy white plastic sole"` (по-английски, без LLM);
4) если «кроссовки» или «куртка» уже изучены раньше, включить им «Стиль» (`category set …`), иначе Jev спросит сам;
5) `buysmarter recommend "мужские кроссовки"` и `"мужская зимняя куртка"`: верх и низ ранжирования, пояснения оси
   «Стиль», где оказалась белая пластиковая подошва, сколько длился шаг style;
6) то же в приложении — скриншоты отчёта, 👍/👎 на одной карточке.
Результаты и выводы запиши в план (раздел «Живой прогон»), отметь «Готово, когда», закоммить и запушь. В конце —
коротко по-русски: что работает, что нет, и 2–3 рекомендации (в т. ч. про 200 мс/фото и вынос карточки «Образцы»).
```
