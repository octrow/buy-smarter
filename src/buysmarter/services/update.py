"""Updates: which install this is, what GitHub has that is newer, and taking it.

Two update channels: ``code`` follows the main branch, ``releases`` the tagged releases. A git
checkout (``uv run`` from a clone) fast-forwards; a ``uv tool`` install reinstalls, as install.sh
does; the frozen desktop build and a plain pip install only point at the release page.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from buysmarter import __version__
from buysmarter.config import USER_ENV_FILE

logger = logging.getLogger(__name__)

REPO = "octrow/buy-smarter"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
_MAIN_SHA_URL = f"https://api.github.com/repos/{REPO}/commits/main"
_NETWORK_TIMEOUT_S = 15
_GIT_TIMEOUT_S = 120
_INSTALL_TIMEOUT_S = 600
_SHORT_SHA = 7
_ROOT = Path(__file__).resolve().parents[3]  # a checkout's root; site-packages' parent otherwise

type Channel = Literal["code", "releases"]
type Install = Literal["checkout", "uv-tool", "frozen", "pip"]
CHANNELS: tuple[Channel, ...] = ("code", "releases")
INSTALL_LABELS: dict[Install, str] = {
    "checkout": "git-клон",
    "uv-tool": "установщик (uv tool)",
    "frozen": "сборка для рабочего стола",
    "pip": "pip",
}


class UpdateError(Exception):
    """GitHub, git or uv failed; the message is for the user."""


@dataclass(frozen=True)
class UpdateSettings:
    channel: Channel
    auto: bool = True
    installed_sha: str = ""  # uv tool on the code channel: the main commit installed last


@dataclass(frozen=True)
class Status:
    current: str  # "0.11.1 (bd2f968)"
    latest: str  # what GitHub has: "0.12.0" or "c0ffee1"
    available: bool
    can_apply: bool  # False: the user updates by hand, ``note`` says how
    note: str  # one line for the screen and the CLI
    target: str = ""  # what ``apply`` takes: a git ref, a release version or a main commit


def detect_install(root: Path = _ROOT) -> Install:
    if getattr(sys, "frozen", False):
        return "frozen"
    if (root / ".git").exists():
        return "checkout"
    if (Path(sys.prefix) / "uv-receipt.toml").exists():
        return "uv-tool"
    return "pip"


def channels_for(install: Install) -> tuple[Channel, ...]:
    """The code channel needs git or uv to take a commit: the others follow releases only."""
    return CHANNELS if install in ("checkout", "uv-tool") else ("releases",)


def settings_file() -> Path:
    return Path(os.environ.get("BUYSMARTER_UPDATE_FILE") or USER_ENV_FILE.parent / "update.json")


def load_settings(install: Install) -> UpdateSettings:
    default = UpdateSettings(channel="code" if install == "checkout" else "releases")
    try:
        saved = json.loads(settings_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        logger.warning("Update settings unreadable (%r); using the defaults", exc)
        return default
    loaded = replace(
        default,
        channel=saved.get("channel", default.channel),
        auto=bool(saved.get("auto", default.auto)),
        installed_sha=str(saved.get("installed_sha", "")),
    )
    if loaded.channel not in channels_for(install):
        return replace(loaded, channel="releases")
    return loaded


def save_settings(settings: UpdateSettings) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings)), encoding="utf-8")


def latest_release() -> str:
    """The latest release's version: /releases/latest redirects to /releases/tag/vX.Y.Z."""
    try:
        with urllib.request.urlopen(f"{RELEASES_URL}/latest", timeout=_NETWORK_TIMEOUT_S) as resp:
            url = resp.geturl()
    except OSError as exc:
        raise UpdateError("GitHub недоступен: проверьте интернет.") from exc
    if match := re.search(r"/tag/v([^/]+)$", url):
        return match.group(1)
    raise UpdateError("На GitHub ещё нет релизов: выберите канал «Код».")


def main_sha() -> str:
    request = urllib.request.Request(
        _MAIN_SHA_URL, headers={"Accept": "application/vnd.github.sha"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT_S) as resp:
            body: bytes = resp.read()
            return body.decode().strip()
    except OSError as exc:
        raise UpdateError("GitHub недоступен: проверьте интернет.") from exc


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _run(args: list[str], timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"Не запустилось: {args[0]} ({exc}).") from exc


def _git(root: Path, *args: str) -> str:
    done = _run(["git", "-C", str(root), *args])
    if done.returncode != 0:
        logger.warning("git %s failed: %s", " ".join(args), done.stderr.strip())
        lines = done.stderr.strip().splitlines() or [f"git {args[0]}: код {done.returncode}"]
        raise UpdateError(f"git: {lines[-1]}")
    return done.stdout.strip()


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    return (
        _run(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer]).returncode == 0
    )


