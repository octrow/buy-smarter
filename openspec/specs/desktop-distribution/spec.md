# desktop-distribution Specification

## Purpose
Defines how a person without Python obtains and starts the buy-smarter desktop app on Linux, Windows and macOS, and what every tagged release must contain, so distribution stays free of paid signing while being honest about the extra steps it costs the user.

## Requirements

### Requirement: Every release ships a desktop build per OS

Each tagged release SHALL include a desktop build for Linux, Windows and macOS. Each build SHALL pass a self-test before it is attached.

#### Scenario: Tagging a release

- **WHEN** a `v*` tag is pushed
- **THEN** the release page lists a Linux archive, a Windows archive and a macOS `.app` archive
- **AND** each was produced by a job whose self-test passed

#### Scenario: A build fails its self-test

- **WHEN** one OS build's self-test fails
- **THEN** that build is not attached, and the workflow run is marked failed

### Requirement: Starts without Python installed

An unpacked build SHALL start the GUI on a machine with no Python and no buy-smarter installed. Only the documented OS prerequisites are needed: Google Chrome for Ozon, plus a desktop credential store for saving keys.

#### Scenario: Fresh Windows machine

- **WHEN** a user unzips the Windows build and runs it
- **THEN** the main window opens and a Kaspi search works

### Requirement: macOS without paid signing

The macOS route SHALL work without Apple Developer membership. The install docs SHALL give `install.sh` as the recommended path and SHALL document the steps for opening the unsigned `.app`.

#### Scenario: Recommended Mac install

- **WHEN** a Mac user runs `install.sh` and then `buysmarter gui`
- **THEN** the app opens without a Gatekeeper prompt

#### Scenario: Unsigned app

- **WHEN** a Mac user opens the downloaded `.app` and macOS blocks it
- **THEN** the install docs explain the System Settings → Privacy & Security → Open Anyway steps

### Requirement: Script installs include the GUI

`install.sh` and `install.ps1` SHALL install the GUI, and SHALL tell the user how to launch it.

#### Scenario: Script install

- **WHEN** the installer finishes
- **THEN** `buysmarter gui` is available, and the installer's last lines show that command
