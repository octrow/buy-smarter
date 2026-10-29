# desktop-installers Specification

## Purpose
Defines the native installer each OS gets for the buy-smarter desktop app, what it installs where, and how a
release lists the downloads, so a non-developer installs the app the way their system expects.

## Requirements

### Requirement: An Ubuntu package that installs like any other app

Each release SHALL include a `.deb` for Ubuntu and Debian on amd64. Installing it SHALL put the app in the
application menu with its icon, and SHALL install the system libraries the app needs to show its window.

#### Scenario: Installing the package

- **WHEN** a user runs `sudo apt install ./buysmarter_<version>_amd64.deb` on Ubuntu 22.04 or later
- **THEN** apt installs the app and the libraries it depends on
- **AND** buy-smarter appears in the application menu with its icon and opens from there

#### Scenario: Removing the package

- **WHEN** the user removes the package
- **THEN** the app and its menu entry are gone, and the user's store and keys stay

### Requirement: Installers for Windows and macOS

Each release SHALL include a Windows setup program that installs without administrator rights and adds a
Start-menu entry and an uninstaller, and a macOS disk image holding the app and a link to Applications.

#### Scenario: Windows setup

- **WHEN** a user runs the setup program and accepts the defaults
- **THEN** the app is installed for that user, a Start-menu entry opens it, and it can be uninstalled from Settings

#### Scenario: macOS disk image

- **WHEN** a user opens the `.dmg`
- **THEN** it shows the app next to an Applications link to drag it onto

### Requirement: Every installer is tested before it is attached

The release workflow SHALL install each installer on its build runner and run the installed app's self-test. A
failing installer SHALL fail the run, and no release SHALL be created.

#### Scenario: A broken package

- **WHEN** the installed app fails its self-test on one OS
- **THEN** the workflow run fails and no release is created

### Requirement: Release notes say what changed and what to download

The release notes SHALL contain the version's CHANGELOG section and a table that names the file to download for
each system.

#### Scenario: Tag without a changelog entry

- **WHEN** a version tag is pushed and CHANGELOG.md has no section for that version
- **THEN** the release workflow fails before anything is published
