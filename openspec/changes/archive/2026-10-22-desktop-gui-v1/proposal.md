# Proposal

## Why

buy-smarter is reachable today only through the CLI, MCP and the HTTP API, all of which are developer tools. ADR 0001 picks a PySide6 desktop app that calls the core in-process, so that people who are not developers can search, read the explainable report, keep preferences and watch prices.

## What Changes

- New package `buysmarter.gui` (PySide6) behind a `[gui]` extra. It is launched with `buysmarter gui` and the `buysmarter-gui` script.
- A core bridge: one asyncio loop in a worker thread runs the core coroutines. Progress, results and errors come back as Qt signals, and a running search can be cancelled.
- Five screens: **Search**, **Report**, **Preferences**, **Price history**, **Settings**. Settings covers the doctor checks, the LLM key (via llm-key-keyring), "Pass Ozon check" and the store location.
- A Qt theme of its own (QSS). It is designed with the impeccable skill, in light and dark variants that follow the OS, with WCAG AA contrast.
- `doctor_checks()` moves from `cli/main.py` to a shared module so the CLI and the GUI show the same rows.

## Capabilities

### New Capabilities

- `desktop-gui`: what the desktop app lets a user do, and how it behaves while the core works (progress, cancel, errors, offline pieces).

### Modified Capabilities

(none)

## Impact

- Depends on: `sqlite-default-store` (history and preferences without Postgres), `llm-key-keyring` (settings key entry, no-LLM runs), and `ozon-system-chrome` (Ozon browser status).
- Dependencies: `PySide6` in `[gui]`, `pytest-qt` in dev.
- Code: new `src/buysmarter/gui/`, `services/doctor.py` (moved doctor checks), and `cli/main.py` (the `gui` command, doctor import). The core needs no API change: progress comes from the existing pipeline log records.
- CI: GUI tests run headless (`QT_QPA_PLATFORM=offscreen`).
