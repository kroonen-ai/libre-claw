# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
import sys

import click.testing
import pytest
from click.testing import CliRunner

from libre_claw.cli import main
from libre_claw.cli_ui import status_text, terminal_color


def test_help_groups_every_command_and_wraps_on_narrow_terminals(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    result = CliRunner().invoke(main, ["--help"], terminal_width=48)
    assert result.exit_code == 0
    assert "Work:" in result.stdout and "Services:" in result.stdout
    assert "Setup & extensions:" in result.stdout and "Quick start:" in result.stdout
    for name, command in main.commands.items():
        if not command.hidden:
            assert name in result.stdout
    assert max(map(len, result.stdout.splitlines())) <= 48
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize("setting,value", [("NO_COLOR", "1"), ("TERM", "dumb")])
def test_help_respects_color_opt_out_even_when_color_is_requested(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    monkeypatch.setattr(click.testing._NamedTextIOWrapper, "isatty", lambda self: self.name == "<stdout>")
    result = CliRunner().invoke(main, ["--help"], color=True)
    assert result.exit_code == 0
    assert "\x1b" not in result.stdout


def test_help_can_use_terminal_styling_without_loading_config(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(click.testing._NamedTextIOWrapper, "isatty", lambda self: self.name == "<stdout>")
    monkeypatch.setattr("libre_claw.cli.load_config", lambda **kwargs: pytest.fail("Help must not load configuration"))
    result = CliRunner().invoke(main, ["--help"], color=True)
    assert result.exit_code == 0
    assert "\x1b[" in result.stdout


@pytest.mark.parametrize("is_terminal", [False, True])
def test_terminal_color_checks_current_stream_without_replacing_its_encoding(monkeypatch, is_terminal):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    with io.TextIOWrapper(io.BytesIO(), encoding="latin-1") as stream:
        monkeypatch.setattr(stream, "isatty", lambda: is_terminal)
        with monkeypatch.context() as output:
            output.setattr(sys, "stdout", stream)
            assert terminal_color() is is_terminal
            assert sys.stdout is stream
            assert stream.encoding == "latin-1"
            click.echo("caf\u00e9")
        assert stream.buffer.getvalue() == b"caf\xe9\n"


def test_terminal_color_handles_a_detached_stdout(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(sys, "stdout", None)
    assert terminal_color() is False
    assert terminal_color(True) is True


def test_run_without_message_rejects_interactive_stdin_instead_of_waiting(monkeypatch):
    monkeypatch.setattr(click.testing._NamedTextIOWrapper, "isatty", lambda self: self.name == "<stdin>")
    monkeypatch.setattr("libre_claw.cli.load_config", lambda **kwargs: pytest.fail("An empty interactive run must not start"))
    result = CliRunner().invoke(main, ["run"])
    assert result.exit_code == 2
    assert "Provide MESSAGE" in result.stderr
    assert "libre-claw tui" in result.stderr


@pytest.fixture
def status_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "custom.toml"
    path.write_text('[general]\ndefault_provider = "deepseek"\ndefault_model = "deepseek-flash"\n'
                    '[providers.deepseek]\napi_key = "not-for-display"\n')
    monkeypatch.setattr("libre_claw.cli._lifecycle_target_urls", lambda *args, **kwargs: ["http://127.0.0.1:8766"])
    return tmp_path, path


def test_status_reports_config_sources_and_health_without_exposing_secrets(status_setup, monkeypatch):
    workspace, config = status_setup
    seen = []
    def health(method, url, path, *, timeout):
        seen.append((method, url, path, timeout))
        return {"ok": True, "active_runs": 2, "secret": "server-secret"}
    monkeypatch.setattr("libre_claw.cli._request_daemon_json", health)
    result = CliRunner().invoke(main, ["--config", str(config), "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workspace"] == str(workspace.resolve())
    assert payload["provider"] == "deepseek" and payload["model"] == "deepseek-flash"
    assert str(config.resolve()) in payload["config_sources"]
    assert payload["daemon"]["state"] == "online" and payload["daemon"]["active_runs"] == 2
    assert seen == [("GET", "http://127.0.0.1:8766", "/health", 1.0)]
    assert result.stderr == ""
    assert "not-for-display" not in result.stdout and "server-secret" not in result.stdout
    assert "\x1b" not in result.stdout


def test_status_offline_has_useful_guidance_and_zero_side_effects(status_setup, monkeypatch):
    _, config = status_setup
    monkeypatch.setattr("libre_claw.cli._request_daemon_json", lambda *args, **kwargs: None)
    monkeypatch.setattr("libre_claw.cli._start_background_process", lambda *args, **kwargs: pytest.fail("Status must not start services"))
    monkeypatch.setenv("NO_COLOR", "1")
    result = CliRunner().invoke(main, ["--config", str(config), "status"])
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.stdout
    assert "start --detach" in result.stdout
    assert "deepseek / deepseek-flash" in result.stdout
    assert "not-for-display" not in result.stdout and "\x1b" not in result.stdout


def test_status_does_not_probe_credential_bearing_stale_urls(status_setup, monkeypatch):
    _, config = status_setup
    monkeypatch.setattr("libre_claw.cli._lifecycle_target_urls", lambda *args, **kwargs: [
        "https://user:password@example.com", "https://example.com?token=cli-ui-secret-value",
    ])
    monkeypatch.setattr("libre_claw.cli._request_daemon_json", lambda *args, **kwargs: pytest.fail("Do not send stale URL credentials"))
    result = CliRunner().invoke(main, ["--config", str(config), "status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["daemon"]["state"] == "unavailable"
    assert "password" not in result.stdout and "cli-ui-secret-value" not in result.stdout


def test_status_never_prints_credentials_from_a_malformed_configured_host(status_setup, monkeypatch):
    _, config = status_setup
    with config.open("a") as handle:
        handle.write('\n[daemon]\nhost = "user:daemon-secret@example.com"\n')
    monkeypatch.setattr("libre_claw.cli._lifecycle_target_urls", lambda *args, **kwargs: ["http://user:daemon-secret@example.com:8766"])
    monkeypatch.setattr("libre_claw.cli._request_daemon_json", lambda *args, **kwargs: pytest.fail("Do not send host credentials"))
    for args in ([], ["--json"]):
        result = CliRunner().invoke(main, ["--config", str(config), "status", *args])
        assert result.exit_code == 0, result.output
        assert "daemon-secret" not in result.stdout
        if args:
            assert json.loads(result.stdout)["daemon"]["url"] is None
        else:
            assert "Check [daemon].host" in result.stdout


def test_status_skips_malformed_stale_url_and_uses_the_valid_target(status_setup, monkeypatch):
    _, config = status_setup
    monkeypatch.setattr("libre_claw.cli._lifecycle_target_urls", lambda *args, **kwargs: [
        "http://bad\x00host:8766", "http://127.0.0.1:8766",
    ])
    def health(method, url, path, *, timeout):
        assert url == "http://127.0.0.1:8766"
        return {"ok": True, "active_runs": 0}
    monkeypatch.setattr("libre_claw.cli._request_daemon_json", health)
    result = CliRunner().invoke(main, ["--config", str(config), "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["daemon"]["state"] == "online"


def test_status_presentation_wraps_and_treats_terminal_controls_as_data(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    payload = {
        "version": "test", "workspace": "/a/very/long/workspace/with/[brackets]/and/more/segments",
        "provider": "deepseek", "model": "model\x1b]52;clipboard", "theme": "libre",
        "config_sources": ["/a/very/long/configuration/path/to/settings.toml"], "log_path": "/tmp/log",
        "daemon": {"state": "online", "active_runs": 1, "dashboard_url": "http://127.0.0.1:8766/dashboard"},
    }
    text = status_text(payload, width=32)
    assert max(map(len, text.splitlines())) <= 32
    assert "[brackets]" in "".join(text.splitlines())
    assert "\\u001b" in text and "\x1b" not in text
