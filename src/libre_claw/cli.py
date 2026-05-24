# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import click

from libre_claw import __version__
from libre_claw.auth.api_keys import ApiKeyStore, KeyStorageError
from libre_claw.config import (
    ConfigError,
    LibreClawConfig,
    default_config_path,
    load_config,
    packaged_default_config_text,
    user_config_path,
)
from libre_claw.telegram.bot import TelegramBot
from libre_claw.tui.app import LibreClawApp


def _raise_click_error(message: str) -> NoReturn:
    raise click.ClickException(message)


def _load_context_config(ctx: click.Context) -> LibreClawConfig:
    obj = ctx.obj or {}
    try:
        return load_config(config_path=obj.get("config_path"), working_directory=obj.get("working_directory"))
    except ConfigError as exc:
        _raise_click_error(str(exc))


@click.group(
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a Libre Claw TOML config file.",
)
@click.option(
    "--working-directory",
    type=click.Path(file_okay=False, path_type=Path),
    help="Working directory for the Libre Claw session.",
)
@click.version_option(__version__, prog_name="libre-claw")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, working_directory: Path | None) -> None:
    """Launch Libre Claw, a terminal-native coding agent harness.

    Running without a subcommand opens the Textual TUI. Use `auth` to manage
    provider keys, `config` to inspect defaults, or `telegram` for the daemon.
    """
    if ctx.invoked_subcommand is not None:
        ctx.obj = {"config_path": config_path, "working_directory": working_directory}
        return
    try:
        config = load_config(config_path=config_path, working_directory=working_directory)
    except ConfigError as exc:
        _raise_click_error(str(exc))

    app = LibreClawApp(config=config)
    app.run()


@main.command("telegram")
@click.pass_context
def telegram_command(ctx: click.Context) -> None:
    """Run Libre Claw as a standalone Telegram bot daemon."""
    config = _load_context_config(ctx)

    bot = TelegramBot(config)
    import asyncio

    try:
        asyncio.run(bot.run())
    except RuntimeError as exc:
        _raise_click_error(str(exc))


@main.group("config")
@click.pass_context
def config_command(ctx: click.Context) -> None:
    """Inspect config paths and bundled defaults."""
    ctx.ensure_object(dict)


@config_command.command("paths")
def config_paths() -> None:
    """Show the repo default path and per-user config path."""
    click.echo(f"repo_default: {default_config_path()}")
    click.echo(f"user_config: {user_config_path()}")


@config_command.command("defaults")
def config_defaults() -> None:
    """Print the bundled default TOML config."""
    click.echo(packaged_default_config_text(), nl=False)


@main.group("auth")
@click.pass_context
def auth_command(ctx: click.Context) -> None:
    """Manage stored provider API keys."""
    ctx.ensure_object(dict)


@auth_command.command("set-key")
@click.argument("provider")
@click.option("--api-key", help="API key value. Omit to enter it securely.")
@click.pass_context
def auth_set_key(ctx: click.Context, provider: str, api_key: str | None) -> None:
    """Store an API key in keyring or the encrypted fallback file."""
    config = _load_context_config(ctx)
    value = api_key or click.prompt(f"{provider} API key", hide_input=True, confirmation_prompt=True)
    store = ApiKeyStore.from_config(config.auth)
    try:
        location = store.set_api_key(provider, value)
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    click.echo(f"Stored {provider} API key in {location.replace('_', ' ')}.")


@auth_command.command("delete-key")
@click.argument("provider")
@click.pass_context
def auth_delete_key(ctx: click.Context, provider: str) -> None:
    """Delete a stored provider API key."""
    config = _load_context_config(ctx)
    store = ApiKeyStore.from_config(config.auth)
    try:
        removed = store.delete_api_key(provider)
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    if removed:
        click.echo(f"Deleted stored {provider} API key.")
    else:
        click.echo(f"No stored {provider} API key found.")


@auth_command.command("status")
@click.pass_context
def auth_status(ctx: click.Context) -> None:
    """Show whether configured provider keys are available without printing them."""
    config = _load_context_config(ctx)
    store = ApiKeyStore.from_config(config.auth)
    providers = [
        (name, _provider_api_key_env(provider_config))
        for name, provider_config in config.providers.items()
        if name in {"anthropic", "openai", "local"}
    ]
    try:
        statuses = store.key_status(providers)
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    for name in sorted(statuses):
        click.echo(f"{name}: {statuses[name]}")


def _provider_api_key_env(provider_config: object) -> str | None:
    if isinstance(provider_config, dict):
        value = provider_config.get("api_key_env")
        if isinstance(value, str):
            return value
    return None
