# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, NoReturn

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
from libre_claw.core.workspace import (
    default_claw_workspace_path,
    initialize_claw_workspace,
    workspace_result_text,
    workspace_status_text,
)
from libre_claw.daemon import DaemonServer, daemon_base_url
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
@click.pass_context
def start_command(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Start the local background runner daemon."""
    _run_daemon_process(ctx, host=host, port=port)


def _run_daemon_process(ctx: click.Context, *, host: str | None, port: int | None) -> None:
    config = _load_context_config(ctx)
    server = DaemonServer(config)
    base_url = daemon_base_url(config, host=host, port=port)
    click.echo(f"Libre Claw daemon listening on {base_url}")
    click.echo(f"Dashboard: {base_url}/dashboard")
    try:
        with _registered_process("daemon", base_url):
            asyncio.run(server.run(host=host, port=port))
    except RuntimeError as exc:
        _raise_click_error(str(exc))
    except KeyboardInterrupt:
        click.echo("Stopped Libre Claw daemon.")


@main.command("stop")
@click.option("--host", help="Host interface for the local daemon API.")
@click.option("--port", type=int, help="Port for the local daemon API.")
@click.option("--timeout", type=float, default=10.0, show_default=True, help="Seconds to wait for shutdown.")
@click.option("--force", is_flag=True, help="Send SIGKILL if the process does not stop after SIGTERM.")
@click.pass_context
def stop_command(ctx: click.Context, host: str | None, port: int | None, timeout: float, force: bool) -> None:
    """Stop a running Libre Claw daemon or Telegram stack from another terminal."""
    config = _load_context_config(ctx)
    result = _stop_lifecycle(config, host=host, port=port, timeout=timeout, force=force)
    click.echo(result.message)


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
    configured_url = daemon_base_url(config, host=host, port=port)
    state_url = state.get("base_url") if isinstance(state.get("base_url"), str) else None
    target_urls = _unique_urls([configured_url if host or port else state_url, configured_url])

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

    if pid is not None:
        if not _is_pid_running(pid):
            _clear_process_state()
            return ProcessStopResult(False, f"No running Libre Claw process found. Removed stale pid {pid}.", pid, mode)
        if not _pid_matches_process_state(pid, state):
            _clear_process_state()
            return ProcessStopResult(
                False,
                f"No running Libre Claw process found. Removed stale pid {pid} without signaling it.",
                pid,
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
