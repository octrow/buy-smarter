# Agents: start here

1. `CONTEXT.md` — the domain terms. Use them in code, docs and commits.
2. `docs/logic-map.md` — how a request becomes a ranked answer: every step, every branch, the module behind it.
3. `docs/adr/` — decisions that are hard to reverse.
4. `docs/plans/` — work in progress; `2026-10-22-logic-map-fixes.md` lists the known logic gaps.

Rules:
- Change a pipeline step or a branch → update `docs/logic-map.md` and `docs/logic-map.ru.md` in the same commit.
- `tests/test_logic_map.py` fails when a `Module:` line in the map points to a module that is gone.
- A new or sharpened domain term goes into `CONTEXT.md`.
