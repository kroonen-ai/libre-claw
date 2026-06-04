# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import (
    configure_telegram,
    default_config_path,
    global_config_path,
    load_config,
    packaged_default_config_text,
    set_global_default_model,
    set_global_working_directory,
    user_config_path,
)
from libre_claw.core.heartbeat import heartbeat_prompt, parse_heartbeat_interval


def test_config_defaults_load_successfully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.general.default_provider == "anthropic"
    assert config.general.default_model == "claude-opus-4-8"
    assert config.general.working_directory == tmp_path.resolve()
    assert config.tui.show_status_bar is True
    assert config.tui.show_file_tree is False
    assert config.tui.use_daemon is False
    assert config.tui.mouse is False
    assert config.tui.inline is False
    assert config.permissions.default_level == "ask"
    assert config.auth.keyring_service == "libre-claw"
    assert config.auth.token_ttl_seconds == 3600
    assert config.agent.context_window_tokens == 200000
    assert config.agent.provider_retry_attempts == 2
    assert config.agent.provider_retry_initial_delay == 1.0
    assert config.goal.max_turns == 20
    assert config.goal.judge_provider == "current"
    assert config.goal.judge_model == ""
    assert config.goal.judge_temperature == 0.0
    assert config.goal.judge_max_tokens == 1024
    assert config.fallback.enabled is False
    assert config.fallback.routes == ()
    assert config.heartbeat.enabled is False
    assert config.heartbeat.interval_minutes == 60
    assert config.heartbeat.route == "tui"
    assert config.heartbeat.telegram_chat_id == 0
    assert "blocked approvals" in "\n".join(config.heartbeat.checklist)
    assert config.memory.enabled is True
    assert config.memory.auto_extract is True
    assert config.memory.auto_summarize is True
    assert config.memory.inject_relevant is True
    assert config.memory.max_injected_items == 8
    assert config.memory.max_injected_tokens == 1200
    assert config.memory.redact_secrets is True
    assert config.memory.archive_sessions is True
    assert config.telegram.use_daemon is False
    assert config.daemon.host == "127.0.0.1"
    assert config.daemon.port == 8766
    assert config.daemon.poll_interval == 0.5
    assert config.automations.enabled is True
    assert config.automations.root == tmp_path / ".libre-claw" / "automations"
    assert config.automations.poll_interval == 30.0
    assert config.automations.max_due_per_tick == 5
    assert "bash" in config.automations.auto_approve_tools
    assert "write_file" not in config.automations.auto_approve_tools
    assert config.automations.finalizer_max_tokens == 3000
    assert config.automations.finalizer_max_context_chars == 70000
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
    assert "built by Kroonen AI (https://kroonen.ai)" in config.agent.system_prompt
    assert "search_files" in config.agent.system_prompt
    assert "browser_download" in config.agent.system_prompt
    assert "browser_execute" in config.agent.system_prompt
    assert "http_request" in config.agent.system_prompt
    assert config.agent.system_prompt_extra == ""
    assert "curl | bash" in config.sandbox.blocked_patterns
    assert config.providers["openrouter"]["api_key_env"] == "OPENROUTER_API_KEY"
    assert config.providers["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert config.providers["openrouter"]["default_model"] == "openrouter/auto"
    assert config.providers["openrouter"]["auto_context_window"] is True
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


def test_config_loads_fallback_and_heartbeat_sections(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[fallback]",
                "enabled = true",
                "",
                "[[fallback.routes]]",
                'provider = "openrouter"',
                'model = "openrouter/auto"',
                'api_key_env = "OPENROUTER_BACKUP_API_KEY"',
                "",
                "[heartbeat]",
                "enabled = true",
                "interval_minutes = 15",
                'route = "telegram"',
                "telegram_chat_id = 42",
                'provider = "openrouter"',
                'model = "deepseek/deepseek-v4-flash"',
                'checklist = ["Check CI", "Check blocked approvals"]',
                'prompt = "custom heartbeat"',
                "",
                "[memory]",
                "enabled = false",
                "auto_extract = false",
                "auto_summarize = true",
                "inject_relevant = false",
                "max_injected_items = 3",
                "max_injected_tokens = 500",
                "redact_secrets = true",
                "archive_sessions = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    config = load_config(config_path=config_path)

    assert config.fallback.enabled is True
    assert config.fallback.routes[0].provider == "openrouter"
    assert config.fallback.routes[0].model == "openrouter/auto"
    assert config.fallback.routes[0].api_key_env == "OPENROUTER_BACKUP_API_KEY"
    assert config.heartbeat.enabled is True
    assert config.heartbeat.interval_minutes == 15
    assert config.heartbeat.route == "telegram"
    assert config.heartbeat.telegram_chat_id == 42
    assert config.heartbeat.checklist == ("Check CI", "Check blocked approvals")
    assert config.heartbeat.prompt == "custom heartbeat"
    assert config.memory.enabled is False
    assert config.memory.auto_extract is False
    assert config.memory.auto_summarize is True
    assert config.memory.inject_relevant is False
    assert config.memory.max_injected_items == 3
    assert config.memory.max_injected_tokens == 500
    assert config.memory.archive_sessions is False


def test_heartbeat_prompt_and_interval_parser(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()

    assert parse_heartbeat_interval("every 30 minutes", 60) == 30
    assert parse_heartbeat_interval("1h", 60) == 60
    assert parse_heartbeat_interval("", 15) == 15
    prompt = heartbeat_prompt(config, surface="tui")
    assert "Run a Libre Claw heartbeat check" in prompt
    assert "blocked approvals" in prompt


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
    assert config.telegram.default_provider == "openrouter"
    assert config.telegram.default_model == "qwen/qwen3.7-max"
    assert config.providers["openrouter"]["default_model"] == "qwen/qwen3.7-max"
    assert config.tui.show_file_tree is False
    assert config.tui.mouse is False
    assert config.tui.inline is False


def test_tui_mouse_and_inline_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / ".libre-claw" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[tui]",
                "mouse = true",
                "inline = true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config()

    assert config.tui.mouse is True
    assert config.tui.inline is True


def test_set_global_working_directory_updates_user_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "Documents" / ".workspace" / "libre-claw"
    workspace.mkdir(parents=True)

    written = set_global_working_directory(workspace)

    assert written == user_config_path()
    text = written.read_text(encoding="utf-8")
    assert f'working_directory = "{workspace}"' in text
    assert load_config().general.working_directory == workspace


def test_configure_telegram_updates_user_config_without_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    written = configure_telegram((123, 456), use_daemon=True)

    assert written == user_config_path()
    text = written.read_text(encoding="utf-8")
    assert "[telegram]" in text
    assert "enabled = true" in text
    assert "use_daemon = true" in text
    assert "allowed_user_ids = [123, 456]" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "bot_token =" not in text
    config = load_config()
    assert config.telegram.enabled is True
    assert config.telegram.use_daemon is True
    assert config.telegram.allowed_user_ids == (123, 456)


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
