# A new category is learned before its first search in the GUI and the CLI

Phase 3 of ADR-0005 started learning a new category after the report, so the search never waited (`docs/plans.md, 2026-10-22-capabilities-phase3`, decision 11). The first result of a new category then had no brand, model or ingredient books: every brand was unknown and composition was not checked (finding #26). The user saw a weak answer, then a button to learn and search again.

The GUI and the CLI now learn a new category first and search with its books. `recommend(learn_first=True)` raises `NeedsLearning` after the profile and the clarification, before any marketplace, when the auto-learning condition holds: no books anywhere in the resolved profile (a related or parent profile with books is not new), a product or need intent, an LLM parse, the claude CLI, the hourly limit. The entry point learns (opus with web search, 1–3 minutes, paid) and calls again without the flag. The GUI shows the learning with a timer and two buttons: "Искать без изучения" searches now while the learning goes on, "Отмена" stops both. The CLI offers the same choices (`cli/learning`): `--learn` learns first without asking; on a terminal it asks first (default yes), and "no" is "Искать без изучения": the search runs now while the category learns in the background, then the CLI waits for it and asks "Повторить поиск?". A report without books on a terminal offers to learn by hand, as the report's button does. Off a terminal without the flag nothing waits. A failed learn searches without books; a used-up hourly limit searches at once and says so. MCP, the HTTP API and the worker keep learning after the report: their clients have timeouts.

## Considered Options

- **Keep "never waits", learn after the report**: fast first answer, but it is the wrong answer for a new category and costs a second search.
- **Wait in every entry point**: MCP and API clients time out long before 1–3 minutes.
- **Wait inside `recommend()`**: the core would own a progress UI and a cancel; an exception like `NeedsClarification` keeps the pause in the entry points.
- **A GUI setting "wait for learning"**: rejected for now; the per-search "Искать без изучения" and `BUYSMARTER_AUTO_LEARN_PER_HOUR=0` cover it.

## Consequences

The first search of a new category in the GUI and the CLI takes 1–3 minutes longer and starts a paid learn without a separate question in the GUI. A cancelled learn drops its in-progress mark, so the next search may learn at once. Plan: `docs/plans.md, 2026-10-24-wait-for-learning`.
