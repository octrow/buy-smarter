# Design

## Context

The core entry points are `services.recommend.recommend(query, *, city, budget, use_llm, sources, …) -> RecommendResult`, `services.price_tracking.product_history(term)` and `refresh_tracked()`, and `memory.prefs.get_all/set`. `RecommendResult` is a pydantic model (recommendation, constraints, used_listings, review_summary, city_defaulted). The pipeline logs a `stage <name> took …` line per stage, and the collectors log per marketplace. `doctor_checks()` lives in `cli/main.py`. `ozon_setup` opens a visible browser to pass the challenge.

## Goals / Non-Goals

**Goals:** a responsive Qt UI over an unchanged async core; cancellable work; the same vocabulary as the CLI report.
**Non-Goals:** packaging (desktop-packaging); a web or mobile UI; editing category knowledge; multi-window layouts.

## Decisions

- **Package layout.** `src/buysmarter/gui/`, next to the other entry-point adapters (`cli/`, `mcp_server/`, `api/`): `app.py` (QApplication, theme, main window with sidebar navigation), `bridge.py` (core runner), `screens/{search,report,preferences,history,settings}.py`, `theme/` (QSS + palette). The models stay in the core, with no GUI copies.
- **Bridge.** `CoreRunner(QObject)` owns one `threading.Thread` running `asyncio.new_event_loop().run_forever()`. `submit(coro) -> Job` uses `asyncio.run_coroutine_threadsafe` and connects the future's done-callback to Qt signals `finished(object)` / `failed(str)` (emitted through a queued connection, so slots run on the GUI thread). `Job.cancel()` → `future.cancel()`, and the core's existing `aclose` paths release browsers. Shutdown stops the loop and joins the thread. Alternatives (QtAsyncio, qasync, CLI subprocess) are rejected in ADR 0001.
- **Progress without core changes.** A `logging.Handler` attached to the `buysmarter` logger for the job's duration maps known records (stage timings, per-source start/done/skip/fail) to a `progress(source, state, text)` signal. Alternative: add a progress callback through `recommend()`. Deferred: the logs already carry the events, and a callback can replace the handler later without touching the screens.
- **Report rendering.** Built from `RecommendResult` directly (widgets plus a `QSortFilterProxyModel` over a `QAbstractTableModel` of alternatives), not from `cli/report.py` markdown. Axis names and reasons reuse the core's strings so both reports read alike.
- **Doctor module.** Move `doctor_checks()` and its probes to `buysmarter/services/doctor.py`. The CLI and the GUI both import it.
- **Theme.** Two QSS files generated from one Python palette dict (light and dark), chosen through `QStyleHints.colorScheme()` and re-applied on `colorSchemeChanged`. Inter font bundled as a resource. The palette values come from an impeccable design pass (task 5.1), and a contrast test checks every pair.
- **Errors.** `failed` carries a user-facing message. The traceback goes to `logsetup`'s file handler, and a global `sys.excepthook` shows a dialog instead of exiting.

## Risks / Trade-offs

- [Risk] Log-record parsing is brittle if the message wording changes → match on logger name plus `extra` fields added to those few log calls (a tiny core edit), not on message text.
- [Risk] Cancelling during a Playwright navigation leaves Chrome running → the bridge awaits the source `aclose()` in a `finally`. A test asserts no browser process is left (stubbed).
- [Risk] Long CPU-bound ranking blocks the core loop, not the GUI → acceptable, because the GUI thread stays free.
- [Risk] Qt tests flaky in CI → `pytest-qt` with `offscreen`, `qtbot.waitSignal` with timeouts, and the core stubbed in GUI tests.
