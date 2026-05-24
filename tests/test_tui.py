# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import load_config
from libre_claw.tui.app import LibreClawApp, _effective_model, _replace_general


def test_tui_can_start_without_anthropic_api_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app = LibreClawApp(config=load_config())

    assert app.agent is None
    assert app.provider_error is not None
    assert "ANTHROPIC_API_KEY" in app.provider_error


def test_tui_phase_four_helper_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    assert "0.1.0" in app.SUB_TITLE
    assert app._palette_matches("cost")[0].name == "/cost"
    assert "provider:model" not in app._status_text()
    assert app._palette_matches("memory")[0].name == "/memory"
    assert app._palette_matches("telegram")[0].name == "/telegram"
    assert app._slash_suggestion_matches("/")[0].name == "/help"
    assert [command.name for command in app._slash_suggestion_matches("/m")] == ["/model", "/memory"]
    assert app._slash_suggestion_matches("/memory ") == []


def test_tui_diff_text(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    diff = app._diff_text("old\nsame", "new\nsame", "file.txt")

    assert "--- file.txt before" in diff
    assert "+++ file.txt after" in diff
    assert "-old" in diff
    assert "+new" in diff


def test_replace_general_updates_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()

    updated = _replace_general(config, default_model="claude-test")

    assert updated.general.default_model == "claude-test"
    assert config.general.default_model == "claude-sonnet-4-6"


def test_effective_model_uses_provider_default_when_switching_to_openai(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = _replace_general(load_config(), default_provider="openai")

    assert _effective_model(config) == "gpt-4o"


def test_effective_model_uses_provider_default_when_switching_to_local(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = _replace_general(load_config(), default_provider="local")

    assert _effective_model(config) == "qwen3:32b"


def test_slash_suggestion_completion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    model_command = app._slash_suggestion_matches("/mo")[0]
    help_command = app._slash_suggestion_matches("/he")[0]
    app._slash_suggestions = [model_command]

    assert app._completion_text(model_command) == "/model "
    assert app._completion_text(help_command) == "/help"
    assert app._should_complete_on_submit("/mo") is True
    assert app._should_complete_on_submit("/model") is False


async def test_tui_mounts_phase_four_layout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        assert app.query_one("#chat")
        assert app.query_one("#input")
        assert app.query_one("#sidebar")
        assert app.query_one("#palette")
