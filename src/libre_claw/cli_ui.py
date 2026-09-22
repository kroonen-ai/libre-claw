# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Compact terminal presentation, separate from command and machine output."""

from __future__ import annotations

import io
import os
import shutil
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from libre_claw import __version__


def terminal_color(explicit: bool | None = None) -> bool:
    if "NO_COLOR" in os.environ or os.environ.get("TERM", "").lower() == "dumb":
        return False
    return explicit if explicit is not None else click.get_text_stream("stdout").isatty()


def _styled(value: str, ctx: click.Context, *, accent: bool = False) -> str:
    if not terminal_color(ctx.color):
        return value
    return click.style(value, bold=True, fg=(217, 140, 124) if accent else None)


class LibreGroup(click.Group):
    """Group the main commands by the work they do; retain Click's parsing."""

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_paragraph()
        formatter.write_text(_styled(f"LIBRE CLAW  {__version__}", ctx, accent=True))
        formatter.write_text("Run without a command to open the terminal UI.")

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands = {
            name: self.get_command(ctx, name)
            for name in self.list_commands(ctx)
        }
        groups = (
            ("Work", ("tui", "chat", "run", "workflow")),
            ("Services", ("status", "start", "stop", "shutdown", "restart", "daemon", "telegram")),
            ("Setup & extensions", ("auth", "config", "workspace", "cordis", "searx", "update")),
        )
        shown: set[str] = set()
        for heading, names in (*groups, ("More", tuple(commands))):
            rows = []
            for name in names:
                command = commands.get(name)
                if command is None or command.hidden or name in shown:
                    continue
                shown.add(name)
                rows.append((_styled(name, ctx), command.get_short_help_str(limit=max(24, formatter.width - 18))))
            if rows:
                with formatter.section(_styled(heading, ctx, accent=True)):
                    formatter.write_dl(rows)

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        with formatter.section(_styled("Quick start", ctx, accent=True)):
            examples = [
                ("libre-claw", "Open the terminal UI"),
                ('libre-claw run "Review changes"', "Run one task"),
                ("libre-claw status", "Check the local setup"),
            ]
            if formatter.width >= 65:
                formatter.write_dl(examples, col_max=40)
            else:
                for command, description in examples:
                    formatter.write_text(command)
                    with formatter.indentation():
                        formatter.write_text(description)
        formatter.write_paragraph()
        formatter.write_text("Use COMMAND --help for options. In the terminal UI, Ctrl+P opens the command palette.")


def _display(value: object) -> str:
    # Values such as paths and model IDs are data, not terminal control codes.
    return "".join(character if character.isprintable() else f"\\u{ord(character):04x}" for character in str(value))


def status_text(payload: dict[str, Any], *, width: int | None = None, color: bool | None = None) -> str:
    stream = io.StringIO()
    console = Console(file=stream, width=max(24, width or shutil.get_terminal_size((80, 24)).columns),
        force_terminal=terminal_color(color), color_system="auto", markup=False, highlight=False)
    console.print(Text.assemble(("LIBRE CLAW", "bold"), (f"  {payload['version']}", "dim")))
    console.print()
    daemon = payload["daemon"]
    rows = [
        ("Workspace", payload["workspace"]),
        ("Model", f"{payload['provider']} / {payload['model'] or 'not selected'}"),
        ("Theme", payload["theme"]),
        ("Daemon", daemon["state"]),
        ("Active turns", daemon["active_runs"] if daemon["active_runs"] is not None else "-"),
        ("Dashboard", daemon["dashboard_url"] or "not configured"),
        ("Log", payload["log_path"]),
    ]
    rows.extend(("Config" if index == 0 else "", path) for index, path in enumerate(payload["config_sources"]))
    if console.width < 48:
        for label, value in rows:
            if label:
                console.print(Text(label, style="bold"))
            console.print(Text(_display(value)), overflow="fold")
    else:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column(overflow="fold")
        for label, value in rows:
            table.add_row(Text(label), Text(_display(value), style="bold" if label == "Daemon" else ""))
        console.print(table)
    console.print()
    if not daemon["dashboard_url"]:
        console.print(Text("Check [daemon].host and port in your configuration.", style="dim"))
    elif daemon["state"] != "online":
        console.print(Text("Start the daemon: libre-claw start --detach", style="dim"))
    console.print(Text("Model and config describe this CLI context. Use --json for structured output.", style="dim"))
    return "\n".join(line.rstrip() for line in stream.getvalue().splitlines()).rstrip()
