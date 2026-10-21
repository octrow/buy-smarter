# Design

## Context

`release.yml` builds the sdist/wheel with `uv build` and publishes to PyPI on `v*` tags. `install.sh` (macOS/Linux) and `install.ps1` use `uv tool install`. The repo is public, so the GitHub-hosted runners are free.

## Goals / Non-Goals

**Goals:** reproducible one-dir builds for three OSes; a smoke test of each artifact in CI; free macOS distribution.
**Non-Goals:** code signing or notarization; auto-update; native installers (MSI/DMG/AppImage); Homebrew tap; bundling Chrome.

## Decisions

- **PyInstaller one-dir** (ADR 0003). One spec file for all OSes, with `sys.platform` branches only for icons and the bundle name. `collect_submodules("keyring.backends")`, `collect_data_files("buysmarter", includes=["alembic/**","gui/theme/**"])`, and `collect_dynamic_libs("curl_cffi")`. Migrations are packaged as package data, so the self-test's auto-migrate finds them.
- **`--self-test`** lives in `gui/app.py`: `QT_QPA_PLATFORM=offscreen`, a temp store dir, `keyring.set_keyring(InMemory)`, migrate to head, build the main window, then quit with exit code 0 or 1. It runs on each job against the built binary, not against the source tree.
- **Release wiring.** `release.yml` calls `desktop.yml` (`workflow_call`) after its tests, and creates the GitHub Release once, after PyPI and every desktop build passed, with the wheel and the archives together. A release therefore never exists without its wheel, which `install.sh` downloads from the latest release. A failed self-test fails the run and nothing is released. `workflow_dispatch` on `desktop.yml` builds without uploading, for dry runs.
- **Archive naming.** `buysmarter-<version>-<os>-<arch>.{tar.gz|zip}` and `buysmarter-<version>-macos-<arch>.app.zip`. On macOS, the runner's arch is `arm64`. An Intel build is skipped until asked for.
- **Prerequisite checks** are deferred to the running app's doctor rows (Chrome, keyring), with no installer-time checks.

## Risks / Trade-offs

- [Risk] PyInstaller misses a dynamic import (pydantic plugins, fastmcp) → the self-test imports the full core, and a failing import fails the job.
- [Risk] Linux builds depend on the runner's glibc → build on `ubuntu-22.04` for wider compatibility, documented as the minimum.
- [Risk] Unsigned Windows binaries trigger SmartScreen → the install docs show "More info → Run anyway". This is accepted under the no-paid-signing constraint.
- [Risk] Bundle size grows with Qt → exclude unused Qt modules (WebEngine, 3D, Multimedia) in the spec, and log the archive size in the job summary.
