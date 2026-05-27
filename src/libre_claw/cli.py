# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import click

from libre_claw import __version__
from libre_claw.auth.api_keys import ApiKeyStore, KeyStorageError
from libre_claw.auth.codex import CodexCliError, CodexCommandResult, codex_logout, codex_status, stream_codex_command
from libre_claw.config import (
    ConfigError,
    LibreClawConfig,
    configure_telegram,
    default_config_path,
    global_config_path,
    load_config,
    packaged_default_config_text,
    set_global_default_model,
    user_config_path,
)
from libre_claw.core.workspace import (
    default_claw_workspace_path,
    initialize_claw_workspace,
    workspace_result_text,
    workspace_status_text,
)
from libre_claw.daemon import DaemonServer
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
    ctx.obj = {"config_path": config_path, "working_directory": working_directory}
    if ctx.invoked_subcommand is not None:
        return
    _run_tui(ctx)


@main.command("tui")
@click.pass_context
def tui_command(ctx: click.Context) -> None:
    """Open the Libre Claw terminal UI."""
    _run_tui(ctx)


@main.command("chat")
@click.pass_context
def chat_command(ctx: click.Context) -> None:
    """Open the Libre Claw chat TUI."""
    _run_tui(ctx)


def _run_tui(ctx: click.Context) -> None:
    config = _load_context_config(ctx)
    app = LibreClawApp(config=config)
    app.run()


@main.group("telegram", invoke_without_command=True)
@click.pass_context
def telegram_command(ctx: click.Context) -> None:
    """Set up or run the Telegram bot bridge."""
    if ctx.invoked_subcommand is not None:
        return
    _run_telegram_bot(ctx)


@telegram_command.command("run")
@click.pass_context
def telegram_run_command(ctx: click.Context) -> None:
    """Run only the Telegram bot bridge."""
    _run_telegram_bot(ctx)


