## Purpose

Defines which browser buy-smarter uses to reach Ozon.kz behind its anti-bot challenge, in what order it picks one, and how the absence of a usable browser is reported, so the desktop build needs no bundled Chromium.

## ADDED Requirements

### Requirement: Prefer the system Chrome

When Google Chrome is installed on the machine, buy-smarter SHALL use it for Ozon instead of a bundled browser.

#### Scenario: Chrome installed, no bundled Chromium

- **WHEN** Google Chrome is installed and Playwright's Chromium is not
- **THEN** an Ozon search runs and returns offers

#### Scenario: Both available

- **WHEN** both Google Chrome and Playwright's Chromium are installed
- **THEN** Ozon uses Google Chrome

### Requirement: Fall back, then switch Ozon off

Without Google Chrome, buy-smarter SHALL use Playwright's bundled Chromium when present. With neither, it SHALL skip Ozon and still return results from the other marketplaces.

#### Scenario: Only bundled Chromium

- **WHEN** Chrome is absent and Playwright's Chromium is installed
- **THEN** Ozon runs on the bundled Chromium

#### Scenario: No browser at all

- **WHEN** neither browser is available
- **THEN** a recommendation completes with Kaspi and OLX results, and the report states that Ozon was skipped for lack of a browser

### Requirement: Ozon setup uses the same browser

The interactive Ozon setup that passes the anti-bot check SHALL open the same browser that searches will use.

#### Scenario: Setup with system Chrome

- **WHEN** Chrome is installed and the user runs Ozon setup
- **THEN** a visible Chrome window opens, and the saved session is the one later searches reuse

### Requirement: Doctor names the Ozon browser

`doctor` SHALL report which browser Ozon will use, or that none is available, with an install hint.

#### Scenario: No browser

- **WHEN** neither browser is available
- **THEN** `doctor` marks Ozon as off and suggests installing Google Chrome
