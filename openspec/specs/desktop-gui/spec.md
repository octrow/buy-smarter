# desktop-gui Specification

## Purpose
Defines what the buy-smarter desktop app lets a non-developer do (search, read the explainable report, keep preferences, track prices, configure the app) and how it behaves while the recommendation core is working, so the app stays responsive, cancellable and honest about what it could not check.

## Requirements

### Requirement: Search with live progress

The Search screen SHALL accept a free-text query, an optional city, an optional budget and the marketplaces to use, and while the search runs it SHALL show progress per marketplace and pipeline stage.

#### Scenario: Running a search

- **WHEN** the user enters "ирригатор до 40000 Алматы" and starts the search
- **THEN** the window stays responsive, and each marketplace shows its state (waiting, running, done, skipped, failed) as the search proceeds
- **AND** the Report screen opens when the search finishes

#### Scenario: Preferences pre-fill

- **WHEN** a default city and budget are saved in preferences
- **THEN** the Search screen starts with them filled in

### Requirement: Cancel a running search

The user SHALL be able to cancel a running search. Cancelling SHALL stop the work, release browser and network resources, and leave the app ready for a new search.

#### Scenario: Cancel mid-search

- **WHEN** the user cancels while Ozon is still running
- **THEN** the search stops within a few seconds, no report is shown, and a new search can start right away

### Requirement: Explainable report

The Report screen SHALL show the best pick with its score, confidence, price and a per-axis breakdown with the reason for each axis. It SHALL also show the alternatives in a table that can be sorted and filtered by axis score, price and marketplace; the rejected offers with their reasons; used listings from OLX; and what could not be verified.

#### Scenario: Reading the best pick

- **WHEN** a search returns a best pick
- **THEN** the report shows each axis as score/maximum with its reason, in the same terms the CLI report uses

#### Scenario: Sorting alternatives

- **WHEN** the user sorts alternatives by price, or filters to a single marketplace
- **THEN** the table reorders or filters without re-running the search

#### Scenario: Opening an offer

- **WHEN** the user activates an offer
- **THEN** its marketplace page opens in the system web browser

#### Scenario: Nothing verifiable

- **WHEN** a marketplace was skipped, or LLM steps ran without LLM
- **THEN** the report's could-not-verify section names each skipped source or step and why

### Requirement: Preferences

The Preferences screen SHALL show and edit the saved preferences (default city, budget, excluded brands and the other keys the core supports). Saved values SHALL apply to the next search.

#### Scenario: Saving a preference

- **WHEN** the user saves a default city
- **THEN** the next search uses it, and it is still set after restarting the app

### Requirement: Price history

The Price history screen SHALL find a tracked product by text and show its recorded snapshots (date, minimum price, offer count, marketplace). It SHALL also offer to refresh all tracked categories, with progress.

#### Scenario: Viewing history

- **WHEN** the user searches history for a product that earlier searches recorded
- **THEN** its snapshots are listed newest first

#### Scenario: Refresh

- **WHEN** the user starts a refresh
- **THEN** it runs with progress and can be cancelled, and the new snapshots appear when it finishes

### Requirement: Settings and health

The Settings screen SHALL show the same health checks as `doctor` (store, LLM route, Ozon browser, Ozon session), each with its hint. It SHALL let the user set or clear an LLM key and start the visible Ozon check.

#### Scenario: Missing Ozon browser

- **WHEN** no browser for Ozon is available
- **THEN** Settings shows Ozon as off with the install hint, and Search shows Ozon as unavailable

#### Scenario: Entering a key

- **WHEN** the user enters an LLM key and saves it
- **THEN** the LLM row turns OK, and the key field no longer displays the key

### Requirement: Errors do not crash the app

A failure inside a search, refresh or setup SHALL be shown to the user as a message without internal details, and SHALL NOT close the app. The details go to the log file.

#### Scenario: Network failure

- **WHEN** a marketplace request fails during a search
- **THEN** that marketplace shows as failed, the other marketplaces' results are still reported, and the app keeps running

### Requirement: Light and dark appearance

The app SHALL follow the OS light or dark setting. Text and controls SHALL meet WCAG AA contrast in both.

#### Scenario: Dark OS theme

- **WHEN** the OS is set to dark
- **THEN** the app starts in its dark theme with every text and background pair at 4.5:1 or more
