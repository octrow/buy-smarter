"""A clarification in the terminal: the question, numbered options, the user's reply."""

import typer
from rich.console import Console
from rich.markup import escape

from buysmarter.services.understanding import Clarification

CLARIFY_PROMPT = "Номер варианта или свой ответ (Enter — первый вариант)"
# Exit code of a run that stopped at a question off a terminal (README: exit codes).
EXIT_CLARIFY = 3


def clarification_lines(clarification: Clarification) -> list[str]:
    """The warning, the question and the numbered options, as plain lines."""
    lines = [f"⚠ {clarification.restricted_note}"] if clarification.restricted_note else []
    lines.append(clarification.question)
    lines += [f"  {n}. {option}" for n, option in enumerate(clarification.options, 1)]
    return lines


def print_clarification(console: Console, clarification: Clarification) -> None:
    for line in clarification_lines(clarification):
        console.print(f"[yellow]{escape(line)}[/yellow]")


def ask(console: Console, clarification: Clarification) -> str | None:
    """Ask on the terminal: an option by its number, the user's own text, or None (Enter)."""
    print_clarification(console, clarification)
    reply = typer.prompt(CLARIFY_PROMPT, default="", show_default=False).strip()
    options = clarification.options
    if reply.isdigit() and 1 <= int(reply) <= len(options):
        return options[int(reply) - 1]
    return reply or None
