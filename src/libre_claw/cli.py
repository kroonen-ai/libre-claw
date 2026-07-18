# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import errno
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NoReturn
from urllib.parse import urlparse

import click
import httpx

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
from libre_claw.core.searxng import (
    default_searxng_path,
    ensure_searxng_files,
    ensure_searxng_up,
    searxng_compose_command,
)
from libre_claw.core.workspace import (
    default_claw_workspace_path,
    initialize_claw_workspace,
    workspace_result_text,
    workspace_status_text,
)
from libre_claw.daemon import DaemonServer, daemon_base_url
from libre_claw.headless import run_headless
from libre_claw.telegram.bot import TelegramBot
from libre_claw.tui.app import LibreClawApp


PROCESS_STATE_NAME = "process.json"
PROCESS_LOG_NAME = "daemon.log"
PROCESS_MODES = {"daemon", "telegram-up", "telegram-run"}


@dataclass(frozen=True)
class ProcessStopResult:
    stopped: bool
    message: str
    pid: int | None = None
    mode: str | None = None


@dataclass(frozen=True)
class StartedProcess:
    pid: int
    base_url: str
    log_path: Path
    mode: str


@dataclass(frozen=True)
class UpdateResult:
    repo_root: Path
    local_head: str
    remote_head: str
    remote_ref: str
    backup_dir: Path | None
    updated: bool
    dry_run: bool = False


class UpdateError(RuntimeError):
    """Raised when the self-update command cannot safely continue."""


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
@click.option(
    "--mouse/--no-mouse",
    "tui_mouse",
    default=None,
    help="Enable or disable Textual mouse capture for the TUI. Disable it for native terminal text selection.",
)
@click.option(
    "--inline/--fullscreen",
    "tui_inline",
    default=None,
    help="Run the TUI inline in terminal scrollback or in the full-screen alternate screen.",
)
@click.version_option(__version__, prog_name="libre-claw")
@click.pass_context
def main(
    ctx: click.Context,
    config_path: Path | None,
    working_directory: Path | None,
    tui_mouse: bool | None,
    tui_inline: bool | None,
) -> None:
    """Launch Libre Claw, a terminal-native coding agent harness.

    Running without a subcommand opens the Textual TUI. Use `auth` to manage
    provider keys, `config` to inspect defaults, or `telegram` for the daemon.
    """
    ctx.obj = {
        "config_path": config_path,
        "working_directory": working_directory,
        "tui_mouse": tui_mouse,
        "tui_inline": tui_inline,
    }
    if ctx.invoked_subcommand is not None:
        return
    _run_tui(ctx)


@main.command("tui")
@click.option(
    "--mouse/--no-mouse",
    "mouse",
    default=None,
    help="Enable or disable Textual mouse capture. Disable it for native terminal text selection.",
)
@click.option(
    "--inline/--fullscreen",
    "inline",
    default=None,
    help="Run inline in terminal scrollback or in the full-screen alternate screen.",
)
@click.pass_context
def tui_command(ctx: click.Context, mouse: bool | None, inline: bool | None) -> None:
    """Open the Libre Claw terminal UI."""
    _run_tui(ctx, mouse=mouse, inline=inline)


@main.command("chat")
@click.option(
    "--mouse/--no-mouse",
    "mouse",
    default=None,
    help="Enable or disable Textual mouse capture. Disable it for native terminal text selection.",
)
@click.option(
    "--inline/--fullscreen",
    "inline",
    default=None,
    help="Run inline in terminal scrollback or in the full-screen alternate screen.",
)
@click.pass_context
def chat_command(ctx: click.Context, mouse: bool | None, inline: bool | None) -> None:
    """Open the Libre Claw chat TUI."""
    _run_tui(ctx, mouse=mouse, inline=inline)


