# Одежда под личный вкус: разведка repo-scout (2026-10-27)

Задача: опциональный модуль buy-smarter, который учится вкусу пользователя в одежде (лайки и дизлайки, фото-образцы)
и ранжирует карточки Kaspi/Ozon/OLX по близости к нему. Быстро, качественно, умеренно просто.

Личный проект: лицензии не важны.

Два запуска repo-scout (`--project buy-smarter`), 93 + 40 строк. Готового модуля «вкус в одежде» нет: близкие по
теме репо — учебные проекты и дипломы (0–5★, 2–80 коммитов). Пригодны как источник **моделей и метода**, не как основа:

```
hopit-ai/Moda                 triage: step 2 · close 0.81 · 45★, MIT, push 2026-08 · verdict: → Deep dive (открытые fashion-эмбеддинги + конвейер)
marqo-ai/marqo-FashionCLIP    triage: step 1 · 149★, Apache-2.0, push 2024-09 (репо стоит, модели на HF живут) · verdict: → Inspect (Marqo-FashionSigLIP как эмбеддинг)
patrickjohncyh/fashion-clip   triage: step 1 · 546★, MIT, push 2025-01 · verdict: unclear (сравнить с Marqo/MODA на LookBench)
SerendipityOneInc/look-bench  triage: step 1 · 22★, Apache-2.0, push 2026-09 · verdict: → Inspect (бенчмарк для выбора модели)
```

- **MODA** (Hopit AI): `moda-fashion-distilled` (203M, open_clip) — Fine R@1 67.63 на LookBench, выше 1.24B-модели;
  `moda-pro-lite(-plus)` — текст → изображение. Конвейер BM25 + SPLADE + dense + NER + cross-encoder на H&M. Грузится
  `open_clip.create_model_and_transforms("hf-hub:HopitAI/moda-fashion-distilled")`.
- **Слой вкуса** готовым не нашёлся: его придётся писать (центроид/Rocchio или логистическая регрессия поверх
  эмбеддингов) — это вопрос для deep research (`docs/research/2026-10-27-clothing-taste-prompt.md`).
- Шум выдачи: общие RAG/рекомендательные библиотеки (sentence-transformers, LanceDB, Transformers4Rec) — инфраструктура,
  не решение.
