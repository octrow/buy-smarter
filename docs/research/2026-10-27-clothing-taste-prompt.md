# Промпт для deep research: модуль «одежда под мой вкус»

Скопировать в Gemini Deep Research целиком. Результат — вернуть в сессию файлом или текстом.

---

You are a senior ML engineer researching how to build a **personal clothing-taste module** for an existing open-source
shopping assistant. Produce a decision-ready report. Prefer sources from 2025–2026; cite every claim (paper, model
card, repo, benchmark page) with a link and date. Write the report in Russian; keep model, library and metric names in
English.

## Context (the host project)

- **buy-smarter**: Python 3.12, Typer CLI + PySide6 desktop app, local SQLite, runs on a user's laptop (Linux; Windows
  and macOS builds too). It searches Kazakhstan marketplaces **Kaspi.kz, Ozon.kz, OLX.kz**, then ranks offers on
  explainable axes (brand, price, reviews, specs…). LLM calls go through an OpenAI-compatible gateway; a small fast
  classifier (TypeSafe Jev) is used for cheap typed judgments.
- What we have per clothing listing: title (Russian/Kazakh, often noisy), price, seller, rating, reviews, **1–N product
  photos** (URLs), sometimes brand, color and size; clothing variants on Kaspi have **no spec page**. OLX listings are
  user photos of used items.
- The new module must be **optional** (an extra, e.g. `pip install buysmarter[style]`), must not slow down non-clothing
  searches, and must work **offline on CPU** after a one-time model download. A GPU may be absent.

## The goal

Rank clothing listings by how well they match **one user's taste**, learned from:
1. a cold start: a few reference photos (own wardrobe, saved looks) and/or a text description ("минимализм, тёмные
   тона, oversize, без логотипов");
2. ongoing feedback: like / dislike on listings shown in the report (tens to a few hundred events per user);
3. hard constraints that are not taste: size, budget, gender, season, material allergies.

Quality first, then speed (target: score 100–300 listings with images in ≤ 5–10 s on a laptop CPU, embeddings
cached), then simplicity (one model + a small learner preferred over a training pipeline).

## Questions to answer

1. **Embedding model.** Compare current fashion-domain image–text models on public fashion retrieval benchmarks
   (LookBench, DeepFashion, Fashion-IQ, KAGL, Polyvore, H&M): Marqo-FashionSigLIP / Marqo-FashionCLIP, HopitAI MODA
   (`moda-fashion-distilled`, `moda-pro-lite-plus`), FashionCLIP 2.0, general SigLIP 2 / OpenCLIP / jina-clip /
   newer 2025–2026 models. For each: params, embedding dim, CPU latency per image (ONNX / int8 if known),
   multilingual text support (Russian queries!), and how it handles marketplace photos (model shots vs flat-lay vs
   amateur OLX photos).
2. **Taste learner on top of embeddings.** Which method works best with 5–300 labeled items per user: centroid /
   Rocchio, logistic regression or linear SVM on frozen embeddings, Gaussian-process / kernel preference models,
   contextual bandits (LinUCB, Thompson) for exploration, small adapters / LoRA personalization, pairwise (Bradley–Terry)
   learning. Evidence from papers or production write-ups; failure modes (popularity collapse, filter bubble, too-few
   negatives) and how to handle them. Multiple tastes per user (work vs weekend) — prototypes / mixture?
3. **Cold start from text and a few photos.** Zero-shot text → image scoring, combining text and image prototypes,
   style-attribute vocabularies (fashion ontologies, attribute extractors such as MODA_NER), "style tags" via a VLM.
4. **Attributes that are not visual taste.** Extracting size, fit, material, season from noisy Russian titles and
   photos; when a VLM (local or API) is worth its cost vs regex/classifier.
5. **Pipeline and storage.** Image fetching and caching, background crop / garment detection (is it needed?),
   vector storage in a local app (sqlite-vec, LanceDB, FAISS, plain numpy), re-ranking cost, explainability ("похоже на
   ваши X и Y") suitable for a report that explains every score.
6. **Existing open-source systems** (GitHub, Hugging Face Spaces, papers with code) for personal fashion
   recommendation from likes — what can be reused as-is, what is research-only. Note stars and last commit.
7. **Evaluation without a big dataset.** How to measure that the module helps for one user (offline replay of
   likes, small A/B in the report, metrics like nDCG@10 / hit-rate of liked items).
8. **Risks.** Model size and download, privacy of user photos (all local?), bias. This is a personal project:
   **licenses do not matter** — do not exclude or rank anything by license.

## Output format

1. **Recommendation in 10 lines**: the model, the taste learner, the storage, expected latency and quality, why.
2. **Comparison tables** for Q1 and Q2 with numbers and links.
3. **Architecture sketch** for the optional module: data flow from listing → image → embedding → taste score → one
   extra ranking axis, plus feedback loop; what runs at search time vs in the background.
4. **MVP plan in 3–5 steps** (smallest version first), and what to postpone.
5. **Open questions** you could not resolve, with what would resolve them.
6. **Sources**: full list with dates.

Starting points already found (verify and go beyond): `hopit-ai/Moda` (2026), `marqo-ai/marqo-FashionCLIP`,
`patrickjohncyh/fashion-clip`, `SerendipityOneInc/look-bench`.