@main.command("run")
@click.argument("message", required=False)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Allow all non-denied tools for this isolated noninteractive run.",
)
@click.option(
    "--system-prompt-extra",
    default="",
    help="Append instructions to the configured system prompt for this run.",
)
@click.option(
    "--trajectory-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write an ATIF v1.7 trajectory for this run.",
)
@click.option("--trajectory-agent-version", default=None, hidden=True)
@click.option("--trajectory-reasoning-effort", default=None, hidden=True)
@click.pass_context
def run_command(
    ctx: click.Context,
    message: str | None,
    auto_approve: bool,
    system_prompt_extra: str,
    trajectory_path: Path | None,
    trajectory_agent_version: str | None,
    trajectory_reasoning_effort: str | None,
) -> None:
    """Run one complete agent turn without opening the TUI."""
    prompt = message if message is not None else sys.stdin.read()
    if not prompt.strip():
        raise click.UsageError("Provide MESSAGE or pipe a prompt on stdin.")
    config = _load_context_config(ctx)
    wrote_text = False

    def stream_text(delta: str) -> None:
        nonlocal wrote_text
        click.echo(delta, nl=False)
        sys.stdout.flush()
        wrote_text = True

    result = asyncio.run(
        run_headless(
            config,
            prompt.strip(),
            auto_approve=auto_approve,
            system_prompt_extra=system_prompt_extra,
            on_text=stream_text,
            trajectory_path=trajectory_path,
            trajectory_agent_version=trajectory_agent_version,
            trajectory_reasoning_effort=trajectory_reasoning_effort,
        )
    )
    if wrote_text:
        click.echo()
    if result.error:
        raise click.ClickException(result.error)


def _run_tui(ctx: click.Context, *, mouse: bool | None = None, inline: bool | None = None) -> None:
    config = _load_context_config(ctx)
    obj = ctx.obj or {}
    mouse_enabled = mouse if mouse is not None else obj.get("tui_mouse")
    inline_enabled = inline if inline is not None else obj.get("tui_inline")
    if mouse_enabled is None:
        mouse_enabled = config.tui.mouse
    if inline_enabled is None:
        inline_enabled = config.tui.inline
    app = LibreClawApp(config=config)
    app.run(mouse=mouse_enabled, inline=inline_enabled)


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
    base_url = daemon_base_url(config, host=host, port=port)
    click.echo(f"Starting Libre Claw daemon on {base_url}")
    click.echo("Starting Telegram bridge. Press Ctrl+C to stop both.")
    try:
        with _registered_process("telegram-up", base_url):
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
        with _registered_process("telegram-run", daemon_base_url(config)):
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
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
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
    _run_daemon_process(ctx, host=host, port=port)


@main.command("start")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.option(
    "-d",
    "--detach",
    is_flag=True,
    help="Start in the background and return immediately. Also configurable as [daemon].detach.",
)
@click.pass_context
def start_command(ctx: click.Context, host: str | None, port: int | None, detach: bool) -> None:
    """Start the local background runner daemon."""
    _run_daemon_process(ctx, host=host, port=port, detach=detach, allow_config_detach=True)


def _run_daemon_process(
    ctx: click.Context,
    *,
    host: str | None,
    port: int | None,
    detach: bool = False,
    allow_config_detach: bool = False,
) -> None:
    config = _load_context_config(ctx)
    effective_detach = detach or (allow_config_detach and config.daemon.detach)
    base_url = daemon_base_url(config, host=host, port=port)
    running_url = _running_daemon_url(config, host=host, port=port)
    if running_url is not None:
        click.echo(f"Libre Claw daemon is already running at {running_url}")
        click.echo(f"Dashboard: {running_url}/dashboard")
        return

    _maybe_autostart_searxng(config)

    if effective_detach:
        started = _start_background_process(ctx, config, "daemon", host=host, port=port)
        healthy = _wait_for_daemon_health(started.base_url, timeout=10.0)
        if healthy:
            click.echo(f"Started Libre Claw daemon with pid {started.pid}.")
        else:
            click.echo(f"Started Libre Claw daemon with pid {started.pid}, but health is not ready yet.")
        click.echo(f"Dashboard: {started.base_url}/dashboard")
        click.echo(f"Log: {started.log_path}")
        return

    server = DaemonServer(config)
    click.echo(f"Libre Claw daemon listening on {base_url}")
    click.echo(f"Dashboard: {base_url}/dashboard")
    try:
        with _registered_process("daemon", base_url):
            asyncio.run(server.run(host=host, port=port))
    except RuntimeError as exc:
        _raise_click_error(str(exc))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            running_url = _running_daemon_url(config, host=host, port=port)
            if running_url is not None:
                click.echo(f"Libre Claw daemon is already running at {running_url}")
                click.echo(f"Dashboard: {running_url}/dashboard")
                return
            _raise_click_error(
                f"Could not start Libre Claw daemon because {base_url} is already in use. "
                "Run `libre-claw shutdown` if this is a stuck Libre Claw process."
            )
        raise
    except KeyboardInterrupt:
        click.echo("Stopped Libre Claw daemon.")


