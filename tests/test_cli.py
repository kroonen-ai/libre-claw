# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import errno

from collections.abc import Iterable

from click.testing import CliRunner

from libre_claw import __version__
from libre_claw.cli import main


class FakeLookup:
    def __init__(self, source: str, value: str | None = None) -> None:
        self.source = source
        self.value = value


class FakeKeyStore:
    stored: dict[str, str] = {}

    def set_api_key(self, provider: str, api_key: str) -> str:
        self.stored[provider] = api_key
        return "encrypted_file"

    def get_api_key(self, provider: str, env_var: str | None = None) -> FakeLookup:
        del env_var
        value = FakeKeyStore.stored.get(provider)
        return FakeLookup("encrypted_file" if value is not None else "missing", value)

    def key_status(self, providers: Iterable[tuple[str, str | None]]) -> dict[str, str]:
        return {provider: self.get_api_key(provider, env).source for provider, env in providers}


def test_cli_entrypoint_imports() -> None:
    assert main.name == "main"


def test_cli_version() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_exposes_telegram_command() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "daemon" in result.output
    assert "restart" in result.output
    assert "start" in result.output
    assert "shutdown" in result.output
    assert "stop" in result.output
    assert "tui" in result.output
    assert "chat" in result.output
    assert "telegram" in result.output
    assert "workspace" in result.output
    assert "auth" in result.output
    assert "config" in result.output


