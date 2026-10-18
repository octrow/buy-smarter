# buy-smarter

An explainable shopping assistant for Kazakhstan: it ranks offers from Kaspi, Ozon and OLX for one user's budget, city and must-haves, and says what it could not verify.

## Language

**Local store**:
The single per-user database (`buysmarter.db` in the user data dir) holding price history, offer snapshots, preferences and learned category knowledge. The LLM and Kimovil caches are separate and not part of it.
_Avoid_: local DB, cache, fallback file
