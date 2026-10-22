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

**Category profile**:
Everything the assistant knows about how to judge one product category: its brand and model books and the capabilities switched on for it, each with its own parameters. Category knowledge is the part of the profile learned so far.
_Avoid_: category config, category settings

**Capability**:
One optional way of judging offers that a category profile switches on: composition (active substances), benchmark (a performance index such as Kimovil), audience, size, counterfeit checks, official stores, used market. A capability can add searches, enrich candidates, reject candidates, score an axis, add pros and cons, or add a report section. The core flow is the same for every category; capabilities are what differ.
_Avoid_: module (that word means a code module), plugin, trait, feature (that word means a required product property)
