# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import (
    default_config_path,
    global_config_path,
    load_config,
    packaged_default_config_text,
    set_global_default_model,
    user_config_path,
)


def test_config_defaults_load_successfully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.general.default_provider == "anthropic"
    assert config.general.default_model == "claude-opus-4-6"
    assert config.general.working_directory == tmp_path.resolve()
    assert config.tui.show_status_bar is True
    assert config.tui.show_file_tree is False
    assert config.tui.use_daemon is False
    assert config.permissions.default_level == "ask"
    assert config.auth.keyring_service == "libre-claw"
    assert config.auth.token_ttl_seconds == 3600
    assert config.agent.context_window_tokens == 200000
    assert config.goal.max_turns == 20
    assert config.goal.judge_provider == "current"
    assert config.goal.judge_model == ""
    assert config.goal.judge_temperature == 0.0
    assert config.goal.judge_max_tokens == 1024
    assert config.telegram.use_daemon is False
    assert config.daemon.host == "127.0.0.1"
    assert config.daemon.port == 8766
    assert config.daemon.poll_interval == 0.5
    assert config.automations.enabled is True
    assert config.automations.root == tmp_path / ".libre-claw" / "automations"
    assert config.automations.poll_interval == 30.0
    assert config.automations.max_due_per_tick == 5
    assert config.browser.allowed_domains == ()
    assert config.browser.denied_domains == ()
    assert config.browser.profile_dir == tmp_path / ".libre-claw" / "browser" / "profiles"
    assert config.browser.downloads_dir == tmp_path / ".libre-claw" / "browser" / "downloads"
    assert config.browser.screenshots_dir == tmp_path / ".libre-claw" / "browser" / "screenshots"
    assert config.browser.default_timeout_ms == 30000
    assert config.browser.headless is True
    assert config.mcp.enabled is False
    assert config.mcp.allowlist == ()
    assert config.mcp.permission_level == "ask"
    assert "Kroonen AI Inc. (https://kroonen.ai)" in config.agent.system_prompt
    assert "search_files" in config.agent.system_prompt
    assert "browser_download" in config.agent.system_prompt
    assert config.agent.system_prompt_extra == ""
    assert "curl | bash" in config.sandbox.blocked_patterns
    assert config.providers["openrouter"]["api_key_env"] == "OPENROUTER_API_KEY"
    assert config.providers["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert config.providers["openrouter"]["default_model"] == "openrouter/auto"
    assert "http_referer" not in config.providers["openrouter"]
    assert "app_title" not in config.providers["openrouter"]
    assert "local" not in config.providers
    assert config.providers["ollama"]["api_format"] == "ollama"
    assert config.providers["ollama"]["api_key_env"] == "OLLAMA_API_KEY"
    assert config.providers["ollama"]["tool_mode"] == "auto"


def test_config_normalizes_legacy_local_provider(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "local"',
                'default_model = "kimi-k2.6:cloud"',
                "",
                "[providers.local]",
                'base_url = "https://ollama.com"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    config = load_config(config_path=config_path)

    assert config.general.default_provider == "ollama"
    assert "local" not in config.providers
    assert config.providers["ollama"]["base_url"] == "https://ollama.com"


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
                "",
                "[agent]",
                'system_prompt = "custom system prompt"',
                'system_prompt_extra = "custom extra"',
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
    assert config.agent.system_prompt == "custom system prompt"
    assert config.agent.system_prompt_extra == "custom extra"


def test_set_global_default_model_updates_user_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    path = user_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "# custom user config",
                "[general]",
                'default_provider = "anthropic"',
                'theme = "dark"',
                "",
                "[tui]",
                "show_file_tree = false",
            ]
        ),
        encoding="utf-8",
    )

    written = set_global_default_model("openrouter", "qwen/qwen3.7-max")

    assert written == path
    text = path.read_text(encoding="utf-8")
    assert 'default_provider = "openrouter"' in text
    assert 'default_model = "qwen/qwen3.7-max"' in text
    assert "[providers.openrouter]" in text
    assert 'theme = "dark"' in text
    config = load_config()
    assert config.general.default_provider == "openrouter"
    assert config.general.default_model == "qwen/qwen3.7-max"
    assert config.providers["openrouter"]["default_model"] == "qwen/qwen3.7-max"
    assert config.tui.show_file_tree is False


def test_global_config_path_prefers_active_user_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    user_path = user_config_path()
    user_path.parent.mkdir(parents=True)
    user_path.write_text("[general]\ndefault_provider = \"codex\"\n", encoding="utf-8")

    config = load_config()

    assert global_config_path(config) == user_path


def test_packaged_default_config_matches_repo_default() -> None:
    assert packaged_default_config_text() == default_config_path().read_text(encoding="utf-8")
