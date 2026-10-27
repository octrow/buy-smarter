# Clothing taste: Marqo-FashionSigLIP on CPU against Kaspi

Date: 2026-10-27. Laptop CPU, 16 threads. Scripts: `clothing-taste/` (run with
`uv run --no-project --with onnxruntime --with pillow --with numpy [--with tokenizers] python <script> ~/style-test/like <spike dir>`;
the Kaspi fetchers run in the project venv). Background: `docs/research/2026-10-27-clothing-taste-review.md`.

Likes: 164 photos the user saved (outfits 35, sneakers 41, winter boots 14, jackets and parkas 74): studio shots of
New Balance, Adidas, Asics, Vans, Converse, Danner, The North Face, Patagonia, Columbia. No dislikes.

## Verdict: WORKS, once the pool holds the user's kind of goods

| | Result |
|---|---|
| Model | `Marqo/marqo-fashionSigLIP`, ready ONNX from the model repo: `vision_model_int8.onnx` 90 MB, `text_model_int8.onnx` 111 MB + `tokenizer.json`; 768-d; no torch |
| Speed (preprocessing included) | int8 **82 ms/photo** in batches of 16, 184 ms one by one; fp32 138 ms. 100 new cards ≈ 8 s, cached afterwards. The research's 30–40 ms was optimistic |
| int8 vs fp32 | cosine 0.988 mean (min 0.969); top-10 overlap 8–9/10 → int8 |
| Kaspi previews | `?format=preview-medium`, ~17 KB, fast |
| Held-out like beats share of Kaspi cards, same category (0.5 = chance) | sneakers 0.97, boots 0.97, jackets 0.87 |
| Control: other-category likes as anchors | 0.52 / 0.48 / 0.68 — the score is about the goods, not the photo source |

**Generic queries are the bottleneck.** "мужские кроссовки", "мужская зимняя куртка" return cheap unbranded cards:
the user found neither the top nor the bottom row of the taste ranking theirs. With Kaspi cards for the liked brands
added to the same pool (titles never shown to the model), the brands rise: share in the taste top-16 — sneakers 73% →
100%, boots 33% → 69%, jackets 32% → 62%; the sneaker top-16 is Samba, Gel, Chuck 70, 530, Old Skool.

**A "never" rule in words works zero-shot.** Three phrases for "cheap glossy white plastic sole" (text tower, mean
cosine, z-scored in the pool): 13 of the 16 strongest hits are 3–7 k ₸ white-platform sneakers; white Converse, Vans
and a mint Puma are caught too (words are fuzzy — dislike photos are the precise tool). A penalty of 0.15 per z above 0
pushes 2 cards out of the taste top-16; what is left is the branded set above. Write the thing itself, never "no X":
these models are blind to negation.

**Brands from the user's menswear digest** (`tiktok-lazy-follower/digests/menswear-actions.md`) on Kaspi, cards naming
the brand in the first page: Adidas Samba, Brooks, Helly Hansen, Salomon, New Balance 12/12; Arc'teryx 12 from 3 990 ₸,
Stone Island 9 900–16 500 ₸, The North Face from 14 000 ₸ — price says replica, the counterfeit check matters here;
Nothomme, Blankgang, Kotlovan none.

Dropped: centring on the Kaspi mean (any non-Kaspi photo then wins); MODA for now (the pool, not the model, was the
limit).
