# Installing the desktop app

[Русская версия](install-desktop.ru.md)

There are two ways to get the buy-smarter window: the install script, or a ready-made build from the
[releases page](https://github.com/octrow/buy-smarter/releases/latest). Both are free and neither needs
Python on your machine.

| Your system | Recommended | Also possible |
|---|---|---|
| macOS | install script | `buysmarter-<version>-macos-arm64.app.zip` (unsigned, see below) |
| Windows | `buysmarter-<version>-windows-x64.zip` | install script |
| Ubuntu / Debian | `buysmarter_<version>_amd64.deb` | install script |
| Other Linux | install script | `buysmarter-<version>-linux-x64.tar.gz` |

## Install script

macOS / Linux, in Terminal:

```bash
curl -LsSf https://raw.githubusercontent.com/octrow/buy-smarter/main/install.sh | sh
```

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/octrow/buy-smarter/main/install.ps1 | iex"
```

When it finishes, open the app with:

```bash
buysmarter gui
```

On a Mac this is the recommended route: nothing is downloaded through the browser, so macOS does not ask
about an unknown developer. Running the script again updates the app.

## Ubuntu and Debian: the .deb package

Download `buysmarter_<version>_amd64.deb` and open it (App Center installs it), or in Terminal:

```bash
sudo apt install ./buysmarter_<version>_amd64.deb
```

buy-smarter then appears in the app menu. apt also installs the system libraries the window needs.
Ubuntu 22.04 or later, Debian 12 or later. To remove it: `sudo apt remove buysmarter` (your history, settings and keys stay).
To update, install the newer `.deb` the same way.

## Ready-made build

Download the archive for your system, unpack it anywhere (your home folder is fine), and start:

- **Windows:** `buysmarter\buysmarter.exe`.
- **Linux:** `buysmarter/buysmarter`. Built on Ubuntu 22.04; older distributions may lack the glibc it needs.
  If it stops with `Could not load the Qt platform plugin "xcb"`, install `libxcb-cursor0`
  (`sudo apt install libxcb-cursor0` on Ubuntu and Debian).
- **macOS:** `buysmarter.app`.

The builds are not signed: signing costs a yearly fee this project does not pay. Your system will warn
you the first time.

**Windows SmartScreen.** If "Windows protected your PC" appears, click **More info**, then **Run anyway**.

**macOS Gatekeeper.** If macOS says the app "cannot be opened because the developer cannot be verified":

1. Click **Done** (not "Move to Trash").
2. Open **System Settings → Privacy & Security**.
3. Scroll down to the message about `buysmarter.app` and click **Open Anyway**.
4. Confirm with your password. From then on the app opens normally.

## What else you need

- **Google Chrome**, for Ozon.kz. The app uses the Chrome you already have and does not ship its own
  browser. Without Chrome, Kaspi and OLX still work and the report says that Ozon was skipped. The first
  time, open **Настройки** (Settings) and click **Пройти проверку Ozon**: it opens Chrome once to pass
  Ozon's anti-bot page.
- **A Kazakhstan internet connection** for Kaspi.kz. From abroad or a datacenter Kaspi refuses requests.
- **Optional: an AI key.** Paste it under **AI-ключ** in **Настройки** to get request parsing and review summaries.
  The key goes to your system's password store (Keychain, Windows Credential Manager, or the Linux keyring),
  never into a file. Without a key the app still works and says what it skipped.

**Настройки** shows what is set up and what each missing piece turns off.
