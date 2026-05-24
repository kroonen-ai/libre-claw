# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import default_config_path, load_config, packaged_default_config_text


def test_config_defaults_load_successfully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.general.default_provider == "anthropic"
    assert config.general.default_model == "claude-sonnet-4-6"
    assert config.general.working_directory == tmp_path.resolve()
    assert config.tui.show_status_bar is True
    assert config.permissions.default_level == "ask"
    assert config.auth.keyring_service == "libre-claw"
    assert config.auth.token_ttl_seconds == 3600
    assert "curl | bash" in config.sandbox.blocked_patterns
    assert config.providers["local"]["api_format"] == "ollama"
    assert config.providers["local"]["api_key_env"] == "OLLAMA_API_KEY"
    assert config.providers["local"]["tool_mode"] == "auto"


def test_config_file_env_and_cli_overrides(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "openai"',
                'default_model = "gpt-4o"',
                'working_directory = "from-file"',
                'theme = "light"',
                'log_level = "debug"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIBRE_CLAW_DEFAULT_MODEL", "env-model")
    monkeypatch.setenv("LIBRE_CLAW_THEME", "env-theme")

    config = load_config(config_path=config_path, working_directory=tmp_path / "from-cli")

    assert config.general.default_provider == "openai"
    assert config.general.default_model == "env-model"
    assert config.general.theme == "env-theme"
    assert config.general.working_directory == (tmp_path / "from-cli").resolve()


def test_packaged_default_config_matches_repo_default() -> None:
    assert packaged_default_config_text() == default_config_path().read_text(encoding="utf-8")