@main.command("shutdown")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.option("--timeout", type=float, default=10.0, show_default=True, help="Seconds to wait for shutdown.")
@click.option("--force", is_flag=True, help="Send SIGKILL if the process does not stop after SIGTERM.")
@click.pass_context
def shutdown_command(ctx: click.Context, host: str | None, port: int | None, timeout: float, force: bool) -> None:
    """Shut down a running Libre Claw daemon or Telegram stack from another terminal."""
    config = _load_context_config(ctx)
    result = _stop_lifecycle(config, host=host, port=port, timeout=timeout, force=force)
    click.echo(result.message)


@main.command("stop")
@click.argument("run_id", required=False)
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.option("--timeout", type=float, default=10.0, show_default=True, help="Seconds to wait for the daemon API.")
@click.pass_context
def stop_command(ctx: click.Context, run_id: str | None, host: str | None, port: int | None, timeout: float) -> None:
    """Stop the active daemon turn without shutting down Libre Claw."""
    config = _load_context_config(ctx)
    click.echo(_stop_active_turn(config, run_id=run_id, host=host, port=port, timeout=timeout))


@main.command("restart")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.option("--timeout", type=float, default=10.0, show_default=True, help="Seconds to wait for stop/start.")
@click.option("--force", is_flag=True, help="Force-kill a stuck previous process before restarting.")
@click.option(
    "--mode",
    type=click.Choice(["auto", "daemon", "telegram-up", "telegram-run"]),
    default="auto",
    show_default=True,
    help="Process to start after stopping. Auto reuses the previous process mode.",
)
@click.pass_context
def restart_command(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    timeout: float,
    force: bool,
    mode: str,
) -> None:
    """Restart the running Libre Claw daemon or Telegram stack in the background."""
    config = _load_context_config(ctx)
    state = _read_process_state()
    selected_mode = _selected_restart_mode(mode, state)
    stop_result = _stop_lifecycle(config, host=host, port=port, timeout=timeout, force=force)
    if not stop_result.stopped and stop_result.pid is not None:
        _raise_click_error(stop_result.message)
    started = _start_background_process(ctx, config, selected_mode, host=host, port=port)
    healthy = _wait_for_daemon_health(started.base_url, timeout=timeout) if selected_mode != "telegram-run" else True
    if healthy:
        click.echo(f"Restarted Libre Claw {started.mode} with pid {started.pid}.")
    else:
        click.echo(f"Started Libre Claw {started.mode} with pid {started.pid}, but health is not ready yet.")
    click.echo(f"Log: {started.log_path}")


@main.command("update")
@click.option(
    "--repo",
    "repo_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Libre Claw git checkout to update. Defaults to the current checkout.",
)
@click.option("--remote", default="origin", show_default=True, help="Git remote to fetch from.")
@click.option("--branch", default="main", show_default=True, help="Remote branch to fast-forward from.")
@click.option("--dry-run", is_flag=True, help="Check for updates without writing a backup or applying changes.")
def update_command(repo_path: Path | None, remote: str, branch: str, dry_run: bool) -> None:
    """Safely update this Libre Claw checkout from the latest main commit."""
    try:
        result = _update_checkout(repo_path, remote=remote, branch=branch, dry_run=dry_run)
    except UpdateError as exc:
        _raise_click_error(str(exc))

    click.echo(f"Repository: {result.repo_root}")
    click.echo(f"Remote: {result.remote_ref}")
    click.echo(f"Current: {_short_commit(result.local_head)}")
    click.echo(f"Latest:  {_short_commit(result.remote_head)}")
    if result.dry_run:
        if result.updated:
            click.echo("Update available. Rerun without --dry-run to back up and fast-forward.")
        else:
            click.echo("Libre Claw is already up to date.")
        return
    if result.backup_dir is not None:
        click.echo(f"Backup: {result.backup_dir}")
    if result.updated:
        click.echo(f"Updated Libre Claw to {_short_commit(result.remote_head)}.")
    else:
        click.echo("Libre Claw is already up to date.")


