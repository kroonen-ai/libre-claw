# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import subprocess
from dataclasses import replace
from pathlib import Path

from rich.console import Console
from rich.segment import Segment
from textual.geometry import Offset
from textual.selection import Selection
from textual.strip import Strip

from libre_claw.auth.api_keys import ApiKeyLookup
from libre_claw.auth.codex import CodexStatus
from libre_claw.config import load_config
from libre_claw.core.automations import AutomationStore
from libre_claw.core.agent import (
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
)
from libre_claw.core.runs import RunEvent, RunStore
from libre_claw.core.tools import ToolCall, ToolResult
from libre_claw.providers import Usage
from libre_claw.tui.app import (
    ASSISTANT_ACCENT,
    ContextMeter,
    LibreClawApp,
    PROJECT_NOTICE,
    PROJECT_LINKS,
    SelectableRichLog,
    STARTUP_ASCII,
    STREAM_RENDER_MAX_BUFFERED_CHARS,
    StreamRenderBuffer,
    TUI_IMAGE_ATTACHMENT_PROMPT,
    TranscriptEntry,
    _effective_model,
    _attachment_metadata,
    _attachment_summary,
    _context_bar,
    _format_token_count,
    _collect_run_artifacts,
    _model_help_text,
    _parse_tui_image_input,
    _parse_compact_options,
    _parse_schedule_command,
    _parse_skills_command,
    _parse_model_argument,
    _replace_general,
    _run_verification_text,
    _rich_log_selection_text,
    _startup_message,
    _startup_renderable,
    _tool_preview,
    _transcript_from_run_events,
)


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeDaemonClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.resolutions: list[tuple[str, str, str]] = []
        self.model_updates: list[tuple[str, str, bool]] = []
        self.model_payload = {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"}
        self._served = False

    async def current_model(self):
        return self.model_payload

    async def update_model(self, provider: str, model: str, *, persist_global: bool = False):
        self.model_updates.append((provider, model, persist_global))
        self.model_payload = {"provider": provider, "model": model}
        return {"provider": provider, "model": model, "persisted_path": None}

    async def start_run(self, message: str, **payload):
        del message, payload
        return {"run": {"run_id": "run-daemon", "state": "queued"}}

    async def get_events(self, run_id: str, after: int = 0):
        del run_id, after
        if self._served:
            return {"events": []}
        self._served = True
        return {
            "events": [
                {"event_id": 1, "type": "assistant_delta", "data": {"text": "hello from daemon"}},
                {"event_id": 2, "type": "run_finished", "data": {"state": "done"}},
            ]
        }

    async def get_run(self, run_id: str):
        del run_id
        return {"run": {"run_id": "run-daemon", "state": "done"}}

    async def cancel_run(self, run_id: str):
        self.cancelled.append(run_id)
        return {"run_id": run_id, "cancelled": True}

    async def health(self):
        return {"ok": True, "active_runs": 1, "telegram_bridge": "running"}

    async def resolve_permission(self, run_id: str, tool_call_id: str, resolution: str):
        self.resolutions.append((run_id, tool_call_id, resolution))
        return {"run_id": run_id, "tool_call_id": tool_call_id, "resolution": resolution}


class FakeApiKeyStore:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def set_api_key(self, provider: str, api_key: str) -> str:
        self.keys[provider] = api_key
        return "encrypted_file"

    def get_api_key(self, provider: str, env_var: str | None = None) -> ApiKeyLookup:
        del env_var
        value = self.keys.get(provider)
        return ApiKeyLookup(value=value, source="encrypted_file" if value else "missing")

    def key_status(self, providers: list[tuple[str, str | None]]) -> dict[str, str]:
        return {provider: "encrypted_file" if provider in self.keys else "missing" for provider, _env in providers}


class FakeAttachmentAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def run(self, user_message: str, attachments=()):
        self.calls.append((user_message, tuple(attachments)))
        yield AgentTextDelta("seen")
        yield AgentDone()


def test_tui_can_start_without_anthropic_api_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app = LibreClawApp(config=load_config())

    assert app.agent is None
    assert app.provider_error is not None
    assert "ANTHROPIC_API_KEY" in app.provider_error


def test_tui_daemon_mode_skips_local_provider_setup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config()
    config = replace(config, tui=replace(config.tui, use_daemon=True))

    app = LibreClawApp(config=config)

    assert app.daemon_client is not None
    assert app.agent is None
    assert app.provider_error is None


def test_tui_phase_four_helper_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    assert "0.1.0" in app.SUB_TITLE
    assert app._palette_matches("cost")[0].name == "/cost"
    assert app._palette_matches("status")[0].name == "/status"
    assert "provider:model" not in app._status_text()
    assert "ctx [" in app._status_text()
    assert app._status_text().endswith(" | idle")
    assert app._palette_matches("memory")[0].name == "/memory"
    assert app._palette_matches("telegram")[0].name == "/telegram"
    assert app._slash_suggestion_matches("/")[0].name == "/help"
    assert app._slash_suggestion_matches("/bt")[0].name == "/btw"
    assert app._slash_suggestion_matches("/ste")[0].name == "/steer"
    assert [command.name for command in app._slash_suggestion_matches("/m")] == ["/model", "/models", "/memory"]
    assert app._slash_suggestion_matches("/g")[0].name == "/goal"
    assert app._slash_suggestion_matches("/memory ")[0].name == "/memory status"
    assert app._slash_suggestion_matches("/w")[0].name == "/workspace"
    assert app._slash_suggestion_matches("/workspace ")[0].name == "/workspace status"


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
    assert config.general.default_model == "claude-opus-4-8"


def test_effective_model_uses_provider_default_when_switching_to_openai(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = _replace_general(load_config(), default_provider="openai")

    assert _effective_model(config) == "gpt-5.5"


def test_effective_model_uses_provider_default_when_switching_to_ollama(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = _replace_general(load_config(), default_provider="ollama")

    assert _effective_model(config) == "qwen3.6:27b"


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


async def test_slash_suggestions_support_arrow_selection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        input_widget = app.query_one("#input")
        input_widget.value = "/m"
        app._update_slash_suggestions(input_widget.value)

        assert app._slash_suggestions[0].name == "/model"
        assert app._slash_suggestion_text(app._slash_suggestions).splitlines()[0].startswith("> /model")

        assert app._move_menu_selection(1) is True
        assert app._slash_suggestion_text(app._slash_suggestions).splitlines()[1].startswith("> /models")

        app.action_accept_suggestion()

    assert input_widget.value == "/models"


async def test_command_palette_supports_arrow_selection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        app.action_command_palette()
        assert app.palette_open is True
        assert app._palette_text("").splitlines()[1].startswith("> /help")

        assert app._move_menu_selection(1) is True
        assert app._palette_text("").splitlines()[2].startswith("> /status")

        await app._handle_palette_input("")

    assert app.palette_open is False
    assert any("Libre Claw status" in entry.content for entry in app.transcript)


def test_selectable_rich_log_extracts_rendered_text() -> None:
    log = SelectableRichLog()
    log.lines = [
        Strip([Segment("System: selectable text    ")]),
        Strip([Segment("next line")]),
    ]

    selected = log.get_selection(Selection(Offset(8, 0), Offset(18, 0)))

    assert _rich_log_selection_text(log.lines) == "System: selectable text\nnext line"
    assert selected == ("selectable", "\n")


async def test_copy_shortcut_prefers_active_text_selection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        chat = app.query_one("#chat", SelectableRichLog)
        chat.clear()
        chat.write("copy this text")
        app.screen.selections = {chat: Selection(Offset(0, 0), Offset(4, 0))}
        app.action_copy_last_response()

    assert app.clipboard == "copy"
    assert any("Copied selected text to clipboard." in entry.content for entry in app.transcript)


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

    assert "Current model: anthropic:claude-opus-4-8" in help_text
    assert "Add `--global`" in help_text
    assert "libre-claw auth set-key openrouter" in help_text
    assert "/model openrouter:openrouter/auto" in help_text


def test_model_argument_suggestions_complete_provider_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    suggestions = app._slash_suggestion_matches("/model openr")
    first = suggestions[0]

    assert first.name == "/model openrouter:deepseek/deepseek-v4-flash"
    assert app._completion_text(first) == "/model openrouter:deepseek/deepseek-v4-flash"
    app._slash_suggestions = [first]
    assert app._should_complete_on_submit("/model openr") is True
    assert app._should_complete_on_submit("/model openrouter:deepseek/deepseek-v4-flash") is False

    ollama_suggestions = app._slash_suggestion_matches("/model minimax-m3")
    assert any(suggestion.name == "/model ollama:minimax-m3:cloud" for suggestion in ollama_suggestions)
    assert any(suggestion.name == "/model openrouter:minimax/minimax-m3" for suggestion in ollama_suggestions)


def test_tui_parses_pasted_image_path(tmp_path: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(TINY_PNG)

    parsed = _parse_tui_image_input(f"inspect {image}", tmp_path)

    assert parsed.message == "inspect"
    assert parsed.warnings == ()
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert attachment.media_type == "image/png"
    assert attachment.filename == "shot.png"
    assert attachment.path == str(image)
    assert base64.b64decode(attachment.data).startswith(b"\x89PNG")
    assert _attachment_metadata(attachment) == {
        "media_type": "image/png",
        "filename": "shot.png",
        "path": str(image),
    }
    assert "type: image/png" in _attachment_summary(attachment)


def test_tui_parses_image_data_url() -> None:
    encoded = base64.b64encode(TINY_PNG).decode("ascii")

    parsed = _parse_tui_image_input(f"look data:image/png;base64,{encoded}", Path.cwd())

    assert parsed.message == "look"
    assert parsed.warnings == ()
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "pasted-image.png"
    assert parsed.attachments[0].media_type == "image/png"


async def test_tui_attach_command_queues_image_for_next_prompt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    image = tmp_path / "shot.png"
    image.write_bytes(TINY_PNG)
    app = LibreClawApp(config=load_config())
    fake_agent = FakeAttachmentAgent()

    async with app.run_test(size=(120, 45)):
        app.agent = fake_agent  # type: ignore[assignment]
        await app._handle_command(f"/attach {image}")
        assert len(app._pending_attachments) == 1
        await app.handle_user_input("what is this?")
        assert app._active_task is not None
        await app._active_task

    assert fake_agent.calls
    user_message, attachments = fake_agent.calls[0]
    assert user_message == "what is this?"
    assert attachments[0].filename == "shot.png"  # type: ignore[attr-defined]
    assert any(entry.role == "attachment" and "shot.png" in (entry.title or "") for entry in app.transcript)


async def test_tui_pasted_image_only_uses_default_prompt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    image = tmp_path / "shot.png"
    image.write_bytes(TINY_PNG)
    app = LibreClawApp(config=load_config())
    fake_agent = FakeAttachmentAgent()

    async with app.run_test(size=(120, 45)):
        app.agent = fake_agent  # type: ignore[assignment]
        await app.handle_user_input(str(image))
        assert app._active_task is not None
        await app._active_task

    assert fake_agent.calls[0][0] == TUI_IMAGE_ATTACHMENT_PROMPT
    assert fake_agent.calls[0][1][0].filename == "shot.png"  # type: ignore[attr-defined]


def test_heartbeat_suggestions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = LibreClawApp(config=load_config())

    suggestions = app._slash_suggestion_matches("/heartbeat ")

    assert any(suggestion.name == "/heartbeat once" for suggestion in suggestions)
    assert any(suggestion.name == "/heartbeat stop" for suggestion in suggestions)


async def test_model_global_flag_persists_user_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/model openrouter:qwen/qwen3.7-max --global")

    config_path = tmp_path / ".libre-claw" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    assert 'default_provider = "openrouter"' in text
    assert 'default_model = "qwen/qwen3.7-max"' in text
    assert app.config.general.default_provider == "openrouter"
    assert app.config.general.default_model == "qwen/qwen3.7-max"
    assert any("Saved as global default" in entry.content for entry in app.transcript)


async def test_model_global_flag_updates_next_launch_for_codex(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    user_path = tmp_path / ".libre-claw" / "config.toml"
    user_path.parent.mkdir(parents=True)
    user_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "openrouter"',
                'default_model = "deepseek/deepseek-v4-flash"',
                "",
                "[providers.openrouter]",
                'default_model = "deepseek/deepseek-v4-flash"',
            ]
        ),
        encoding="utf-8",
    )
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/model codex:gpt-5.5 --global")

    reloaded = load_config()
    assert reloaded.general.default_provider == "codex"
    assert reloaded.general.default_model == "gpt-5.5"
    assert reloaded.providers["codex"]["default_model"] == "gpt-5.5"
    assert app.config.general.default_provider == "codex"
    assert app.config.general.default_model == "gpt-5.5"


async def test_tui_syncs_changed_global_model_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        config_path = tmp_path / ".libre-claw" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "\n".join(
                [
                    "[general]",
                    'default_provider = "openrouter"',
                    'default_model = "deepseek/deepseek-v4-pro"',
                    "",
                    "[providers.openrouter]",
                    'default_model = "deepseek/deepseek-v4-pro"',
                ]
            ),
            encoding="utf-8",
        )
        app._global_model_config_mtime_ns = None
        app._sync_global_model_if_changed()

    assert app.config.general.default_provider == "openrouter"
    assert app.config.general.default_model == "deepseek/deepseek-v4-pro"
    assert app.config.providers["openrouter"]["default_model"] == "deepseek/deepseek-v4-pro"
    assert any("Global model changed to openrouter:deepseek/deepseek-v4-pro" in entry.content for entry in app.transcript)


async def test_tui_syncs_changed_global_theme_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        config_path = tmp_path / ".libre-claw" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("[general]\ntheme = \"matrix\"\n", encoding="utf-8")
        app._global_model_config_mtime_ns = None
        app._sync_global_model_if_changed()

        chat = app.query_one("#chat")
        assert app.config.general.theme == "matrix"
        assert chat.styles.scrollbar_color.hex == "#00FF41"

    assert any("Global theme changed to Matrix" in entry.content for entry in app.transcript)


async def test_tui_daemon_mode_syncs_daemon_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config()
    config = replace(config, tui=replace(config.tui, use_daemon=True))
    app = LibreClawApp(config=config)
    daemon = FakeDaemonClient()
    daemon.model_payload = {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4-pro",
        "context_window_tokens": 524_288,
        "detected_max_completion_tokens": 16_384,
        "detected_context_source": "models",
    }
    app.daemon_client = daemon  # type: ignore[assignment]

    async with app.run_test():
        await app._sync_daemon_model_if_changed()

    assert app.config.general.default_provider == "openrouter"
    assert app.config.general.default_model == "deepseek/deepseek-v4-pro"
    assert app.config.agent.context_window_tokens == 524_288
    assert app.config.providers["openrouter"]["detected_max_completion_tokens"] == 16_384
    assert app.config.providers["openrouter"]["default_model"] == "deepseek/deepseek-v4-pro"
    assert "openrouter:deepseek/deepseek-v4-pro" in app._status_text()
    assert any("Daemon model changed to openrouter:deepseek/deepseek-v4-pro" in entry.content for entry in app.transcript)


async def test_tui_accepts_telegram_style_status_and_alias_commands(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config()
    config = replace(config, tui=replace(config.tui, use_daemon=True))
    app = LibreClawApp(config=config)
    daemon = FakeDaemonClient()
    app.daemon_client = daemon  # type: ignore[assignment]

    async with app.run_test():
        await app._handle_command("/new")
        await app._handle_command("/status")
        await app._handle_command("/models")
        await app._handle_command("/daemon")

    system_text = "\n".join(entry.content for entry in app.transcript if entry.role == "system")
    assert "Libre Claw status" in system_text
    assert "- Provider: `openrouter`" in system_text
    assert "Daemon" in system_text
    assert "- Telegram bridge: running" in system_text
    assert "Suggested models:" in system_text
    assert "Transcript cleared." in system_text


async def test_tui_daemon_mode_model_command_updates_daemon(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = load_config()
    config = replace(config, tui=replace(config.tui, use_daemon=True))
    app = LibreClawApp(config=config)
    daemon = FakeDaemonClient()
    app.daemon_client = daemon  # type: ignore[assignment]

    async with app.run_test():
        await app._handle_command("/model openrouter:deepseek/deepseek-v4-pro")
        await asyncio.gather(*app._run_background_tasks)

    assert app.config.general.default_provider == "openrouter"
    assert app.config.general.default_model == "deepseek/deepseek-v4-pro"
    assert daemon.model_updates == [("openrouter", "deepseek/deepseek-v4-pro", False)]


async def test_goal_commands_update_session_limit_and_report_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/goal max 5")
        await app._handle_command("/goal status")

    assert app._goal_max_turns == 5
    assert any("Goal max turns set to 5" in entry.content for entry in app.transcript)
    assert any("No active goal. Max turns: 5." in entry.content for entry in app.transcript)


def test_skills_command_parser_handles_scopes_and_content() -> None:
    assert _parse_skills_command("") == {"action": "list"}
    assert _parse_skills_command("add --project release-flow run pytest") == {
        "action": "add",
        "scope": "project",
        "name": "release-flow",
        "content": "run pytest",
    }
    assert _parse_skills_command("delete --user release-flow") == {
        "action": "delete",
        "scope": "user",
        "name": "release-flow",
    }


def test_schedule_command_parser_handles_add_and_mutations() -> None:
    assert _parse_schedule_command("") == {"action": "list"}
    assert _parse_schedule_command("examples") == {"action": "examples"}
    assert _parse_schedule_command("pause auto-1") == {"action": "pause", "automation_id": "auto-1"}
    assert _parse_schedule_command("add --route tui daily 09:00 | Daily | Check repo") == {
        "action": "add",
        "route": "tui",
        "schedule": "daily 09:00",
        "name": "Daily",
        "prompt": "Check repo",
    }


async def test_skills_commands_manage_user_and_project_skills(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/skills add --project pytest-debug # Pytest Debug")
        await app._handle_command("/skills list")
        await app._handle_command("/skills show pytest-debug")
        await app._handle_command("/skills edit --project pytest-debug # Pytest Debug Updated")
        await app._handle_command("/skills delete --project pytest-debug")

    assert any("Added project skill pytest-debug" in entry.content for entry in app.transcript)
    assert any("project:pytest-debug" in entry.content for entry in app.transcript)
    assert any("Skill: Pytest Debug" in entry.content for entry in app.transcript)
    assert any("Updated project skill pytest-debug" in entry.content for entry in app.transcript)
    assert any("Skill deleted." in entry.content for entry in app.transcript)
    assert not (tmp_path / ".libre-claw" / "skills" / "pytest-debug.md").exists()


async def test_schedule_commands_manage_recurring_runs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.automation_store = AutomationStore(tmp_path / "automations")

    async with app.run_test():
        await app._handle_command("/schedule examples")
        await app._handle_command("/schedule add daily 09:00 | Daily | Check repo")
        automation = (await app.automation_store.list())[0]
        await app._handle_command("/schedule list")
        await app._handle_command(f"/schedule pause {automation.automation_id}")
        await app._handle_command(f"/schedule resume {automation.automation_id}")
        await app._handle_command(f"/schedule delete {automation.automation_id}")

    assert any("Daily repo health check" in entry.content for entry in app.transcript)
    assert any("Scheduled:" in entry.content for entry in app.transcript)
    assert any("Schedules:" in entry.content for entry in app.transcript)
    assert any("Updated schedule:" in entry.content for entry in app.transcript)
    assert any("Deleted schedule" in entry.content for entry in app.transcript)
    assert await app.automation_store.list() == []


async def test_tui_daemon_mode_streams_daemon_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, tui=replace(config.tui, use_daemon=True))
    app = LibreClawApp(config=config)
    app.daemon_client = FakeDaemonClient()  # type: ignore[assignment]

    async with app.run_test():
        await app.handle_user_input("hello")
        assert app._active_task is not None
        await app._active_task

    assert any(entry.role == "assistant" and entry.content == "hello from daemon" for entry in app.transcript)
    assert any("Daemon run run-daemon started" in entry.content for entry in app.transcript)


async def test_run_commands_list_inspect_resume_and_cancel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.run_store = RunStore(tmp_path / "runs")
    run = await app.run_store.create_run("test run", kind="chat", provider="openrouter", model="openrouter/auto")
    await app.run_store.append_event(run.run_id, "user_message", {"content": "hello"})
    await app.run_store.append_event(run.run_id, "assistant_delta", {"text": "hi"})

    async with app.run_test():
        await app._handle_command("/runs")
        await app._handle_command(f"/run {run.run_id}")
        await app._handle_command(f"/resume {run.run_id}")
        await app._handle_command(f"/cancel {run.run_id}")

    loaded = await app.run_store.load_run(run.run_id)

    assert loaded is not None
    assert loaded.state == "cancelled"
    assert any(run.run_id in entry.content for entry in app.transcript if entry.role == "system")
    assert any(entry.role == "user" and entry.content == "hello" for entry in app.transcript)
    assert any(entry.role == "assistant" and entry.content == "hi" for entry in app.transcript)


async def test_artifact_panel_and_changes_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.run_store = RunStore(tmp_path / "runs")
    run = await app.run_store.create_run("artifact run", kind="chat", provider="openrouter", model="openrouter/auto")
    await app.run_store.append_event(run.run_id, "user_message", {"content": "hello"})
    await app.run_store.append_event(run.run_id, "assistant_delta", {"text": "I will check it."})
    await app.run_store.finish_run(
        run.run_id,
        "done",
        plan="I will check it.\n",
        summary="Done.\n",
        verification="Verified.\n",
        diff="diff --git a/a b/a\n",
    )

    async with app.run_test():
        await app._handle_command(f"/artifacts diff {run.run_id}")
        await app._handle_command(f"/changes {run.run_id}")

    assert app._artifact_visible is True
    assert app._artifact_tab == "diff"
    assert (run.path / "last_seen.json").exists()
    assert any("Changes for" in entry.content for entry in app.transcript if entry.role == "system")


async def test_approvals_command_lists_blocked_runs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.run_store = RunStore(tmp_path / "runs")
    run = await app.run_store.create_run(
        "blocked run",
        kind="chat",
        provider="openrouter",
        model="openrouter/auto",
        state="blocked",
    )
    await app.run_store.append_event(
        run.run_id,
        "permission_request",
        {"tool_call_id": "toolu_1", "name": "bash", "arguments": {"command": "date"}},
    )

    async with app.run_test():
        await app._handle_command("/approvals")

    assert any("Blocked approval inbox" in entry.content for entry in app.transcript)
    assert any("toolu_1" in entry.content for entry in app.transcript)


async def test_usage_command_reports_openrouter_run_rollups(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.run_store = RunStore(tmp_path / "runs")
    run = await app.run_store.create_run(
        "usage run",
        kind="chat",
        provider="openrouter",
        model="qwen/qwen3.7-max",
    )
    await app.run_store.append_event(run.run_id, "run_started", {"surface": "tui:chat"})
    await app.run_store.append_event(run.run_id, "usage", {"input_tokens": 7, "output_tokens": 3, "cost": 0.0001})

    async with app.run_test():
        await app._handle_command("/usage openrouter")
        await app._handle_command("/usage openrouter attribution")
        await app._handle_command("/usage openrouter presets")

    system_text = "\n".join(entry.content for entry in app.transcript if entry.role == "system")
    assert "OpenRouter usage" in system_text
    assert "qwen/qwen3.7-max" in system_text
    assert "tui:chat" in system_text
    assert "https://openrouter.ai/apps?url=https://libreclaw.sh" in system_text
    assert "/model openrouter:qwen/qwen3.7-max --global" in system_text


async def test_setup_key_flow_hides_and_stores_provider_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_store = FakeApiKeyStore()
    monkeypatch.setattr("libre_claw.tui.app.ApiKeyStore.from_config", lambda _auth: fake_store)
    monkeypatch.setattr(
        "libre_claw.tui.app.codex_status",
        lambda: asyncio.sleep(0, result=CodexStatus(available=True, logged_in=False, detail="missing")),
    )
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/setup key openrouter")
        input_widget = app.query_one("#input")
        assert input_widget.password is True
        await app.handle_user_input("sk-or-secret")
        assert input_widget.password is False
        await app._handle_command("/setup status")

    assert fake_store.keys == {"openrouter": "sk-or-secret"}
    system_text = "\n".join(entry.content for entry in app.transcript if entry.role == "system")
    assert "Stored openrouter API key" in system_text
    assert "openrouter: encrypted_file" in system_text
    assert "sk-or-secret" not in system_text


async def test_transcript_from_run_events_reconstructs_tool_entries(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("tools", kind="chat", provider="openai", model="gpt-5.5")
    await store.append_event(run.run_id, "user_message", {"content": "whoami"})
    await store.append_event(run.run_id, "tool_call", {"id": "toolu_1", "name": "bash", "arguments": {"command": "whoami"}})
    await store.append_event(
        run.run_id,
        "tool_result",
        {"tool_call_id": "toolu_1", "name": "bash", "is_error": False, "content": "rob"},
    )

    entries = _transcript_from_run_events(await store.load_events(run.run_id))

    assert entries[0].role == "user"
    assert entries[1].role == "tool"
    assert entries[1].title == "bash result"
    assert entries[1].content == "rob"


def test_run_verification_text_summarizes_tool_results(tmp_path: Path) -> None:
    tool_event = RunEvent(
        event_id=1,
        timestamp="2026-05-25T00:00:00",
        type="tool_result",
        data={
            "name": "bash",
            "arguments": {"command": "pytest"},
            "is_error": False,
            "metadata": {"exit_code": 0, "duration_ms": 123},
        },
    )

    text = _run_verification_text(
        "done",
        [tool_event],
        tmp_path,
        git_status=" M src/file.py\n",
    )

    assert "Run finished with state: done" in text
    assert "bash: ok (exit_code=0, duration_ms=123, command='pytest')" in text
    assert " M src/file.py" in text


async def test_collect_run_artifacts_captures_git_diff(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("after\n", encoding="utf-8")

    verification, diff, browser = await _collect_run_artifacts(tmp_path, "done", [])

    assert "Git status at finish:" in verification
    assert " M tracked.txt" in verification
    assert "diff --git" in diff
    assert "+after" in diff
    assert "No browser artifacts" in browser


def test_assistant_label_uses_brand_accent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    renderable = app._format_entry(TranscriptEntry(role="assistant", content="hello"))

    assert ASSISTANT_ACCENT in str(renderable.renderables[0].style)


def test_startup_message_includes_ascii_art_and_release_notes() -> None:
    message = _startup_message()

    assert STARTUP_ASCII.strip() in message
    assert PROJECT_LINKS in message
    assert "## 0.1.0" in message
    assert "Type /help for commands." in message


def test_startup_renderable_collapses_release_notes() -> None:
    console = Console(record=True, width=160)
    console.print(_startup_renderable(False))
    collapsed = console.export_text()
    console = Console(record=True, width=160)
    console.print(_startup_renderable(True))
    expanded = console.export_text()

    assert STARTUP_ASCII.strip().splitlines()[0] in collapsed
    assert "release notes collapsed" in collapsed
    assert "Press Ctrl+R to expand" in collapsed
    assert PROJECT_LINKS in collapsed
    assert PROJECT_NOTICE in collapsed
    assert "## 0.1.0" not in collapsed
    assert PROJECT_LINKS in expanded
    assert PROJECT_NOTICE in expanded
    assert "0.1.0 - 2026-05-24" in expanded
    assert "Press Ctrl+R to collapse" in expanded


def test_stream_render_buffer_flushes_first_delta_then_throttles() -> None:
    buffer = StreamRenderBuffer(interval=0.05, max_buffered_chars=STREAM_RENDER_MAX_BUFFERED_CHARS)

    buffer.append("H")
    assert buffer.should_flush(1.0) is True
    assert buffer.flush(1.0) == "H"

    buffer.append("e")
    assert buffer.should_flush(1.01) is False
    buffer.append("llo")
    assert buffer.flush(1.02) == "ello"


def test_stream_render_buffer_flushes_large_batches() -> None:
    buffer = StreamRenderBuffer(interval=10.0, max_buffered_chars=5, last_flush_at=1.0, rendered_once=True)

    buffer.append("abc")
    assert buffer.should_flush(2.0) is False
    buffer.append("de")

    assert buffer.should_flush(2.0) is True


async def test_streaming_tail_updates_avoid_full_transcript_rerender(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        index = app._append_assistant("")
        full_renders = 0

        def count_full_render() -> None:
            nonlocal full_renders
            full_renders += 1

        monkeypatch.setattr(app, "_render_transcript", count_full_render)

        app._append_to_entry(index, "hello")
        app._append_to_entry(index, " world")

        assert app.transcript[index].content == "hello world"
        assert full_renders == 0


async def test_non_tail_entry_updates_fall_back_to_full_render(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        assistant_index = app._append_assistant("hello")
        app._append_system("after")
        full_renders = 0

        def count_full_render() -> None:
            nonlocal full_renders
            full_renders += 1

        monkeypatch.setattr(app, "_render_transcript", count_full_render)

        app._append_to_entry(assistant_index, " world")

        assert app.transcript[assistant_index].content == "hello world"
        assert full_renders == 1


async def test_shared_agent_stream_event_handler_renders_tools_usage_and_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        assistant_index = app._append_assistant("")
        buffer = StreamRenderBuffer(interval=0.0, max_buffered_chars=10)
        call = ToolCall(id="toolu_1", name="bash", arguments={"command": "pwd"})

        assert app._handle_agent_stream_event(
            AgentTextDelta("hello"),
            assistant_index,
            buffer,
            stop_on_error=True,
        ) == (True, False)
        app._flush_stream_buffer(assistant_index, buffer)
        assert app.transcript[assistant_index].content == "hello"

        assert app._handle_agent_stream_event(AgentToolCall(call), assistant_index, buffer, stop_on_error=True) == (
            True,
            False,
        )
        assert app._handle_agent_stream_event(
            AgentToolResult(call, ToolResult(content="ok")),
            assistant_index,
            buffer,
            stop_on_error=True,
        ) == (True, False)
        assert [entry.title for entry in app.transcript if entry.role == "tool"] == ["bash result"]

        assert app._handle_agent_stream_event(
            AgentDone(Usage(input_tokens=2, output_tokens=3)),
            assistant_index,
            buffer,
            stop_on_error=True,
        ) == (True, False)
        assert app.usage.input_tokens == 2
        assert app.usage.output_tokens == 3

        assert app._handle_agent_stream_event(
            AgentError("provider down"),
            assistant_index,
            buffer,
            stop_on_error=True,
        ) == (True, True)
        assert any(entry.content == "provider down" for entry in app.transcript)


def test_cost_text_shows_cumulative_provider_usage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.usage = Usage(input_tokens=10, output_tokens=5, cached_tokens=3, reasoning_tokens=2, cost=0.000071)

    text = app._cost_text()

    assert "Tokens: 15 total" in text
    assert "Input: 10" in text
    assert "Output: 5" in text
    assert "Cached input: 3" in text
    assert "Reasoning output: 2" in text
    assert "Cost: $0.000071" in text
    assert "Context estimate:" in text
    assert "$0.000071" in app._status_text()
    assert "15 provider tokens" in app._status_text()


def test_status_text_falls_back_to_estimated_tokens(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.session.add_user_message("hello context")

    status = app._status_text()

    assert "est tokens" in status
    assert "ctx [" in status


async def test_idle_status_refresh_does_not_repaint_when_text_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(100, 20)):
        status = app.query_one("#status")
        calls: list[str] = []
        original_update = status.update

        def update_once(renderable: object = "") -> object:
            calls.append(str(renderable))
            return original_update(renderable)

        status.update = update_once  # type: ignore[method-assign]

        app._update_status()
        app._update_status()

    assert len(calls) == 1


def test_context_bar_shows_small_nonzero_usage() -> None:
    meter = ContextMeter(estimated_tokens=1, context_window_tokens=200000, ratio=1 / 200000)

    assert _context_bar(meter) == "[#---------]"
    assert meter.display_percent == "<1%"


def test_format_token_count_compacts_large_values() -> None:
    assert _format_token_count(999) == "999"
    assert _format_token_count(1200) == "1.2k"
    assert _format_token_count(200000) == "200k"
    assert _format_token_count(1200000) == "1.2M"


def test_context_report_and_compact_option_helpers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    app.session.add_user_message("hello context")

    report = app._context_report()

    assert "Context: [" in report
    assert "estimated tokens" in report
    options = _parse_compact_options("--force --keep 4")
    assert options.force is True
    assert options.keep_last == 4
    assert _parse_compact_options("status").status is True
    assert _parse_compact_options("--keep nope").error is not None


async def test_compact_status_and_force_keep(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())
    for index in range(6):
        app.session.add_user_message(f"message {index}")

    async with app.run_test():
        await app._handle_command("/compact status")
        app._compact_context("--force --keep 2")

    assert len(app.session.messages) == 2
    assert app.session.summary is not None
    assert any("Context:" in entry.content for entry in app.transcript)
    assert any("Compacted context from 6 messages to 2" in entry.content for entry in app.transcript)


async def test_tui_btw_and_steer_store_future_turn_notes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_command("/btw prefer concise replies")
        await app._handle_command("/steer use the HN skill")

    summary = app.session.summary or ""
    assert "Side note: prefer concise replies" in summary
    assert "Steering instruction: use the HN skill" in summary
    assert any("Side note saved" in entry.content for entry in app.transcript)
    assert any("Steering instruction saved" in entry.content for entry in app.transcript)


async def test_tui_memory_commands_manage_searchable_items(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        await app._handle_memory_command("add Robin prefers EST")
        await app._handle_memory_command("search EST")
        await app._handle_memory_command("status")
        await app._handle_memory_command("off")

    assert any("Added memory" in entry.content for entry in app.transcript)
    assert any("Robin prefers EST" in entry.content for entry in app.transcript)
    assert any("Memory status" in entry.content for entry in app.transcript)
    assert app.memory_enabled is False


def test_ctrl_c_binding_exits_app() -> None:
    binding = next(binding for binding in LibreClawApp.BINDINGS if binding.key == "ctrl+c")

    assert binding.action == "quit_app"
    assert binding.description == "Exit"


def test_ctrl_r_binding_toggles_release_notes() -> None:
    binding = next(binding for binding in LibreClawApp.BINDINGS if binding.key == "ctrl+r")

    assert binding.action == "toggle_release_notes"
    assert binding.description == "Release Notes"


def test_transcript_scroll_bindings_are_available() -> None:
    bindings = {binding.key: binding for binding in LibreClawApp.BINDINGS}

    assert bindings["pageup"].action == "scroll_chat_up"
    assert bindings["pageup"].priority is True
    assert bindings["pagedown"].action == "scroll_chat_down"
    assert bindings["pagedown"].priority is True
    assert bindings["ctrl+home"].action == "scroll_chat_top"
    assert bindings["ctrl+home"].priority is True
    assert bindings["ctrl+end"].action == "scroll_chat_bottom"
    assert bindings["ctrl+end"].priority is True


async def test_tui_mounts_phase_four_layout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        assert app.query_one("#chat")
        assert app.transcript[0].role == "startup"
        assert app.query_one("#input")
        assert app.query_one("#sidebar-rail")
        assert app.query_one("#sidebar")
        assert app.query_one("#file-tree")
        assert app.query_one("#sidebar-hide")
        assert app.query_one("#sidebar").display is False
        assert app.query_one("#sidebar-rail").display is True
        assert app.query_one("#sidebar-show")
        assert app.query_one("#sidebar-up")
        assert app.query_one("#palette")
        assert app.query_one("#permission-panel").has_class("hidden")
        assert app.query_one("#input").cursor_blink is False


async def test_tui_release_notes_toggle_uses_startup_transcript_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        assert app.transcript[0].role == "startup"
        assert app.startup_expanded is False

        app.action_toggle_release_notes()

        assert app.transcript[0].role == "startup"
        assert app.startup_expanded is True


async def test_tui_preserves_chat_selection_when_rerendering(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(80, 18)):
        chat = app.query_one("#chat", SelectableRichLog)
        selection = Selection(Offset(0, 0), Offset(6, 0))
        app.screen.selections = {chat: selection}

        app._append_entry("system", "selection should survive")

        assert app.screen.selections.get(chat) is selection


async def test_tui_preserves_chat_scroll_when_reading_history(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(80, 12)) as pilot:
        for index in range(60):
            app._append_entry("system", f"history line {index}")
        await pilot.pause()

        chat = app.query_one("#chat", SelectableRichLog)
        assert chat.max_scroll_y > 0
        chat.scroll_to(y=3, animate=False, immediate=True)
        await pilot.pause()
        scroll_y = chat.scroll_y

        app._append_entry("system", "new line while reading history")
        await pilot.pause()

        assert chat.scroll_y == scroll_y


async def test_tui_main_panel_avoids_vertical_divider_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)) as pilot:
        workspace = app.query_one("#workspace")
        sidebar = app.query_one("#sidebar")
        sidebar_rail = app.query_one("#sidebar-rail")
        file_tree = app.query_one("#file-tree")
        main = app.query_one("#main")
        chat = app.query_one("#chat")
        input_box = app.query_one("#input")

        assert sidebar.display is False
        assert sidebar_rail.display is True
        app.action_toggle_sidebar()
        await pilot.pause()

        assert workspace.styles.border.top[0] == "solid"
        assert workspace.styles.border.left[0] == ""
        assert workspace.styles.border.right[0] == ""
        assert sidebar_rail.styles.border.top[0] == ""
        assert sidebar.styles.border.top[0] == ""
        assert sidebar.styles.border_right[0] == ""
        assert sidebar.region.height == main.region.height
        assert file_tree.region.x == sidebar.region.x
        assert chat.region.x == input_box.region.x
        assert chat.region.width == input_box.region.width
        assert main.styles.border_left[0] == ""
        assert chat.styles.border.top[0] == ""
        assert input_box.styles.border.top[0] == "solid"


async def test_tui_sidebar_left_rail_toggle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test():
        sidebar = app.query_one("#sidebar")
        rail = app.query_one("#sidebar-rail")

        assert sidebar.display is False
        assert rail.display is True

        app.action_toggle_sidebar()
        assert sidebar.display is True
        assert rail.display is False

        app.action_toggle_sidebar()
        assert sidebar.display is False
        assert rail.display is True


async def test_tui_scrollbars_use_brand_accent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        for selector in ("#workspace", "#sidebar", "#file-tree", "#main", "#input"):
            styles = app.query_one(selector).styles

            assert styles.scrollbar_color.hex == "#EF4444"
            assert styles.scrollbar_color_hover.hex == "#EF4444"
            assert styles.scrollbar_color_active.hex == "#EF4444"
            assert styles.scrollbar_size_vertical == 1
            assert styles.scrollbar_size_horizontal == 1

        chat_styles = app.query_one("#chat").styles
        assert chat_styles.scrollbar_color.hex == "#EF4444"
        assert chat_styles.scrollbar_size_vertical == 0
        assert chat_styles.scrollbar_size_horizontal == 0


async def test_tui_uses_configured_theme_palette(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config()
    themed = replace(config, general=replace(config.general, theme="matrix"))
    app = LibreClawApp(config=themed)

    async with app.run_test(size=(120, 45)):
        workspace = app.query_one("#workspace")
        chat = app.query_one("#chat")
        suggestions = app.query_one("#suggestions")
        input_box = app.query_one("#input")
        renderable = app._format_entry(TranscriptEntry(role="assistant", content="hello"))

        assert workspace.styles.background.hex == "#06100A"
        assert workspace.styles.border.top[1].hex == "#00FF41"
        assert workspace.styles.border.bottom[1].hex == "#00FF41"
        assert chat.styles.scrollbar_color.hex == "#00FF41"
        assert suggestions.styles.border.top[1].hex == "#00FF41"
        assert suggestions.styles.border.right[1].hex == "#00FF41"
        assert suggestions.styles.border.bottom[1].hex == "#00FF41"
        assert suggestions.styles.border.left[1].hex == "#00FF41"
        assert input_box.styles.border.top[1].hex == "#00FF41"
        assert "#00ff41" in str(renderable.renderables[0].style).lower()


async def test_theme_command_switches_and_persists_global_theme(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        suggestions = app._slash_suggestion_matches("/theme tok")
        assert suggestions[0].name == "/theme tokyo-night"

        await app._handle_command("/theme matrix --global")
        chat = app.query_one("#chat")
        renderable = app._format_entry(TranscriptEntry(role="assistant", content="hello"))

        assert app.config.general.theme == "matrix"
        assert chat.styles.scrollbar_color.hex == "#00FF41"
        assert "#00ff41" in str(renderable.renderables[0].style).lower()

    config_path = tmp_path / ".libre-claw" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    assert 'theme = "matrix"' in text
    assert load_config().general.theme == "matrix"
    assert any("Saved as global default" in entry.content for entry in app.transcript)


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


async def test_tool_call_updates_single_collapsed_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = LibreClawApp(config=load_config())

    async with app.run_test(size=(120, 45)):
        call = ToolCall(id="toolu_1", name="bash", arguments={"command": "whoami"})
        index = app._append_tool_call(call)
        result_index = app._append_tool_result(call, ToolResult(content="robinkroonen\n"))

        assert result_index == index
        tool_entries = [entry for entry in app.transcript if entry.role == "tool"]
        assert len(tool_entries) == 1
        assert tool_entries[0].title == "bash result"
        assert tool_entries[0].collapsed is True
        assert _tool_preview(tool_entries[0]) == "robinkroonen"
        assert "collapsed" not in str(app._format_entry(tool_entries[0], index))


def test_expanded_tool_output_is_limited() -> None:
    entry = TranscriptEntry(
        role="tool",
        title="bash result",
        content="\n".join(f"line {index}" for index in range(20)),
        collapsed=False,
        metadata={"status": "result"},
    )
    renderable = str(LibreClawApp(config=load_config())._format_entry(entry))

    assert "line 0" in renderable
    assert "line 11" in renderable
    assert "line 12" not in renderable
    assert "8 more lines hidden" in renderable


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
