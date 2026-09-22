# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import libre_claw.auth.codex as codex_auth
from libre_claw.cli import main
from libre_claw.config import load_config
from libre_claw.tui.app import LibreClawApp


@pytest.fixture
def codex_auth_commands(monkeypatch, tmp_path):
    """Capture every Codex command without accessing a real login or key store."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    commands = []

    async def fake_run(args, input_text=None, timeout=None):
        commands.append(list(args))
        return codex_auth.CodexCommandResult(
            args=tuple(args), exit_code=0, stdout="Logged in using ChatGPT\n", stderr="",
        )

    async def fake_stream(args, input_text=None):
        commands.append(list(args))
        yield codex_auth.CodexCommandEvent(stream="stderr", text="Login completed.\n")
        yield codex_auth.CodexCommandResult(args=tuple(args), exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(codex_auth, "codex_available", lambda executable="codex": True)
    monkeypatch.setattr(codex_auth, "run_codex_command", fake_run)
    monkeypatch.setattr("libre_claw.cli.stream_codex_command", fake_stream)
    monkeypatch.setattr("libre_claw.tui.app.stream_codex_command", fake_stream)
    monkeypatch.setattr(
        "libre_claw.cli.ApiKeyStore.from_config",
        lambda _config: SimpleNamespace(key_status=lambda _providers: {}),
    )
    return commands


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["codex-login"], ["login", "--device-auth"]),
        (["codex-login", "--browser"], ["login"]),
        (["codex-status"], ["login", "status"]),
        (["status", "codex"], ["login", "status"]),
        (["status"], ["login", "status"]),
        (["codex-logout"], ["logout"]),
    ],
)
def test_cli_codex_auth_uses_configured_executable(codex_auth_commands, tmp_path, command, expected):
    executable = str(tmp_path / "custom bin" / "codex")
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[providers.codex]\nexecutable = "{executable}"\n', encoding="utf-8")

    result = CliRunner().invoke(main, ["--config", str(config_path), "auth", *command])

    assert result.exit_code == 0, result.output
    assert codex_auth_commands == [[executable, *expected]]
    if command == ["codex-login"]:
        assert "security settings or workspace permissions" in result.output
        assert "--browser" in result.output


@pytest.mark.parametrize("command", [["codex-status"], ["status", "codex"]])
def test_codex_status_does_not_open_libre_claw_api_key_store(codex_auth_commands, monkeypatch, command):
    monkeypatch.setattr(
        "libre_claw.cli.ApiKeyStore.from_config",
        lambda _config: pytest.fail("Codex owns its own credentials"),
    )

    result = CliRunner().invoke(main, ["auth", *command])

    assert result.exit_code == 0, result.output
    assert codex_auth_commands == [["codex", "login", "status"]]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/codex login", ["login", "--device-auth"]),
        ("/codex login browser", ["login"]),
        ("/codex status", ["login", "status"]),
        ("/setup status", ["login", "status"]),
        ("/setup codex", ["login", "--device-auth"]),
        ("/codex logout", ["logout"]),
    ],
)
async def test_tui_codex_auth_uses_configured_executable(
    codex_auth_commands, monkeypatch, tmp_path, command, expected,
):
    executable = str(tmp_path / "custom bin" / "codex")
    config = replace(load_config(), providers={"codex": {"executable": executable}})
    monkeypatch.setattr(LibreClawApp, "_rebuild_agent", lambda _self: None)
    monkeypatch.setattr(LibreClawApp, "_update_status", lambda _self: None)
    app = LibreClawApp(config=config)
    messages = []
    monkeypatch.setattr(app, "_append_system", messages.append)

    await app._handle_command(command)

    assert codex_auth_commands == [[executable, *expected]]
    if expected == ["login", "--device-auth"]:
        assert any("security settings or workspace permissions" in message for message in messages)
        assert any("/codex login browser" in message for message in messages)
