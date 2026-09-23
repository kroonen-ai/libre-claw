# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Inspect the shared Cordis engine or verify the local offline runtime."""

import asyncio
import json

import click

from libre_claw.config import load_config
from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.daemon import DaemonClient, daemon_base_url


@click.group("engine")
def engine_group() -> None:
    """Inspect Cordis services and check the offline runtime."""


@engine_group.command("status")
@click.pass_context
def status_command(ctx: click.Context) -> None:
    """Show the running daemon's service graph without starting a task."""
    obj = ctx.find_root().obj or {}
    config = load_config(config_path=obj.get("config_path"), working_directory=obj.get("working_directory"))
    try:
        value = asyncio.run(DaemonClient(daemon_base_url(config)).engine_status())
        click.echo(json.dumps(value, indent=2))
    except Exception as exc:
        raise click.ClickException(f"Could not inspect the daemon engine: {exc}") from exc


@engine_group.command("check")
def check_command() -> None:
    """Start and stop an isolated offline engine to verify local prerequisites."""
    async def check() -> dict:
        async with CordisEngine() as engine:
            return await engine.inspect()
    try:
        click.echo(json.dumps(asyncio.run(check()), indent=2))
    except (OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