def _checkout_blocker(root: Path, target: str) -> str:
    """Why a fast-forward to ``target`` would not be safe here; empty when it is."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        return f"Открыта ветка {branch}, не main: обновите её сами."
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        return "Есть незакоммиченные изменения: обновление подождёт, пока их не закоммитят."
    if not _is_ancestor(root, "HEAD", target):
        return "Есть неотправленные коммиты: сначала git push или git pull --rebase."
    return ""


def _checkout_status(root: Path, channel: Channel) -> Status:
    current = f"{__version__} ({_git(root, 'rev-parse', '--short', 'HEAD')})"
    if channel == "code":
        _git(root, "fetch", "-q", "origin", "main")
        target = "origin/main"
        latest = _git(root, "rev-parse", "--short", target)
    else:
        latest = latest_release()
        target = f"v{latest}"
        _git(root, "fetch", "-q", "origin", "tag", target)
    if _is_ancestor(root, target, "HEAD"):
        return Status(current, latest, False, False, "Установлена последняя версия.", target)
    blocker = _checkout_blocker(root, target)
    note = blocker or f"Доступно обновление: {latest}."
    return Status(current, latest, True, not blocker, note, target)


def _uv_tool_status(channel: Channel, settings: UpdateSettings) -> Status:
    if channel == "code":
        sha = main_sha()
        latest, available = sha[:_SHORT_SHA], sha != settings.installed_sha
        current = f"{__version__} ({settings.installed_sha[:_SHORT_SHA] or 'коммит неизвестен'})"
    else:
        latest = sha = latest_release()
        available = _version_key(latest) > _version_key(__version__)
        current = __version__
    if not available:
        return Status(current, latest, False, False, "Установлена последняя версия.", sha)
    if sys.platform == "win32":
        # ponytail: Windows locks the running env's files; a helper that reinstalls after exit
        # would lift this.
        note = f"Доступно {latest}: закройте программу и запустите команду установки снова."
        return Status(current, latest, True, False, note, sha)
    if shutil.which("uv") is None:
        note = f"Доступно {latest}, но uv не найден: запустите команду установки снова."
        return Status(current, latest, True, False, note, sha)
    return Status(current, latest, True, True, f"Доступно обновление: {latest}.", sha)


def check(settings: UpdateSettings, install: Install | None = None, root: Path = _ROOT) -> Status:
    """What the channel has that this install lacks. Blocking (git, network): run off the GUI."""
    install = install or detect_install(root)
    if install == "checkout":
        return _checkout_status(root, settings.channel)
    if install == "uv-tool":
        return _uv_tool_status(settings.channel, settings)
    latest = latest_release()
    if _version_key(latest) <= _version_key(__version__):
        return Status(__version__, latest, False, False, "Установлена последняя версия.", latest)
    return Status(__version__, latest, True, False, f"Доступна {latest}: {RELEASES_URL}", latest)


def _uv_tool_spec(channel: Channel, target: str) -> str:
    if channel == "code":
        return f"buysmarter[gui] @ {REPO_URL}/archive/{target}.tar.gz"
    wheel = f"buysmarter-{target}-py3-none-any.whl"
    return f"buysmarter[gui] @ {RELEASES_URL}/download/v{target}/{wheel}"


def apply(settings: UpdateSettings, install: Install | None = None, root: Path = _ROOT) -> str:
    """Check again, then take the update; the line to show. The new code runs after a restart."""
    install = install or detect_install(root)
    status = check(settings, install, root)
    if not status.available or not status.can_apply:
        return status.note
    if install == "checkout":
        _git(root, "merge", "-q", "--ff-only", status.target)
    else:
        _reinstall(settings, status.target)
    return f"Обновлено до {status.latest}. Перезапустите программу."


def _reinstall(settings: UpdateSettings, target: str) -> None:
    """``uv tool install --reinstall`` at ``target``; the code channel remembers the commit."""
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    spec = _uv_tool_spec(settings.channel, target)
    done = _run(
        ["uv", "tool", "install", "--reinstall", "--python", python, spec], _INSTALL_TIMEOUT_S
    )
    if done.returncode != 0:
        logger.warning("uv tool install failed: %s", done.stderr.strip())
        raise UpdateError("uv не установил обновление: подробности в логе.")
    if settings.channel == "code":
        save_settings(replace(settings, installed_sha=target))


def restart_command(install: Install | None = None, root: Path = _ROOT) -> list[str]:
    """Start the GUI again: a checkout through ``uv run``, so new dependencies are synced first."""
    install = install or detect_install(root)
    if install == "frozen":
        return [sys.executable]
    uv = shutil.which("uv")
    if install == "checkout" and uv:
        return [uv, "run", "-q", "--project", str(root), "buysmarter-gui"]
    return [sys.executable, "-m", "buysmarter.gui"]
