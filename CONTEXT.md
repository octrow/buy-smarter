# buy-smarter

An explainable shopping assistant for Kazakhstan: it ranks offers from Kaspi, Ozon and OLX for one user's budget, city and must-haves, and says what it could not verify.

## Language

**Local store**:
The single per-user database (`buysmarter.db` in the user data dir) holding price history, offer snapshots, preferences and learned category knowledge. The LLM and Kimovil caches are separate and not part of it.
_Avoid_: local DB, cache, fallback file

**Category knowledge**:
What the assistant has learned about one product category: its brand book, model book, category type and, for composition-driven categories, its ingredient book. Kept in the local store; a query whose category has none borrows a related category's knowledge or falls back to spec-driven.
_Avoid_: category cache, category config

**Category type**:
How a category is judged. **Spec-driven** (the default — phones, shoes, appliances): offers are compared on specs, brand and price. **Composition-driven** (medicines, cosmetics, supplements): the active substances in the composition decide, so search and ranking also work by substance.
_Avoid_: category kind, mode

**Learning a category**:
Building category knowledge for a category that has none, from web research or the user's notes. Today a separate step the user starts; planned to happen on the first query for an unknown category.
_Avoid_: training, category creation
