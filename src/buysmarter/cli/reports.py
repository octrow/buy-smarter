"""``buysmarter reports``: past searches, as the app's «Прошлые поиски» and «Повторить».

The list, the labels and the query come from ``cli/report`` (``saved_reports``, ``report_parts``),
the same the screen uses; ``repeat`` runs ``recommend`` with the saved query.
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from buysmarter.cli.console import console
from buysmarter.cli.report import report_parts, report_text, saved_reports

NO_REPORTS = "Пока нет сохранённых поисков — отчёт сохраняется после каждого поиска."
HEADERS = ("№", "Когда", "Запрос")

reports_app = typer.Typer(
    help="Past searches, as the app's «Прошлые поиски»: list, show, repeat.",
    invoke_without_command=True,
)
_Number = Annotated[int, typer.Argument(help="The number in `buysmarter reports`.", min=1)]


@reports_app.callback()
def list_(ctx: typer.Context) -> None:
    """List the saved reports, newest first."""
    if ctx.invoked_subcommand is not None:
        return
    paths = saved_reports()
    if not paths:
        console.print(NO_REPORTS)
        return
    table = Table(*HEADERS, header_style="bold")
    for number, path in enumerate(paths, start=1):
        when, query = report_parts(path)
        table.add_row(str(number), when, escape(query))
    console.print(table)


@reports_app.command("show")
def show(number: _Number) -> None:
    """Print one saved report."""
    console.print(Markdown(report_text(_picked(number))))


@reports_app.command("repeat")
def repeat(ctx: typer.Context, number: _Number) -> None:
    """Search the saved report's query again, as «Повторить» in the app."""
    from buysmarter.cli import main  # main registers this group: imported here, not at the top

    ctx.invoke(main.recommend, query=report_parts(_picked(number))[1])


def _picked(number: int) -> Path:
    paths = saved_reports()
    if number > len(paths):
        raise typer.BadParameter(f"есть отчёты 1–{len(paths)}", param_hint="NUMBER")
    return paths[number - 1]
