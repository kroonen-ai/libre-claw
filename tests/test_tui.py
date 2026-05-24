# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from libre_claw.config import load_config
from libre_claw.core.agent import AgentPermissionRequest
from libre_claw.core.tools import ToolCall
from libre_claw.tui.app import (
    ASSISTANT_ACCENT,
    LibreClawApp,
    STARTUP_ASCII,
    TranscriptEntry,
    _effective_model,
    _model_help_text,
    _parse_model_argument,
    _replace_general,
    _startup_message,
)


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


def test_effective_model_uses_provider_default_when_switching_to_ollama(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = _replace_general(load_config(), default_provider="ollama")

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


def test_model_argument_parses_provider_and_colon_model() -> None:
    assert _parse_model_argument("kimi-k2.6:cloud", "ollama") == ("ollama", "kimi-k2.6:cloud")
    assert _parse_model_argument("openrouter:openai/gpt-4o", "ollama") == ("openrouter", "openai/gpt-4o")
    assert _parse_model_argument("openrouter openai/gpt-4o", "ollama") == ("openrouter", "openai/gpt-4o")
    assert _parse_model_argument("list", "ollama") is None


def test_model_help_includes_enrollment_commands(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()

    help_text = _model_help_text(config)

    assert "Current model: anthropic:claude-sonnet-4-6" in help_text
    assert "libre-claw auth set-key openrouter" in help_text
    assert "/model openrouter:openrouter/auto" in help_text


def test_model_argument_suggestions_complete_provider_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    suggestions = app._slash_suggestion_matches("/model openr")
    first = suggestions[0]

    assert first.name == "/model openrouter:openrouter/auto"
    assert app._completion_text(first) == "/model openrouter:openrouter/auto"
    app._slash_suggestions = [first]
    assert app._should_complete_on_submit("/model openr") is True
    assert app._should_complete_on_submit("/model openrouter:openrouter/auto") is False


def test_assistant_label_uses_purple_accent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    renderable = app._format_entry(TranscriptEntry(role="assistant", content="hello"))

    assert ASSISTANT_ACCENT in str(renderable.renderables[0].style)


def test_startup_message_includes_ascii_art_and_release_notes() -> None:
    message = _startup_message()

    assert STARTUP_ASCII.strip() in message
    assert "## 0.1.0" in message
    assert "Type /help for commands." in message


def test_ctrl_c_binding_exits_app() -> None:
    binding = next(binding for binding in LibreClawApp.BINDINGS if binding.key == "ctrl+c")

    assert binding.action == "quit_app"
    assert binding.description == "Exit"


async def test_tui_mounts_phase_four_layout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        assert app.query_one("#chat")
        assert app.query_one("#input")
        assert app.query_one("#sidebar")
        assert app.query_one("#file-tree")
        assert app.query_one("#sidebar-up")
        assert app.query_one("#palette")
        assert app.query_one("#permission-panel").has_class("hidden")


async def test_tui_main_panel_avoids_vertical_divider_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        workspace = app.query_one("#workspace")
        sidebar = app.query_one("#sidebar")
        file_tree = app.query_one("#file-tree")
        main = app.query_one("#main")
        chat = app.query_one("#chat")
        input_box = app.query_one("#input")

        assert workspace.styles.border.top[0] == "solid"
        assert workspace.styles.border.left[0] == ""
        assert workspace.styles.border.right[0] == ""
        assert sidebar.styles.border.top[0] == ""
        assert sidebar.styles.border_right[0] == ""
        assert sidebar.region.height == main.region.height
        assert file_tree.region.x == sidebar.region.x
        assert chat.region.x == input_box.region.x
        assert chat.region.width == input_box.region.width
        assert main.styles.border_left[0] == ""
        assert chat.styles.border.top[0] == ""
        assert input_box.styles.border.top[0] == "solid"


async def test_tui_scrollbars_use_blue_accent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        for selector in ("#workspace", "#sidebar", "#file-tree", "#main", "#chat", "#input"):
            styles = app.query_one(selector).styles

            assert styles.scrollbar_color.hex == "#0070F3"
            assert styles.scrollbar_color_hover.hex == "#0070F3"
            assert styles.scrollbar_color_active.hex == "#0070F3"
            assert styles.scrollbar_size_vertical == 1
            assert styles.scrollbar_size_horizontal == 1


async def test_file_tree_up_updates_agent_working_directory(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(project)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config(working_directory=project))

    async with app.run_test(size=(120, 45)) as pilot:
        app._go_up_directory()
        await pilot.pause()

        assert app.config.general.working_directory == tmp_path.resolve()
        assert app.query_one("#file-tree").path == tmp_path.resolve()
        assert app.query_one("#sidebar-root").content == f"cwd: {tmp_path.resolve()}"


async def test_tui_permission_panel_resolves_exact_call(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        request = AgentPermissionRequest(
            call=ToolCall(id="toolu_1", name="bash", arguments={"command": "pwd"}),
            future=future,
        )

        app._pending_permission = request
        app._show_permission_prompt(request)

        assert not app.query_one("#permission-panel").has_class("hidden")
        assert app.query_one("#permission-warning").content == (
            "Choose once, always for this tool, or always for this exact command."
        )

        app._resolve_pending_permission("always_allow_call")

        assert future.result() == "always_allow_call"
        assert app.query_one("#permission-panel").has_class("hidden")


async def test_tui_permission_panel_warns_for_dangerous_commands(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        request = AgentPermissionRequest(
            call=ToolCall(id="toolu_1", name="bash", arguments={"command": "rm -rf /"}),
            future=future,
        )

        app._pending_permission = request
        app._show_permission_prompt(request)

        assert "Warning: Command blocked by sandbox pattern: rm -rf /" in str(
            app.query_one("#permission-warning").content
        )
        assert app.query_one("#permission-always-tool").disabled is True
        assert app.query_one("#permission-always-call").disabled is True

        app._resolve_pending_permission("always_allow_call")

        assert future.done() is False
        assert app.query_one("#permission-panel").has_class("hidden") is False

        app._resolve_pending_permission("allow_once")

        assert future.result() == "allow_once"
        assert app.query_one("#permission-panel").has_class("hidden")