def _update_checkout(repo_path: Path | None, *, remote: str, branch: str, dry_run: bool = False) -> UpdateResult:
    repo_root = _resolve_update_repo(repo_path)
    current_branch = _git_stdout(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if current_branch == "HEAD":
        raise UpdateError("Libre Claw update requires a branch checkout, but this repository is detached.")
    if current_branch != branch:
        raise UpdateError(f"Libre Claw update only updates `{branch}`. Current branch is `{current_branch}`.")

    remote_ref = f"{remote}/{branch}"
    click.echo(f"Checking {remote_ref}...")
    _run_git(repo_root, ["fetch", "--prune", remote, branch])
    local_head = _git_stdout(repo_root, ["rev-parse", "HEAD"])
    remote_head = _git_stdout(repo_root, ["rev-parse", "--verify", remote_ref])
    if local_head == remote_head:
        return UpdateResult(repo_root, local_head, remote_head, remote_ref, None, updated=False, dry_run=dry_run)

    if dry_run:
        return UpdateResult(repo_root, local_head, remote_head, remote_ref, None, updated=True, dry_run=True)

    backup_dir = _create_update_backup(repo_root, remote=remote, branch=branch, local_head=local_head, remote_head=remote_head)
    if _git_dirty(repo_root):
        raise UpdateError(
            "Working tree has uncommitted changes. "
            f"Backup written to {backup_dir}. Commit or stash your changes, then rerun `libre-claw update`."
        )
    if not _git_is_ancestor(repo_root, local_head, remote_head):
        raise UpdateError(
            f"Local `{branch}` cannot fast-forward to {remote_ref}. "
            f"Backup written to {backup_dir}. Rebase or merge manually."
        )

    click.echo("Applying fast-forward update...")
    _run_git(repo_root, ["merge", "--ff-only", remote_ref])
    new_head = _git_stdout(repo_root, ["rev-parse", "HEAD"])
    return UpdateResult(repo_root, local_head, new_head, remote_ref, backup_dir, updated=True)


def _resolve_update_repo(repo_path: Path | None) -> Path:
    candidates = [repo_path.expanduser() if repo_path is not None else Path.cwd()]
    package_root = Path(__file__).resolve().parents[2]
    if package_root not in candidates:
        candidates.append(package_root)
    for candidate in candidates:
        try:
            start = candidate.resolve()
        except OSError:
            continue
        result = _run_git(start, ["rev-parse", "--show-toplevel"], check=False)
        if result.returncode == 0:
            root = Path(result.stdout.strip()).resolve()
            if root.is_dir():
                return root
    raise UpdateError("Could not find a Libre Claw git checkout to update.")


def _create_update_backup(repo_root: Path, *, remote: str, branch: str, local_head: str, remote_head: str) -> Path:
    backup_dir = _update_backup_root() / f"{_utc_timestamp()}-{_short_commit(local_head)}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "remote": remote,
        "branch": branch,
        "local_head": local_head,
        "remote_head": remote_head,
    }
    (backup_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (backup_dir / "status.txt").write_text(_git_stdout(repo_root, ["status", "--short", "--branch"], allow_empty=True), encoding="utf-8")
    (backup_dir / "working-tree.patch").write_text(
        _git_stdout(repo_root, ["diff", "--binary"], allow_empty=True),
        encoding="utf-8",
    )
    (backup_dir / "staged.patch").write_text(
        _git_stdout(repo_root, ["diff", "--cached", "--binary"], allow_empty=True),
        encoding="utf-8",
    )
    untracked = _git_stdout(repo_root, ["ls-files", "--others", "--exclude-standard"], allow_empty=True)
    (backup_dir / "untracked.txt").write_text(untracked, encoding="utf-8")
    _write_untracked_archive(repo_root, untracked.splitlines(), backup_dir)
    _run_git(repo_root, ["bundle", "create", str(backup_dir / "head.bundle"), "HEAD"])
    return backup_dir


def _update_backup_root() -> Path:
    return _runtime_dir() / "backups" / "updates"


def _write_untracked_archive(repo_root: Path, files: list[str], backup_dir: Path) -> None:
    safe_files: list[tuple[str, Path]] = []
    for name in files:
        candidate = (repo_root / name).resolve()
        if not _path_is_relative_to(candidate, repo_root) or not candidate.is_file():
            continue
        safe_files.append((name, candidate))
    if not safe_files:
        return
    with zipfile.ZipFile(backup_dir / "untracked.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in safe_files:
            archive.write(path, arcname=name)


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_dirty(repo_root: Path) -> bool:
    return bool(_git_stdout(repo_root, ["status", "--porcelain"], allow_empty=True).strip())


def _git_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return _run_git(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0


def _git_stdout(repo_root: Path, args: list[str], *, allow_empty: bool = False) -> str:
    result = _run_git(repo_root, args)
    if not result.stdout and not allow_empty:
        raise UpdateError(f"Git command returned no output: git {' '.join(args)}")
    return result.stdout.strip() if not allow_empty else result.stdout


def _run_git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed git executable with structured arguments.
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Could not run git {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise UpdateError(f"Git command failed: git {' '.join(args)}\n{details}")
    return result


def _short_commit(commit: str) -> str:
    return commit[:12]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _runtime_dir() -> Path:
    return Path.home() / ".libre-claw"


def _process_state_path() -> Path:
    return _runtime_dir() / PROCESS_STATE_NAME


def _process_log_path() -> Path:
    return _runtime_dir() / PROCESS_LOG_NAME


@contextmanager
def _registered_process(mode: str, base_url: str) -> Iterator[None]:
    _write_process_state(mode, base_url)
    try:
        yield
    finally:
        _remove_process_state_if_current()


def _write_process_state(mode: str, base_url: str) -> None:
    if mode not in PROCESS_MODES:
        raise ValueError(f"Unsupported Libre Claw process mode: {mode}")
    path = _process_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "mode": mode,
        "base_url": base_url,
        "cwd": str(Path.cwd()),
        "argv": sys.argv,
        "started_at": time.time(),
    }
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _read_process_state() -> dict[str, Any]:
    path = _process_state_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _remove_process_state_if_current() -> None:
    state = _read_process_state()
    if _state_pid(state) != os.getpid():
        return
    try:
        _process_state_path().unlink()
    except FileNotFoundError:
        pass


def _state_pid(state: dict[str, Any]) -> int | None:
    value = state.get("pid")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _selected_restart_mode(mode: str, state: dict[str, Any]) -> str:
    if mode != "auto":
        return mode
    previous = state.get("mode")
    if isinstance(previous, str) and previous in PROCESS_MODES:
        return previous
    return "daemon"


def _stop_lifecycle(
    config: LibreClawConfig,
    *,
    host: str | None,
    port: int | None,
    timeout: float,
    force: bool,
) -> ProcessStopResult:
    state = _read_process_state()
    pid = _state_pid(state)
    mode = state.get("mode") if isinstance(state.get("mode"), str) else None
    target_urls = _lifecycle_target_urls(config, host=host, port=port)

    for base_url in target_urls:
        if _request_daemon_shutdown(base_url):
            if pid is not None and _is_pid_running(pid) and not _wait_for_pid_exit(pid, timeout):
                if force and _kill_pid(pid, signal.SIGKILL) and _wait_for_pid_exit(pid, timeout=2.0):
                    _clear_process_state()
                    return ProcessStopResult(True, f"Stopped Libre Claw {mode or 'process'} with pid {pid}.", pid, mode)
                return ProcessStopResult(
                    False,
                    f"Shutdown was requested, but pid {pid} is still running. Retry with --force.",
                    pid,
                    mode,
                )
            _clear_process_state()
            return ProcessStopResult(True, f"Stopped Libre Claw {mode or 'daemon'} at {base_url}.", pid, mode)

    for base_url in target_urls:
        if not _daemon_health_ok(base_url):
            continue
        listener_pid = _listener_pid_for_base_url(base_url)
        if listener_pid is None:
            return ProcessStopResult(
                False,
                f"Libre Claw is responding at {base_url}, but no listener pid could be found.",
                pid,
                mode,
            )
        if not _pid_matches_process_state(listener_pid, {"argv": ["libre-claw"]}):
            return ProcessStopResult(
                False,
                f"Libre Claw is responding at {base_url}, but listener pid {listener_pid} does not look like Libre Claw.",
                listener_pid,
                mode,
            )
        if not _kill_pid(listener_pid, signal.SIGTERM):
            return ProcessStopResult(False, f"Could not signal Libre Claw listener pid {listener_pid}.", listener_pid, mode)
        if _wait_for_pid_exit(listener_pid, timeout):
            _clear_process_state()
            return ProcessStopResult(
                True,
                f"Stopped Libre Claw daemon on {base_url} with pid {listener_pid}.",
                listener_pid,
                mode or "daemon",
            )
        if force and _kill_pid(listener_pid, signal.SIGKILL) and _wait_for_pid_exit(listener_pid, timeout=2.0):
            _clear_process_state()
            return ProcessStopResult(
                True,
                f"Force-stopped Libre Claw daemon on {base_url} with pid {listener_pid}.",
                listener_pid,
                mode or "daemon",
            )
        return ProcessStopResult(
            False,
            f"Sent SIGTERM to Libre Claw listener pid {listener_pid}, but it is still running. Retry with --force.",
            listener_pid,
            mode or "daemon",
        )

    if pid is not None:
        if not _is_pid_running(pid):
            _clear_process_state()
            return ProcessStopResult(False, f"No running Libre Claw process found. Removed stale pid {pid}.", None, mode)
        if not _pid_matches_process_state(pid, state):
            _clear_process_state()
            return ProcessStopResult(
                False,
                f"No running Libre Claw process found. Removed stale pid {pid} without signaling it.",
                None,
                mode,
            )
        if not _kill_pid(pid, signal.SIGTERM):
            return ProcessStopResult(False, f"Could not signal Libre Claw pid {pid}.", pid, mode)
        if _wait_for_pid_exit(pid, timeout):
            _clear_process_state()
            return ProcessStopResult(True, f"Stopped Libre Claw {mode or 'process'} with pid {pid}.", pid, mode)
        if force and _kill_pid(pid, signal.SIGKILL) and _wait_for_pid_exit(pid, timeout=2.0):
            _clear_process_state()
            return ProcessStopResult(True, f"Force-stopped Libre Claw {mode or 'process'} with pid {pid}.", pid, mode)
        return ProcessStopResult(False, f"Sent SIGTERM to pid {pid}, but it is still running. Retry with --force.", pid, mode)

    return ProcessStopResult(False, "No running Libre Claw process found.")


def _stop_active_turn(
    config: LibreClawConfig,
    *,
    run_id: str | None,
    host: str | None,
    port: int | None,
    timeout: float,
) -> str:
    base_urls = _lifecycle_target_urls(config, host=host, port=port)
    if run_id:
        for base_url in base_urls:
            if _cancel_daemon_run(base_url, run_id, timeout=timeout):
                return f"Stopped daemon turn {run_id}."
        return f"Could not stop daemon turn {run_id}. If you meant to stop Libre Claw itself, use `libre-claw shutdown`."

    for base_url in base_urls:
        payload = _request_daemon_json("GET", base_url, "/runs?limit=25", timeout=timeout)
        runs = payload.get("runs") if payload else None
        if not isinstance(runs, list):
            continue
        active = _first_active_run(runs)
        if active is None:
            continue
        active_id = str(active.get("run_id", ""))
        if active_id and _cancel_daemon_run(base_url, active_id, timeout=timeout):
            return f"Stopped daemon turn {active_id}."
    return "No active daemon turn found. If you meant to stop Libre Claw itself, use `libre-claw shutdown`."


def _running_daemon_url(config: LibreClawConfig, *, host: str | None, port: int | None) -> str | None:
    for base_url in _lifecycle_target_urls(config, host=host, port=port):
        payload = _request_daemon_json("GET", base_url, "/health", timeout=0.75)
        if payload and payload.get("ok") is True:
            return base_url
    return None


def _first_active_run(runs: list[object]) -> dict[str, Any] | None:
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("state", "")).lower() in {"queued", "running", "blocked"}:
            return run
    return None


def _cancel_daemon_run(base_url: str, run_id: str, *, timeout: float) -> bool:
    payload = _request_daemon_json("POST", base_url, f"/runs/{run_id}/cancel", timeout=timeout)
    return payload is not None


def _request_daemon_json(method: str, base_url: str, path: str, *, timeout: float) -> dict[str, Any] | None:
    try:
        response = httpx.request(method, f"{_client_base_url(base_url)}{path}", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _lifecycle_target_urls(config: LibreClawConfig, *, host: str | None, port: int | None) -> list[str]:
    configured_url = daemon_base_url(config, host=host, port=port)
    state = _read_process_state()
    state_url = state.get("base_url") if isinstance(state.get("base_url"), str) else None
    return _unique_urls([configured_url if host or port else state_url, configured_url])


def _unique_urls(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for value in values:
        if not value:
            continue
        normalized = value.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def _request_daemon_shutdown(base_url: str) -> bool:
    try:
        response = httpx.post(f"{_client_base_url(base_url)}/shutdown", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _daemon_health_ok(base_url: str) -> bool:
    payload = _request_daemon_json("GET", base_url, "/health", timeout=0.75)
    return bool(payload and payload.get("ok") is True)


def _listener_pid_for_base_url(base_url: str) -> int | None:
    port = _port_from_base_url(base_url)
    if port is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments.
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0:
            return pid
    return None


def _port_from_base_url(base_url: str) -> int | None:
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def _client_base_url(base_url: str) -> str:
    if base_url.startswith("http://0.0.0.0:"):
        return "http://127.0.0.1:" + base_url.rsplit(":", maxsplit=1)[-1]
    return base_url.rstrip("/")


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_pid(pid: int, sig: signal.Signals) -> bool:
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


def _pid_matches_process_state(pid: int, state: dict[str, Any]) -> bool:
    command = _process_command(pid)
    if command is None:
        return True
    if "libre-claw" in command or "libre_claw" in command:
        return True
    argv = state.get("argv")
    if isinstance(argv, list) and argv:
        executable = Path(str(argv[0])).name
        return bool(executable and executable in command)
    return False


def _process_command(pid: int) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments.
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    command = result.stdout.strip()
    return command or None


def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.1)
    return not _is_pid_running(pid)


def _clear_process_state() -> None:
    try:
        _process_state_path().unlink()
    except FileNotFoundError:
        pass


def _start_background_process(
    ctx: click.Context,
    config: LibreClawConfig,
    mode: str,
    *,
    host: str | None,
    port: int | None,
) -> StartedProcess:
    if mode not in PROCESS_MODES:
        raise click.ClickException(f"Unsupported restart mode: {mode}")
    command = _entry_command() + _global_cli_args(ctx)
    if mode == "telegram-up":
        command.extend(["telegram", "up"])
    elif mode == "telegram-run":
        command.extend(["telegram", "run"])
    else:
        command.append("daemon")
    if mode != "telegram-run":
        if host:
            command.extend(["--host", host])
        if port is not None:
            command.extend(["--port", str(port)])

    log_path = _process_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(  # noqa: S603 - command is the current Libre Claw executable.
            command,
            cwd=Path.cwd(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return StartedProcess(
        pid=process.pid,
        base_url=daemon_base_url(config, host=host, port=port),
        log_path=log_path,
        mode=mode,
    )


def _entry_command() -> list[str]:
    executable = Path(sys.argv[0])
    if executable.exists() and os.access(executable, os.X_OK):
        return [str(executable)]
    return [sys.executable, "-m", "libre_claw"]


def _global_cli_args(ctx: click.Context) -> list[str]:
    obj = ctx.obj or {}
    args: list[str] = []
    config_path = obj.get("config_path")
    working_directory = obj.get("working_directory")
    if isinstance(config_path, Path):
        args.extend(["--config", str(config_path)])
    if isinstance(working_directory, Path):
        args.extend(["--working-directory", str(working_directory)])
    return args


def _wait_for_daemon_health(base_url: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{_client_base_url(base_url)}/health", timeout=1.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


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


@main.group("searx", invoke_without_command=True)
@click.pass_context
def searx_command(ctx: click.Context) -> None:
    """Manage Libre Claw's optional local SearXNG search instance."""
    if ctx.invoked_subcommand is not None:
        return
    config = _load_context_config(ctx)
    click.echo(f"configured_base_url: {config.web_search.base_url}")
    click.echo(f"local_files: {default_searxng_path()}")
    click.echo("Run `libre-claw searx init` then `libre-claw searx up` to start local search.")


@searx_command.command("init")
@click.option(
    "--path",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for docker-compose.yml and settings.yml. Defaults to ~/.libre-claw/searxng.",
)
def searx_init_command(root: Path | None) -> None:
    """Write local SearXNG compose/settings files with JSON search enabled."""
    files = ensure_searxng_files(root)
    click.echo(f"Wrote local SearXNG files in {files.root}")
    click.echo(f"compose: {files.compose_path}")
    click.echo(f"settings: {files.settings_path}")
    click.echo("Next: run `libre-claw searx up` and keep [web_search].base_url pointed at http://127.0.0.1:8888.")


@searx_command.command("up")
@click.option(
    "--path",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory containing the SearXNG docker-compose.yml.",
)
def searx_up_command(root: Path | None) -> None:
    """Start the optional local SearXNG container."""
    files = ensure_searxng_files(root)
    _run_searxng_compose(files.root, "up", "-d")
    click.echo("SearXNG is starting at http://127.0.0.1:8888")
    click.echo("Test it with `libre-claw searx test`.")


@searx_command.command("down")
@click.option(
    "--path",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory containing the SearXNG docker-compose.yml.",
)
def searx_down_command(root: Path | None) -> None:
    """Stop the optional local SearXNG container."""
    files = ensure_searxng_files(root)
    _run_searxng_compose(files.root, "down")


@searx_command.command("status")
@click.option(
    "--path",
    "root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory containing the SearXNG docker-compose.yml.",
)
def searx_status_command(root: Path | None) -> None:
    """Show Docker Compose status for the optional local SearXNG container."""
    files = ensure_searxng_files(root)
    _run_searxng_compose(files.root, "ps")


@searx_command.command("test")
@click.option("--base-url", help="SearXNG base URL. Defaults to [web_search].base_url.")
@click.option("--query", default="Libre Claw", show_default=True, help="Search query to test.")
@click.pass_context
def searx_test_command(ctx: click.Context, base_url: str | None, query: str) -> None:
    """Verify that SearXNG JSON search is reachable."""
    config = _load_context_config(ctx)
    target = (base_url or config.web_search.base_url).rstrip("/")
    try:
        response = httpx.get(
            f"{target}/search",
            params={"q": query, "format": "json"},
            headers={"accept": "application/json"},
            timeout=max(1.0, float(config.web_search.timeout)),
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        _raise_click_error(f"Could not reach SearXNG at {target}: {exc}")

    if response.status_code == 403:
        _raise_click_error("SearXNG is reachable, but JSON output is disabled. Add `json` to search.formats.")
    if response.status_code < 200 or response.status_code >= 300:
        _raise_click_error(f"SearXNG returned HTTP {response.status_code}: {response.text[:500]}")
    try:
        payload = response.json()
    except ValueError as exc:
        _raise_click_error(f"SearXNG did not return JSON: {exc}")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        _raise_click_error("SearXNG JSON response did not include a results list.")
    click.echo(f"OK: {len(results)} result(s) from {target}")
    if results and isinstance(results[0], dict):
        title = str(results[0].get("title") or "Untitled").strip()
        url = str(results[0].get("url") or "").strip()
        click.echo(f"first: {title} {url}".rstrip())


_LOCAL_SEARXNG_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _maybe_autostart_searxng(config: Any) -> None:
    """Bring up the local SearXNG container when web search points at it.

    Only fires when web_search is enabled, the provider is the bundled
    `searxng` provider, and `base_url` targets a loopback address — so users
    who run a remote SearXNG (or disable web search) are never touched.
    Failures are logged to stdout but never block daemon startup.
    """
    web = config.web_search
    if not web.enabled or web.provider != "searxng":
        return
    base_url = (web.base_url or "").strip()
    if not _is_local_searxng_url(base_url):
        return
    try:
        up = ensure_searxng_up()
    except Exception as exc:  # noqa: BLE001 - never block daemon startup.
        click.echo(f"SearXNG auto-start skipped: {exc}")
        return
    if up.status == "started":
        click.echo(f"SearXNG auto-started: {up.message}")
    elif up.status == "already-running":
        # Quiet by default: the container was already up, nothing to report.
        pass
    else:
        click.echo(f"SearXNG auto-start skipped: {up.message}")


def _is_local_searxng_url(base_url: str) -> bool:
    parsed = httpx.URL(base_url)
    return parsed.host in _LOCAL_SEARXNG_HOSTS


def _run_searxng_compose(root: Path, *args: str) -> None:
    command = searxng_compose_command(root, *args)
    try:
        result = subprocess.run(  # noqa: S603 - fixed docker compose executable with generated compose file.
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        _raise_click_error("Docker was not found. Install Docker Desktop or Docker Engine to run local SearXNG.")
    except subprocess.TimeoutExpired as exc:
        _raise_click_error(f"Docker Compose timed out: {exc}")

    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False, err=True)
    if result.returncode != 0:
        _raise_click_error(f"Docker Compose exited with {result.returncode}.")


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
