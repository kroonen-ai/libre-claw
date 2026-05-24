# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from click.testing import CliRunner

from libre_claw import __version__
from libre_claw.cli import main


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
    assert "telegram" in result.output
    assert "auth" in result.output
    assert "config" in result.output


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
    assert "secret-anthropic-key" not in result.output
