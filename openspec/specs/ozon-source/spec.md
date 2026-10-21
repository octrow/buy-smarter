# ozon-source Specification

## Purpose
Defines which browser buy-smarter uses to reach Ozon.kz behind its anti-bot challenge, in what order it picks one, and how the absence of a usable browser is reported, so the desktop build needs no bundled Chromium.

## Requirements

### Requirement: Prefer the system Chrome once Ozon setup has run

Headless Google Chrome passes Ozon's anti-bot check only with a session saved by Ozon setup (gate run of
2026-10-21). When Google Chrome is installed and a saved Ozon session exists, buy-smarter SHALL use Chrome for Ozon
instead of a bundled browser.

#### Scenario: Chrome and a saved session, no bundled Chromium

- **WHEN** Google Chrome is installed, a saved Ozon session exists, and Playwright's Chromium is not installed
- **THEN** an Ozon search runs on Chrome and returns offers

#### Scenario: Both browsers and a saved session

- **WHEN** both Google Chrome and Playwright's Chromium are installed and a saved Ozon session exists
- **THEN** Ozon uses Google Chrome

#### Scenario: Chrome without a saved session

- **WHEN** Google Chrome is installed, no saved Ozon session exists, and Playwright's Chromium is not installed
- **THEN** Ozon is skipped, and the report and `doctor` ask the user to run Ozon setup

### Requirement: Fall back, then switch Ozon off

Without Google Chrome, buy-smarter SHALL use Playwright's bundled Chromium when present. With neither, it SHALL skip Ozon and still return results from the other marketplaces.

#### Scenario: Only bundled Chromium

- **WHEN** Playwright's Chromium is installed and either Chrome is absent or no saved Ozon session exists
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

`doctor` SHALL report which browser Ozon will use, or why Ozon is off, with a hint for that case.

#### Scenario: Chrome in use

- **WHEN** Ozon will run on Google Chrome
- **THEN** `doctor` shows the Ozon browser row as ok with "Google Chrome"

#### Scenario: Chrome waiting for setup

- **WHEN** Google Chrome is installed, no saved Ozon session exists, and Playwright's Chromium is not installed
- **THEN** `doctor` marks Ozon as off and suggests running Ozon setup

#### Scenario: No browser

- **WHEN** neither browser is available
- **THEN** `doctor` marks Ozon as off and suggests installing Google Chrome