def test_cli_tui_uses_native_terminal_selection_by_default(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    seen: dict[str, object] = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class FakeApp:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            seen["config"] = config

        def run(self, *, mouse: bool = True, inline: bool = False, **kwargs: object) -> None:
            seen["mouse"] = mouse
            seen["inline"] = inline
            seen["kwargs"] = kwargs

    monkeypatch.setattr("libre_claw.cli.LibreClawApp", FakeApp)

    result = runner.invoke(main, ["tui"])

    assert result.exit_code == 0
    assert seen["mouse"] is False
    assert seen["inline"] is True


def test_cli_tui_allows_mouse_and_inline_overrides(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    seen: dict[str, object] = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class FakeApp:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            seen["config"] = config

        def run(self, *, mouse: bool = True, inline: bool = False, **kwargs: object) -> None:
            seen["mouse"] = mouse
            seen["inline"] = inline
            seen["kwargs"] = kwargs

    monkeypatch.setattr("libre_claw.cli.LibreClawApp", FakeApp)

    result = runner.invoke(main, ["tui", "--mouse", "--inline"])

    assert result.exit_code == 0
    assert seen["mouse"] is True
    assert seen["inline"] is True


def test_cli_tui_allows_fullscreen_override(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    seen: dict[str, object] = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class FakeApp:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            seen["config"] = config

        def run(self, *, mouse: bool = True, inline: bool = False, **kwargs: object) -> None:
            seen["mouse"] = mouse
            seen["inline"] = inline
            seen["kwargs"] = kwargs

    monkeypatch.setattr("libre_claw.cli.LibreClawApp", FakeApp)

    result = runner.invoke(main, ["tui", "--fullscreen"])

    assert result.exit_code == 0
    assert seen["mouse"] is False
    assert seen["inline"] is False


def test_cli_start_exposes_daemon_options() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["start", "--help"])

    assert result.exit_code == 0
    assert "Start the local background runner daemon" in result.output
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--detach" in result.output
    assert "-d" in result.output


def test_cli_start_reports_already_running_daemon(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.cli._running_daemon_url", lambda config, host, port: "http://127.0.0.1:8766")

    def fail_daemon_server(config):  # type: ignore[no-untyped-def]
        del config
        raise AssertionError("start should not create a second daemon")

    monkeypatch.setattr("libre_claw.cli.DaemonServer", fail_daemon_server)

    result = runner.invoke(main, ["start"])

    assert result.exit_code == 0
    assert "already running at http://127.0.0.1:8766" in result.output
    assert "Dashboard: http://127.0.0.1:8766/dashboard" in result.output


def test_cli_start_detached_uses_background_daemon(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    selected_modes: list[str] = []
    monkeypatch.setattr("libre_claw.cli._running_daemon_url", lambda config, host, port: None)

    def fake_start(ctx, config, mode, host, port):  # type: ignore[no-untyped-def]
        del ctx, config
        selected_modes.append(mode)
        assert host == "127.0.0.1"
        assert port == 9876
        return type(
            "Started",
            (),
            {"pid": 5000, "base_url": "http://127.0.0.1:9876", "log_path": tmp_path / "daemon.log", "mode": mode},
        )()

    monkeypatch.setattr("libre_claw.cli._start_background_process", fake_start)
    monkeypatch.setattr("libre_claw.cli._wait_for_daemon_health", lambda base_url, timeout: True)

    result = runner.invoke(main, ["start", "-d", "--host", "127.0.0.1", "--port", "9876"])

    assert result.exit_code == 0
    assert selected_modes == ["daemon"]
    assert "Started Libre Claw daemon with pid 5000" in result.output
    assert "Dashboard: http://127.0.0.1:9876/dashboard" in result.output
    assert "Log:" in result.output


def test_cli_start_reports_port_conflict_without_traceback(monkeypatch, tmp_path) -> None:
    class PortConflictServer:
        def __init__(self, config):  # type: ignore[no-untyped-def]
            del config

        async def run(self, host=None, port=None):  # type: ignore[no-untyped-def]
            del host, port
            raise OSError(errno.EADDRINUSE, "address already in use")

    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.cli._running_daemon_url", lambda config, host, port: None)
    monkeypatch.setattr("libre_claw.cli.DaemonServer", PortConflictServer)

    result = runner.invoke(main, ["start"])

    assert result.exit_code != 0
    assert "already in use" in result.output
    assert "Traceback" not in result.output


def test_cli_shutdown_reports_no_running_process(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.cli._request_daemon_shutdown", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._daemon_health_ok", lambda base_url: False)

    result = runner.invoke(main, ["shutdown"])

    assert result.exit_code == 0
    assert "No running Libre Claw process found" in result.output


def test_cli_shutdown_kills_unregistered_healthy_daemon_listener(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    killed: list[tuple[int, object]] = []
    running = {"value": True}
    monkeypatch.setattr("libre_claw.cli._request_daemon_shutdown", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._daemon_health_ok", lambda base_url: True)
    monkeypatch.setattr("libre_claw.cli._listener_pid_for_base_url", lambda base_url: 4242)
    monkeypatch.setattr("libre_claw.cli._process_command", lambda pid: "/path/.venv/bin/libre-claw daemon")
    monkeypatch.setattr("libre_claw.cli._is_pid_running", lambda pid: running["value"])

    def fake_kill(pid: int, sig: object) -> bool:
        killed.append((pid, sig))
        running["value"] = False
        return True

    monkeypatch.setattr("libre_claw.cli._kill_pid", fake_kill)

    result = runner.invoke(main, ["shutdown"])

    assert result.exit_code == 0
    assert "Stopped Libre Claw daemon on http://127.0.0.1:8766 with pid 4242" in result.output
    assert killed


def test_cli_shutdown_uses_recorded_pid_fallback(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".libre-claw" / "process.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"pid": 4242, "mode": "telegram-up", "base_url": "http://127.0.0.1:8766"}\n',
        encoding="utf-8",
    )
    killed: list[tuple[int, object]] = []
    running = {"value": True}
    monkeypatch.setattr("libre_claw.cli._request_daemon_shutdown", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._daemon_health_ok", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._is_pid_running", lambda pid: running["value"])

    def fake_kill(pid: int, sig: object) -> bool:
        killed.append((pid, sig))
        running["value"] = False
        return True

    monkeypatch.setattr("libre_claw.cli._kill_pid", fake_kill)

    result = runner.invoke(main, ["shutdown"])

    assert result.exit_code == 0
    assert "Stopped Libre Claw telegram-up with pid 4242" in result.output
    assert killed
    assert not state_path.exists()


def test_cli_shutdown_does_not_signal_stale_reused_pid(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".libre-claw" / "process.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"pid": 4242, "mode": "daemon", "base_url": "http://127.0.0.1:8766"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("libre_claw.cli._request_daemon_shutdown", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._daemon_health_ok", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._is_pid_running", lambda pid: True)
    monkeypatch.setattr("libre_claw.cli._process_command", lambda pid: "/usr/bin/other-app")

    def fail_kill(pid: int, sig: object) -> bool:
        raise AssertionError("stale PID should not be signaled")

    monkeypatch.setattr("libre_claw.cli._kill_pid", fail_kill)

    result = runner.invoke(main, ["shutdown"])

    assert result.exit_code == 0
    assert "Removed stale pid 4242 without signaling it" in result.output
    assert not state_path.exists()


def test_cli_stop_cancels_latest_active_turn(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    requests: list[tuple[str, str, str]] = []

    def fake_request(method: str, base_url: str, path: str, timeout: float):
        requests.append((method, base_url, path))
        if method == "GET":
            return {"runs": [{"run_id": "run-1", "state": "done"}, {"run_id": "run-2", "state": "running"}]}
        if method == "POST" and path == "/runs/run-2/cancel":
            return {"run": {"run_id": "run-2", "state": "cancelled"}}
        return None

    monkeypatch.setattr("libre_claw.cli._request_daemon_json", fake_request)

    result = runner.invoke(main, ["stop"])

    assert result.exit_code == 0
    assert "Stopped daemon turn run-2" in result.output
    assert requests[-1] == ("POST", "http://127.0.0.1:8766", "/runs/run-2/cancel")


def test_cli_restart_reuses_previous_process_mode(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".libre-claw" / "process.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"pid": 4242, "mode": "telegram-up", "base_url": "http://127.0.0.1:8766"}\n',
        encoding="utf-8",
    )
    selected_modes: list[str] = []
    monkeypatch.setattr(
        "libre_claw.cli._stop_lifecycle",
        lambda config, host, port, timeout, force: type(
            "Result",
            (),
            {"stopped": True, "message": "stopped", "pid": 4242, "mode": "telegram-up"},
        )(),
    )

    def fake_start(ctx, config, mode, host, port):  # type: ignore[no-untyped-def]
        del ctx, config, host, port
        selected_modes.append(mode)
        return type(
            "Started",
            (),
            {"pid": 5000, "base_url": "http://127.0.0.1:8766", "log_path": tmp_path / "daemon.log", "mode": mode},
        )()

    monkeypatch.setattr("libre_claw.cli._start_background_process", fake_start)
    monkeypatch.setattr("libre_claw.cli._wait_for_daemon_health", lambda base_url, timeout: True)

    result = runner.invoke(main, ["restart"])

    assert result.exit_code == 0
    assert selected_modes == ["telegram-up"]
    assert "Restarted Libre Claw telegram-up with pid 5000" in result.output


def test_cli_restart_starts_after_clearing_stale_pid(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".libre-claw" / "process.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"pid": 4242, "mode": "daemon", "base_url": "http://127.0.0.1:8766"}\n',
        encoding="utf-8",
    )
    selected_modes: list[str] = []
    monkeypatch.setattr("libre_claw.cli._request_daemon_shutdown", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._daemon_health_ok", lambda base_url: False)
    monkeypatch.setattr("libre_claw.cli._is_pid_running", lambda pid: False)

    def fake_start(ctx, config, mode, host, port):  # type: ignore[no-untyped-def]
        del ctx, config, host, port
        selected_modes.append(mode)
        return type(
            "Started",
            (),
            {"pid": 5000, "base_url": "http://127.0.0.1:8766", "log_path": tmp_path / "daemon.log", "mode": mode},
        )()

    monkeypatch.setattr("libre_claw.cli._start_background_process", fake_start)
    monkeypatch.setattr("libre_claw.cli._wait_for_daemon_health", lambda base_url, timeout: True)

    result = runner.invoke(main, ["restart"])

    assert result.exit_code == 0
    assert selected_modes == ["daemon"]
    assert "Restarted Libre Claw daemon with pid 5000" in result.output
    assert not state_path.exists()


def test_cli_telegram_help_exposes_setup_and_up() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["telegram", "--help"])

    assert result.exit_code == 0
    assert "allow" in result.output
    assert "setup" in result.output
    assert "up" in result.output
    assert "status" in result.output


def test_cli_telegram_setup_stores_token_and_config(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    FakeKeyStore.stored.clear()
    monkeypatch.setattr("libre_claw.cli.ApiKeyStore.from_config", lambda config: FakeKeyStore())

    result = runner.invoke(
        main,
        [
            "telegram",
            "setup",
            "--bot-token",
            "secret-token",
            "--user-id",
            "123",
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3.7-max",
        ],
    )

    assert result.exit_code == 0
    assert FakeKeyStore.stored["telegram"] == "secret-token"
    assert "secret-token" not in result.output
    config_text = (tmp_path / ".libre-claw" / "config.toml").read_text(encoding="utf-8")
    assert "enabled = true" in config_text
    assert "use_daemon = true" in config_text
    assert "allowed_user_ids = [123]" in config_text
    assert 'default_provider = "openrouter"' in config_text


def test_cli_telegram_setup_fails_when_key_cannot_be_verified(monkeypatch, tmp_path) -> None:
    class BrokenKeyStore:
        def set_api_key(self, provider: str, api_key: str) -> str:
            del provider, api_key
            return "keyring"

        def get_api_key(self, provider: str, env_var: str | None = None) -> FakeLookup:
            del provider, env_var
            return FakeLookup("missing", None)

    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.cli.ApiKeyStore.from_config", lambda config: BrokenKeyStore())

    result = runner.invoke(
        main,
        [
            "telegram",
            "setup",
            "--bot-token",
            "secret-token",
            "--user-id",
            "123",
        ],
    )

    assert result.exit_code != 0
    assert "Could not verify the stored telegram key" in result.output
    assert "secret-token" not in result.output


def test_cli_telegram_allow_appends_user_id(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / ".libre-claw" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[telegram]",
                "enabled = true",
                "use_daemon = true",
                'bot_token_env = "TELEGRAM_BOT_TOKEN"',
                "allowed_user_ids = [123]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(main, ["telegram", "allow", "456"])

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "allowed_user_ids = [123, 456]" in config_text
    assert "Restart `libre-claw telegram up`" in result.output


def test_cli_workspace_init_creates_workspace_and_updates_config(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "project"
    source.mkdir()
    (source / "SOUL.md").write_text("# Project Soul\n\nKnow the project.", encoding="utf-8")
    target = tmp_path / "Documents" / ".workspace" / "libre-claw"

    result = runner.invoke(main, ["--working-directory", str(source), "workspace", "init", "--path", str(target)])

    assert result.exit_code == 0
    assert "Libre Claw workspace initialized" in result.output
    assert (target / "SOUL.md").exists()
    config_text = (tmp_path / ".libre-claw" / "config.toml").read_text(encoding="utf-8")
    assert f'working_directory = "{target}"' in config_text


def test_cli_workspace_status(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(main, ["workspace", "status"])

    assert result.exit_code == 0
    assert "Libre Claw workspace:" in result.output


def test_cli_config_defaults_outputs_toml() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["config", "defaults"])

    assert result.exit_code == 0
    assert "[general]" in result.output
    assert 'default_provider = "anthropic"' in result.output


def test_cli_auth_status_does_not_print_keys(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic-key")

    result = runner.invoke(main, ["auth", "status"])

    assert result.exit_code == 0
    assert "anthropic: environment" in result.output
    assert "openrouter: missing" in result.output
    assert "secret-anthropic-key" not in result.output