@telegram_command.command("up")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.pass_context
def telegram_up_command(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Run the local daemon and Telegram bot together."""
    config = _load_context_config(ctx)
    config = replace(config, telegram=replace(config.telegram, enabled=True, use_daemon=True))
    click.echo(f"Starting Libre Claw daemon on http://{host or config.daemon.host}:{port or config.daemon.port}")
    click.echo("Starting Telegram bridge. Press Ctrl+C to stop both.")
    try:
        asyncio.run(_run_telegram_stack(config, host=host, port=port))
    except RuntimeError as exc:
        _raise_click_error(str(exc))
    except KeyboardInterrupt:
        click.echo("Stopped Libre Claw Telegram stack.")


@telegram_command.command("setup")
@click.option("--bot-token", help="Telegram bot token. Omit to enter it securely.")
@click.option("--user-id", "user_ids", type=int, multiple=True, help="Allowed Telegram numeric user ID. Can be repeated.")
@click.option("--no-daemon", is_flag=True, help="Do not route Telegram runs through the local daemon.")
@click.option("--provider", help="Optional default provider to persist, for example openrouter.")
@click.option("--model", help="Optional default model to persist, for example qwen/qwen3.7-max.")
@click.pass_context
def telegram_setup_command(
    ctx: click.Context,
    bot_token: str | None,
    user_ids: tuple[int, ...],
    no_daemon: bool,
    provider: str | None,
    model: str | None,
) -> None:
    """Configure Telegram in one guided command."""
    config = _load_context_config(ctx)
    token = bot_token or click.prompt("Telegram bot token from @BotFather", hide_input=True, confirmation_prompt=True)
    if not user_ids:
        user_ids = (click.prompt("Your numeric Telegram user ID", type=int),)

    store = ApiKeyStore.from_config(config.auth)
    try:
        location = _set_api_key_verified(store, "telegram", token)
        path = configure_telegram(
            tuple(user_ids),
            enabled=True,
            use_daemon=not no_daemon,
            bot_token_env=config.telegram.bot_token_env,
            config_path=global_config_path(config),
        )
        if provider and model:
            set_global_default_model(provider, model, config_path=path)
    except (ConfigError, KeyStorageError) as exc:
        _raise_click_error(str(exc))

    click.echo(f"Stored Telegram bot token in {location.replace('_', ' ')}.")
    click.echo(f"Updated Telegram config at {path}.")
    click.echo("Next: run `libre-claw telegram up` and message your bot /start.")


@telegram_command.command("allow")
@click.argument("user_id", type=int)
@click.pass_context
def telegram_allow_command(ctx: click.Context, user_id: int) -> None:
    """Allow one Telegram numeric user ID without changing the bot token."""
    config = _load_context_config(ctx)
    allowed = set(config.telegram.allowed_user_ids)
    config_path = global_config_path(config)
    if allowed == {123456789} and not config_path.exists():
        allowed.clear()
    was_allowed = user_id in allowed
    allowed.add(user_id)
    path = configure_telegram(
        tuple(sorted(allowed)),
        enabled=True,
        use_daemon=config.telegram.use_daemon,
        bot_token_env=config.telegram.bot_token_env,
        config_path=config_path,
    )
    if was_allowed:
        click.echo(f"Telegram user ID {user_id} was already allowed.")
    else:
        click.echo(f"Allowed Telegram user ID {user_id}.")
    click.echo(f"Updated Telegram config at {path}.")
    click.echo("Restart `libre-claw telegram up` so the running bot reloads the allowlist.")


@telegram_command.command("status")
@click.pass_context
def telegram_status_command(ctx: click.Context) -> None:
    """Show Telegram readiness without printing secrets."""
    config = _load_context_config(ctx)
    store = ApiKeyStore.from_config(config.auth)
    try:
        token_source = store.get_api_key("telegram", config.telegram.bot_token_env).source
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    click.echo(f"enabled: {config.telegram.enabled}")
    click.echo(f"use_daemon: {config.telegram.use_daemon}")
    click.echo(f"allowed_user_ids: {list(config.telegram.allowed_user_ids)}")
    click.echo(f"bot_token: {token_source}")
    click.echo(f"daemon: http://{config.daemon.host}:{config.daemon.port}")


def _run_telegram_bot(ctx: click.Context) -> None:
    config = _load_context_config(ctx)

    bot = TelegramBot(config)

    try:
        asyncio.run(bot.run())
    except RuntimeError as exc:
        _raise_click_error(str(exc))


async def _run_telegram_stack(config: LibreClawConfig, host: str | None = None, port: int | None = None) -> None:
    server = DaemonServer(config, start_telegram_bridge=False)
    server_task = asyncio.create_task(server.run(host=host, port=port), name="libre-claw-daemon")
    await asyncio.sleep(0.25)
    bot_task = asyncio.create_task(TelegramBot(config).run(), name="libre-claw-telegram")
    tasks = {server_task, bot_task}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@main.command("daemon")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.pass_context
def daemon_command(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Run the local background runner daemon."""
    config = _load_context_config(ctx)
    server = DaemonServer(config)
    base_url = f"http://{host or config.daemon.host}:{port or config.daemon.port}"
    click.echo(f"Libre Claw daemon listening on {base_url}")
    click.echo(f"Dashboard: {base_url}/dashboard")
    try:
        asyncio.run(server.run(host=host, port=port))
    except RuntimeError as exc:
        _raise_click_error(str(exc))


@main.group("workspace", invoke_without_command=True)
@click.pass_context
def workspace_command(ctx: click.Context) -> None:
    """Manage Libre Claw's dedicated runtime workspace."""
    if ctx.invoked_subcommand is not None:
        return
    config = _load_context_config(ctx)
    click.echo(workspace_status_text(config.general.working_directory))


@workspace_command.command("status")
@click.pass_context
def workspace_status_command(ctx: click.Context) -> None:
    """Show the current and default Libre Claw workspace paths."""
    config = _load_context_config(ctx)
    click.echo(workspace_status_text(config.general.working_directory))


@workspace_command.command("init")
@click.option(
    "--path",
    "target",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Workspace directory to create. Defaults to ~/Documents/.workspace/libre-claw.",
)
@click.option(
    "--source",
    "source_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source directory to copy soul/skill Markdown from. Defaults to the current configured working directory.",
)
@click.option("--set-default/--no-set-default", default=True, help="Persist this workspace as the default working directory.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing workspace Markdown files.")
@click.pass_context
def workspace_init_command(
    ctx: click.Context,
    target: Path | None,
    source_root: Path | None,
    set_default: bool,
    overwrite: bool,
) -> None:
    """Create Libre Claw's runtime workspace and copy Markdown context files."""
    config = _load_context_config(ctx)
    try:
        result = initialize_claw_workspace(
            source_root=source_root or config.general.working_directory,
            target=target or default_claw_workspace_path(),
            set_default=set_default,
            config_path=global_config_path(config),
            overwrite=overwrite,
        )
    except ConfigError as exc:
        _raise_click_error(str(exc))
    click.echo(workspace_result_text(result))


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
        location = _set_api_key_verified(store, provider, value)
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    click.echo(f"Stored {provider} API key in {location.replace('_', ' ')}.")


def _set_api_key_verified(store: ApiKeyStore, provider: str, value: str) -> str:
    """Store a provider key and prove a new process can read it back."""
    location = store.set_api_key(provider, value)
    lookup = store.get_api_key(provider)
    if lookup.value != value.strip():
        msg = (
            f"Could not verify the stored {provider} key. Libre Claw did not persist "
            "the credential, so restarting the app would lose access."
        )
        raise KeyStorageError(msg)
    return location


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
        if name in {"anthropic", "openai", "openrouter", "ollama"}
    ]
    providers.append(("telegram", config.telegram.bot_token_env))
    try:
        statuses = store.key_status(providers)
    except KeyStorageError as exc:
        _raise_click_error(str(exc))
    for name in sorted(statuses):
        click.echo(f"{name}: {statuses[name]}")
    status = asyncio.run(codex_status())
    click.echo(f"codex: {'logged_in' if status.logged_in else 'missing'}")


