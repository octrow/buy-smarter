# Desktop GUI on PySide6, core called in-process from an asyncio worker thread

buy-smarter is meant to be installed by people who are not developers, so its GUI is a PySide6 desktop app rather than a NiceGUI page in the browser. It calls the core directly (the same path as the CLI), not the HTTP API, so the app needs no Postgres or Redis. The core is asyncio-based (httpx, curl_cffi, zendriver), so it runs on a standard asyncio loop in a dedicated thread: the UI submits coroutines with `asyncio.run_coroutine_threadsafe`, gets progress and results back through Qt signals, and cancels a search with `future.cancel()`.

v1 screens: Search (live per-marketplace progress, cancel), Report (best pick with per-axis bars; sortable and filterable alternatives; rejected offers with reasons; used OLX listings; what could not be verified), Preferences, Price history, Settings/doctor (LLM key, "pass Ozon").

**Policy across projects:** apps given to strangers get PySide6. Internal tools (chat-watch, greenkeep, jobs, repo-scout, tiktok-lazy-follower) stay on NiceGUI + nicegui-kit and are not migrated. Which project belongs to which tier is decided per project later.

**Look:** a Qt theme of its own (QSS, designed with the impeccable skill), with no tokens shared with nicegui-kit. The two tiers do not need to look like one family.

## Considered Options

- **NiceGUI + nicegui-kit, later `native=True` / PyTauri**: shortest path, kit already exists; rejected for a distributed product in favour of native widgets.
- **`PySide6.QtAsyncio`**: rejected. It is a technical preview and only implements the basic layer (futures, tasks, call_soon). It has no sockets, network connections or subprocesses, which httpx, curl_cffi and zendriver all need (doc.qt.io/qtforpython-6/PySide6/QtAsyncio).
- **qasync**: an extra dependency, and its subprocess/websocket behaviour is unverified.
- **CLI subprocess + JSON stdout**: progress and cancellation are awkward.
- **Flet**: nicegui-kit cannot be reused, and its component model is different.
