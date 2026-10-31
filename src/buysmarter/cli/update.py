"""``update``: the version and its updates, the same as the app's «О программе»."""

from dataclasses import replace
from typing import Annotated

import typer
from rich.markup import escape

from buysmarter.cli.console import console
from buysmarter.services import update as updates
from buysmarter.services.update import INSTALL_LABELS, Channel, UpdateError

CHANNEL_LABELS: dict[Channel, str] = {"code": "код (ветка main)", "releases": "релизы"}


def update(
    check_only: Annotated[bool, typer.Option("--check", help="Only say what is new.")] = False,
    channel: Annotated[
        str | None, typer.Option(help="Follow: code (the main branch) or releases. Saved.")
    ] = None,
    auto: Annotated[
        bool | None, typer.Option("--auto/--no-auto", help="Let the desktop app update itself.")
    ] = None,
) -> None:
    """Update buy-smarter from GitHub: the main branch or the latest release."""
    install = updates.detect_install()
    settings = updates.load_settings(install)
    if channel is not None:
        if channel not in updates.channels_for(install):
            allowed = ", ".join(updates.channels_for(install))
            raise typer.BadParameter(f"this install follows: {allowed}", param_hint="--channel")
        settings = replace(settings, channel=channel)
    if auto is not None:
        settings = replace(settings, auto=auto)
    if channel is not None or auto is not None:
        updates.save_settings(settings)
    console.print(
        f"Установка: {INSTALL_LABELS[install]} · канал: {CHANNEL_LABELS[settings.channel]}"
        f" · автообновление: {'вкл' if settings.auto else 'выкл'}"
    )
    try:
        status = updates.check(settings, install)
        console.print(f"Версия: {escape(status.current)}")
        if check_only or not status.available:
            console.print(escape(status.note))
            return
        console.print(escape(updates.apply(settings, install)))
    except UpdateError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