@auth_command.command("codex-login")
@click.option("--browser", "browser_login", is_flag=True, help="Use Codex's normal browser login instead of device auth.")
def auth_codex_login(browser_login: bool) -> None:
    """Log in to Codex/ChatGPT auth for the Codex-backed Libre Claw provider."""
    try:
        result = asyncio.run(_stream_codex_login(browser_login=browser_login))
    except CodexCliError as exc:
        _raise_click_error(str(exc))
    if result.exit_code != 0:
        _raise_click_error(f"Codex login exited with {result.exit_code}.")


@auth_command.command("codex-status")
def auth_codex_status() -> None:
    """Show Codex CLI login status without printing credentials."""
    status = asyncio.run(codex_status())
    click.echo(status.detail)


@auth_command.command("codex-logout")
def auth_codex_logout() -> None:
    """Log out of Codex/ChatGPT auth through the Codex CLI."""
    try:
        result = asyncio.run(codex_logout())
    except CodexCliError as exc:
        _raise_click_error(str(exc))
    if result.output:
        click.echo(result.output)
    if result.exit_code != 0:
        _raise_click_error(f"Codex logout exited with {result.exit_code}.")


def _provider_api_key_env(provider_config: object) -> str | None:
    if isinstance(provider_config, dict):
        value = provider_config.get("api_key_env")
        if isinstance(value, str):
            return value
    return None


async def _stream_codex_login(browser_login: bool) -> CodexCommandResult:
    args = ["codex", "login"]
    if not browser_login:
        args.append("--device-auth")

    final: CodexCommandResult | None = None
    async for event in stream_codex_command(args):
        if isinstance(event, CodexCommandResult):
            final = event
            continue
        click.echo(event.text, nl=False, err=event.stream == "stderr")

    if final is None:
        raise CodexCliError("Codex login ended without a result.")
    return final
