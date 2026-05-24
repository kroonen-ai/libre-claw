# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import difflib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, RichLog, Static

from libre_claw import __version__
from libre_claw.config import GeneralConfig, LibreClawConfig, load_config
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    Session,
)
from libre_claw.core.memory import MemoryStore
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.sandbox import SandboxPolicy, SandboxViolation
from libre_claw.core.session import ChatMessage
from libre_claw.core.tools import ToolCall, ToolResult
from libre_claw.providers import ProviderConfigurationError, Usage, create_provider
from libre_claw.release import latest_release_notes
from libre_claw.tools_builtin import create_builtin_registry


TranscriptRole = Literal["user", "assistant", "system", "tool", "permission", "file"]


@dataclass
class TranscriptEntry:
    role: TranscriptRole
    content: str
    title: str | None = None
    collapsed: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    description: str


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "/help", "Show available commands"),
    SlashCommand("/clear", "/clear", "Clear transcript and session history"),
    SlashCommand("/cancel", "/cancel", "Cancel active generation or tool execution"),
    SlashCommand("/cost", "/cost", "Show token and cost summary"),
    SlashCommand("/model", "/model [provider:]<name>|list", "Choose or list models for new turns"),
    SlashCommand("/provider", "/provider anthropic|openai|openrouter|ollama", "Switch provider for new turns"),
    SlashCommand("/save", "/save [name]", "Save the current session"),
    SlashCommand("/load", "/load <name>", "Load a saved session"),
    SlashCommand("/compact", "/compact", "Compact older context into the session summary"),
    SlashCommand("/memory", "/memory list|add <fact>|forget <id>", "Manage persistent memory facts"),
    SlashCommand("/telegram", "/telegram", "Show Telegram bridge status"),
    SlashCommand("/tools", "/tools expand|collapse|toggle <index>", "Control tool call details"),
    SlashCommand("/exit", "/exit", "Exit Libre Claw"),
)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "openrouter", "ollama")
MODEL_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-20250918", "Claude Opus 4"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1", "GPT-4.1"),
        ("o3", "o3 reasoning"),
        ("o4-mini", "o4-mini reasoning"),
        ("codex-mini", "Codex Mini"),
    ),
    "openrouter": (
        ("openrouter/auto", "OpenRouter automatic routing"),
        ("anthropic/claude-sonnet-4.5", "Claude through OpenRouter"),
        ("openai/gpt-4o", "GPT-4o through OpenRouter"),
        ("moonshotai/kimi-k2", "Kimi K2 through OpenRouter"),
    ),
    "ollama": (
        ("kimi-k2.6:cloud", "Kimi K2.6 on Ollama Cloud"),
        ("gpt-oss:120b", "GPT OSS 120B on Ollama"),
        ("qwen3:32b", "Qwen3 local daemon"),
    ),
}


PERMISSION_KEYS: dict[str, PermissionResolution] = {
    "y": "allow_once",
    "n": "deny",
    "a": "always_allow_tool",
    "!": "always_allow_call",
    "exclamation_mark": "always_allow_call",
}

ASSISTANT_ACCENT = "#8B5CF6"
STARTUP_ASCII = r"""
 _     _ _                 ____ _
| |   (_) |__  _ __ ___   / ___| | __ ___      __
| |   | | '_ \| '__/ _ \ | |   | |/ _` \ \ /\ / /
| |___| | |_) | | |  __/ | |___| | (_| |\ V  V /
|_____|_|_.__/|_|  \___|  \____|_|\__,_| \_/\_/
"""


class LibreClawApp(App[None]):
    """Textual application with streaming providers, tools, memory, and Telegram status."""

    TITLE = "Libre Claw"
    SUB_TITLE = "v0.1.0"

    CSS = """
    Screen {
        layout: vertical;
        background: #101418;
        color: #dce3ea;
        scrollbar-color: #0070F3;
        scrollbar-color-hover: #0070F3;
        scrollbar-color-active: #0070F3;
        scrollbar-background: #101418;
        scrollbar-background-hover: #101418;
        scrollbar-background-active: #101418;
        scrollbar-corner-color: #101418;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    Screen.light {
        background: #f7f7f2;
        color: #18212a;
        scrollbar-color: #0070F3;
        scrollbar-color-hover: #0070F3;
        scrollbar-color-active: #0070F3;
        scrollbar-background: #f7f7f2;
        scrollbar-background-hover: #f7f7f2;
        scrollbar-background-active: #f7f7f2;
        scrollbar-corner-color: #f7f7f2;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    #status {
        height: 1;
        background: #18212a;
        color: #f2f5f8;
        padding: 0 1;
    }

    Screen.light #status {
        background: #d9e2ec;
        color: #101418;
    }

    #workspace {
        height: 1fr;
        border: none;
        border-top: solid #0070F3;
        border-bottom: solid #0070F3;
        background: #111820;
    }

    Screen.light #workspace {
        background: #ffffff;
        border-top: solid #a8b3bd;
        border-bottom: solid #a8b3bd;
    }

    #sidebar {
        width: 30;
        min-width: 22;
        height: 1fr;
        border: none;
        background: #0f151c;
    }

    #sidebar-up {
        height: 1;
        margin: 0 1;
        min-width: 8;
    }

    #sidebar-root {
        height: auto;
        padding: 0 1;
        color: #8a96a3;
    }

    #file-tree {
        height: 1fr;
        background: #0f151c;
    }

    Screen.light #sidebar {
        background: #eef2f6;
        border: none;
    }

    Screen.light #file-tree {
        background: #eef2f6;
    }

    #main {
        width: 1fr;
        height: 1fr;
        border: none;
        background: #111820;
    }

    #palette {
        height: auto;
        max-height: 10;
        padding: 1 2;
        border: solid #c6a15b;
        background: #17140d;
        color: #f6e9c5;
    }

    #palette.hidden {
        display: none;
    }

    #suggestions {
        height: auto;
        max-height: 8;
        padding: 0 2;
        border: solid #0070F3;
        background: #0b1726;
        color: #dbeafe;
    }

    #suggestions.hidden {
        display: none;
    }

    #chat {
        height: 1fr;
        padding: 1 2;
        border: none;
        background: #111820;
    }

    Screen.light #main {
        background: #ffffff;
        border: none;
    }

    Screen.light #chat {
        background: #ffffff;
        border: none;
    }

    #permission-panel {
        height: auto;
        padding: 1 2;
        border-top: solid #0070F3;
        background: #0b1726;
        color: #dbeafe;
    }

    #permission-panel.hidden {
        display: none;
    }

    #permission-title {
        height: auto;
        color: #ffffff;
        text-style: bold;
    }

    #permission-warning {
        height: auto;
        color: #ffcc66;
        text-style: bold;
    }

    #permission-actions {
        height: 3;
        padding-top: 1;
    }

    #permission-actions Button {
        margin-right: 1;
        min-width: 14;
    }

    Screen.light #permission-panel {
        background: #edf5ff;
        color: #101418;
    }

    Screen.light #permission-title {
        color: #101418;
    }

    #workspace,
    #sidebar,
    #file-tree,
    #main,
    #palette,
    #suggestions,
    #permission-panel,
    #chat,
    #input {
        scrollbar-color: #0070F3;
        scrollbar-color-hover: #0070F3;
        scrollbar-color-active: #0070F3;
        scrollbar-background: #101418;
        scrollbar-background-hover: #101418;
        scrollbar-background-active: #101418;
        scrollbar-corner-color: #101418;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    Screen.light #workspace,
    Screen.light #sidebar,
    Screen.light #file-tree,
    Screen.light #main,
    Screen.light #palette,
    Screen.light #suggestions,
    Screen.light #permission-panel,
    Screen.light #chat,
    Screen.light #input {
        scrollbar-color: #0070F3;
        scrollbar-color-hover: #0070F3;
        scrollbar-color-active: #0070F3;
        scrollbar-background: #f7f7f2;
        scrollbar-background-hover: #f7f7f2;
        scrollbar-background-active: #f7f7f2;
        scrollbar-corner-color: #f7f7f2;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    #input {
        height: 3;
        border: none;
        border-top: solid #0070F3;
        background: #121a22;
    }

    Screen.light #input {
        background: #ffffff;
        border-top: solid #a8b3bd;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Exit", show=True),
        Binding("escape", "interrupt", "Interrupt", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Files", show=True),
        Binding("ctrl+p", "command_palette", "Palette", show=True),
        Binding("ctrl+shift+c", "copy_last_response", "Copy Last", show=True),
        Binding("tab", "accept_suggestion", "Complete", show=False),
    ]

    def __init__(self, config: LibreClawConfig | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        self.session = Session()
        self.memory_store = MemoryStore()
        self.memory_facts: list[str] = []
        self.agent: Agent | None = None
        self.provider_error: str | None = None
        self.usage = Usage()
        self.transcript: list[TranscriptEntry] = []
        self.sidebar_visible = self.config.tui.show_file_tree
        self.palette_open = False
        self._slash_suggestions: list[SlashCommand] = []
        self._active_task: asyncio.Task[None] | None = None
        self._pending_permission: AgentPermissionRequest | None = None
        self._started_at = time.monotonic()
        self._last_assistant_response = ""

        self._rebuild_agent()

    def compose(self) -> ComposeResult:
        if self.config.tui.show_status_bar:
            yield Static(self._status_text(), id="status")

        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Button("Up", id="sidebar-up", variant="primary", compact=True)
                yield Static(self._sidebar_root_text(), id="sidebar-root")
                yield DirectoryTree(self.config.general.working_directory, id="file-tree")
            with Vertical(id="main"):
                yield Static("", id="palette", classes="hidden")
                yield RichLog(id="chat", wrap=True, highlight=True, markup=True)
                yield Static("", id="suggestions", classes="hidden")
                with Vertical(id="permission-panel", classes="hidden"):
                    yield Static("", id="permission-title")
                    yield Static("", id="permission-warning")
                    with Horizontal(id="permission-actions"):
                        yield Button("Approve", id="permission-allow-once", variant="success", compact=True)
                        yield Button("Deny", id="permission-deny", variant="error", compact=True)
                        yield Button("Always Tool", id="permission-always-tool", compact=True)
                        yield Button("Always Command", id="permission-always-call", compact=True)
                yield Input(placeholder=self._input_placeholder(), id="input")

    async def on_mount(self) -> None:
        self.add_class(self.config.general.theme)
        self.query_one("#input", Input).focus()
        self._sync_sidebar_visibility()
        self._update_palette()
        self._update_slash_suggestions("")
        self.set_interval(1, self._update_status)
        await self._initialize_memory()
        self._append_system(_startup_message())
        if self.provider_error is not None:
            self._append_system(self.provider_error)

    async def on_unmount(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self._should_complete_on_submit(text):
            self._accept_first_suggestion(event.input)
            return

        event.input.value = ""
        self._update_slash_suggestions("")
        if not text and not self.palette_open:
            return

        await self.handle_user_input(text)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if self.palette_open:
            self._update_palette(event.value)
            return
        self._update_slash_suggestions(event.value)

    def on_key(self, event: events.Key) -> None:
        if self._pending_permission is None:
            return

        resolution = PERMISSION_KEYS.get(event.key) or PERMISSION_KEYS.get(event.character or "")
        if resolution is None:
            return

        event.stop()
        self._resolve_pending_permission(resolution)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "sidebar-up":
            event.stop()
            self._go_up_directory()
            return

        mapping: dict[str | None, PermissionResolution] = {
            "permission-allow-once": "allow_once",
            "permission-deny": "deny",
            "permission-always-tool": "always_allow_tool",
            "permission-always-call": "always_allow_call",
        }
        resolution = mapping.get(button_id)
        if resolution is None:
            return

        event.stop()
        self._resolve_pending_permission(resolution)

    async def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        path = Path(event.path)
        content = await asyncio.to_thread(path.read_text, "utf-8", "replace")
        preview = "\n".join(content.splitlines()[:120])
        self._append_entry(
            "file",
            preview,
            title=str(path),
            metadata={"path": str(path), "truncated": len(content.splitlines()) > 120},
        )

    async def handle_user_input(self, text: str) -> None:
        """Handle a message, slash command, permission response, or palette query."""
        if self.palette_open:
            await self._handle_palette_input(text)
            return

        if self._pending_permission is not None and not text.startswith("/"):
            self._handle_permission_input(text)
            return

        if text.startswith("/"):
            await self._handle_command(text)
            return

        if self._active_task is not None and not self._active_task.done():
            self._append_system("A response is already streaming. Use /cancel to stop it.")
            return

        self._append_user(text)
        if self.agent is None:
            self._append_system(self.provider_error or "No provider is available.")
            return

        assistant_index = self._append_assistant("")
        self._active_task = asyncio.create_task(self._stream_agent_response(text, assistant_index))

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/exit":
            self._cancel_active_generation()
            self.exit()
            return
        if command == "/clear":
            self._clear_transcript()
            return
        if command == "/cancel":
            self._cancel_active_generation()
            return
        if command == "/help":
            self._append_system(self._help_text())
            return
        if command == "/cost":
            self._append_system(self._cost_text())
            return
        if command == "/model":
            self._set_model(argument)
            return
        if command == "/provider":
            self._set_provider(argument)
            return
        if command == "/save":
            self._save_session(argument)
            return
        if command == "/load":
            self._load_session(argument)
            return
        if command == "/compact":
            self._compact_context()
            return
        if command == "/memory":
            await self._handle_memory_command(argument)
            return
        if command == "/telegram":
            self._append_system(self._telegram_status())
            return
        if command == "/tools":
            self._handle_tools_command(argument)
            return

        self._append_system(f"Unknown command: {command}")

    async def _handle_palette_input(self, query: str) -> None:
        matches = self._palette_matches(query)
        if not query:
            self._close_palette()
            return
        if not matches:
            self._append_system(f"No command palette match for: {query}")
            self._close_palette()
            return

        slash = matches[0].usage.split()[0]
        self._close_palette()
        await self._handle_command(slash)

    def action_interrupt(self) -> None:
        if self.palette_open:
            self._close_palette()
            return
        self._cancel_active_generation()

    def action_quit_app(self) -> None:
        self._cancel_active_generation(quiet=True)
        self.exit()

    def action_toggle_sidebar(self) -> None:
        self.sidebar_visible = not self.sidebar_visible
        self._sync_sidebar_visibility()
        state = "shown" if self.sidebar_visible else "hidden"
        self._append_system(f"File tree {state}.")

    def action_command_palette(self) -> None:
        self.palette_open = not self.palette_open
        self._update_palette(self.query_one("#input", Input).value)
        self._update_slash_suggestions("")
        input_widget = self.query_one("#input", Input)
        input_widget.placeholder = self._input_placeholder()
        input_widget.focus()

    def action_accept_suggestion(self) -> None:
        if self.palette_open:
            return
        self._accept_first_suggestion(self.query_one("#input", Input))

    def action_copy_last_response(self) -> None:
        if not self._last_assistant_response:
            self._append_system("No assistant response to copy.")
            return
        self.copy_to_clipboard(self._last_assistant_response)
        self._append_system("Copied last assistant response to clipboard.")

    async def _stream_agent_response(self, user_message: str, assistant_index: int) -> None:
        if self.agent is None:
            return

        try:
            async for event in self.agent.run(user_message):
                if isinstance(event, AgentTextDelta):
                    self._append_to_entry(assistant_index, event.text)
                    self._last_assistant_response = self.transcript[assistant_index].content
                    continue

                if isinstance(event, AgentToolCall):
                    self._append_tool_call(event.call)
                    continue

                if isinstance(event, AgentPermissionRequest):
                    self._pending_permission = event
                    self._show_permission_prompt(event)
                    continue

                if isinstance(event, AgentToolResult):
                    self._append_tool_result(event.call, event.result)
                    continue

                if isinstance(event, AgentDone):
                    if event.usage is not None:
                        self.usage = event.usage
                        self._update_status()
                    continue

                if isinstance(event, AgentError):
                    if not self.transcript[assistant_index].content:
                        self.transcript.pop(assistant_index)
                        self._render_transcript()
                    self._append_system(event.message)
                    break
        except asyncio.CancelledError:
            self._append_system("Generation cancelled.")
        finally:
            self._pending_permission = None
            self._hide_permission_prompt()
            self._active_task = None
            self.query_one("#input", Input).focus()

    def _cancel_active_generation(self, quiet: bool = False) -> None:
        if self._pending_permission is not None and not self._pending_permission.future.done():
            self._pending_permission.future.set_result("deny")
            self._pending_permission = None
            self._hide_permission_prompt()
        if self._active_task is None or self._active_task.done():
            if not quiet:
                self._append_system("No active generation to cancel.")
            return
        self._active_task.cancel()

    def _handle_permission_input(self, text: str) -> None:
        normalized = text.strip().lower()
        mapping: dict[str, PermissionResolution] = {
            "y": "allow_once",
            "yes": "allow_once",
            "n": "deny",
            "no": "deny",
            "a": "always_allow_tool",
            "!": "always_allow_call",
        }
        resolution = mapping.get(normalized)
        if resolution is None:
            self._append_permission("Please answer y, n, a, or !")
            return

        self._resolve_pending_permission(resolution)

    def _show_permission_prompt(self, request: AgentPermissionRequest) -> None:
        panel = self.query_one("#permission-panel", Vertical)
        title = self.query_one("#permission-title", Static)
        warning = self.query_one("#permission-warning", Static)
        allow_once = self.query_one("#permission-allow-once", Button)
        always_tool = self.query_one("#permission-always-tool", Button)
        always_call = self.query_one("#permission-always-call", Button)

        danger = self._dangerous_permission_warning(request.call)
        title.update(
            f"{request.call.name} wants permission\n"
            f"{self._format_arguments(request.call.arguments)}"
        )
        warning.update(
            f"Warning: {danger}\nDangerous commands require one-time approval."
            if danger is not None
            else "Choose once, always for this tool, or always for this exact command."
        )
        always_tool.disabled = danger is not None
        always_call.disabled = danger is not None
        panel.remove_class("hidden")
        self.query_one("#input", Input).placeholder = self._input_placeholder()
        allow_once.focus()

    def _hide_permission_prompt(self) -> None:
        panel = self.query_one("#permission-panel", Vertical)
        panel.add_class("hidden")
        self.query_one("#permission-title", Static).update("")
        self.query_one("#permission-warning", Static).update("")
        for button_id in ("#permission-always-tool", "#permission-always-call"):
            self.query_one(button_id, Button).disabled = False
        self.query_one("#input", Input).placeholder = self._input_placeholder()

    def _resolve_pending_permission(self, resolution: PermissionResolution) -> None:
        request = self._pending_permission
        if request is None:
            return

        danger = self._dangerous_permission_warning(request.call)
        if danger is not None and resolution in {"always_allow_tool", "always_allow_call"}:
            self._append_permission("Dangerous commands can only be approved once or denied.")
            return

        if not request.future.done():
            request.future.set_result(resolution)
        self._pending_permission = None
        self._hide_permission_prompt()
        self._append_system(f"Permission response recorded for {request.call.name}: {_permission_label(resolution)}")

    def _dangerous_permission_warning(self, call: ToolCall) -> str | None:
        if call.name != "bash":
            return None

        command = call.arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None

        policy = SandboxPolicy(
            working_directory=self.config.general.working_directory,
            restrict_to_working_dir=self.config.sandbox.restrict_to_working_dir,
            command_timeout=self.config.sandbox.command_timeout,
            allow_sudo=self.config.sandbox.allow_sudo,
            blocked_patterns=self.config.sandbox.blocked_patterns,
        )
        try:
            policy.validate_command(command)
        except SandboxViolation as exc:
            return str(exc)
        return None

    def _clear_transcript(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._append_system("Cancel the active response before clearing the transcript.")
            return
        self.transcript.clear()
        self.session.clear()
        self._last_assistant_response = ""
        self._render_transcript()
        self._append_system("Transcript cleared.")

    def _set_model(self, model: str) -> None:
        if not model:
            self._append_system(_model_help_text(self.config))
            return

        parsed = _parse_model_argument(model, self.config.general.default_provider)
        if parsed is None:
            self._append_system(_model_help_text(self.config))
            return

        provider, selected_model = parsed
        self.config = _replace_general(self.config, default_provider=provider, default_model=selected_model)
        self._rebuild_agent()
        self._update_status()
        if self.provider_error:
            self._append_system(
                f"Model set to {provider}:{selected_model}, but provider setup is incomplete.\n"
                f"{self.provider_error}"
            )
        else:
            self._append_system(f"Model set to {provider}:{selected_model}.")

    def _set_provider(self, provider: str) -> None:
        if not provider:
            self._append_system(_provider_help_text(self.config))
            return
        provider = provider.strip().lower()
        if provider == "local":
            provider = "ollama"
        if provider not in SUPPORTED_PROVIDERS:
            self._append_system(_provider_help_text(self.config))
            return
        self.config = _replace_general(self.config, default_provider=provider)
        self._rebuild_agent()
        self._update_status()
        if self.provider_error:
            self._append_system(self.provider_error)
        else:
            self._append_system(f"Provider set to {provider}.")

    def _save_session(self, name: str) -> None:
        session_name = name or datetime.now().strftime("%Y%m%d-%H%M%S")
        asyncio.create_task(self._save_session_async(session_name))

    def _load_session(self, name: str) -> None:
        if not name:
            self._append_system("Usage: /load <name>")
            return
        asyncio.create_task(self._load_session_async(name))

    async def _save_session_async(self, session_name: str) -> None:
        stored = await self.memory_store.save_session(session_name, self.session)
        self._append_system(f"Session saved as {stored.name}.")

    async def _load_session_async(self, name: str) -> None:
        stored = await self.memory_store.load_session(name)
        if stored is None:
            self._append_system(f"No saved session named {name}.")
            return
        self.session.messages = stored.messages
        self.session.summary = stored.summary or None
        self.transcript = self._transcript_from_messages(stored.messages)
        self._render_transcript()
        self._update_status()
        self._rebuild_agent()
        self._append_system(f"Session loaded from {name}.")

    async def _handle_memory_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        value = parts[1].strip() if len(parts) > 1 else ""

        if action == "list":
            facts = await self.memory_store.list_facts()
            if not facts:
                self._append_system("No memory facts stored.")
                return
            self._append_system("\n".join(f"{fact.id}: {fact.fact}" for fact in facts))
            return

        if action == "add":
            if not value:
                self._append_system("Usage: /memory add <fact>")
                return
            fact = await self.memory_store.add_fact(value)
            await self._refresh_memory_facts()
            self._append_system(f"Added memory fact {fact.id}.")
            return

        if action == "forget":
            if not value.isdigit():
                self._append_system("Usage: /memory forget <id>")
                return
            removed = await self.memory_store.forget_fact(int(value))
            await self._refresh_memory_facts()
            self._append_system("Memory fact forgotten." if removed else f"No memory fact with id {value}.")
            return

        self._append_system("Usage: /memory list|add <fact>|forget <id>")

    def _compact_context(self) -> None:
        before = len(self.session.messages)
        summary = self.session.compact(keep_last=8)
        after = len(self.session.messages)
        if summary is None or before == after:
            self._append_system("Context is already compact enough.")
            return
        self._rebuild_agent()
        self._append_system(f"Compacted context from {before} messages to {after}.")

    def _handle_tools_command(self, argument: str) -> None:
        parts = argument.split()
        if not parts:
            self._append_system("Usage: /tools expand|collapse|toggle <index>")
            return

        action = parts[0].lower()
        index = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        tool_entries = [entry for entry in self.transcript if entry.role == "tool"]

        if action in {"expand", "collapse"} and index is None:
            collapsed = action == "collapse"
            for entry in tool_entries:
                entry.collapsed = collapsed
            self._render_transcript()
            self._append_system(f"Tool entries {action}ed.")
            return

        if action == "toggle" and index is not None and 0 <= index < len(tool_entries):
            tool_entries[index].collapsed = not tool_entries[index].collapsed
            self._render_transcript()
            return

        self._append_system("Usage: /tools expand|collapse|toggle <index>")

    def _go_up_directory(self) -> None:
        current = self.config.general.working_directory.resolve()
        parent = current.parent
        if parent == current:
            self._append_system("Already at the filesystem root.")
            return

        self.config = _replace_general(self.config, working_directory=parent)
        self.query_one("#file-tree", DirectoryTree).path = parent
        self.query_one("#sidebar-root", Static).update(self._sidebar_root_text())
        self._rebuild_agent()
        self._update_status()
        self._append_system(f"Explorer root and agent working directory set to {parent}.")

    def _append_user(self, text: str) -> int:
        return self._append_entry("user", text)

    def _append_assistant(self, text: str) -> int:
        return self._append_entry("assistant", text)

    def _append_system(self, text: str) -> int:
        return self._append_entry("system", text)

    def _append_permission(self, text: str) -> int:
        return self._append_entry("permission", text)

    def _append_tool_call(self, call: ToolCall) -> int:
        return self._append_entry(
            "tool",
            self._format_arguments(call.arguments),
            title=f"Calling {call.name}",
            collapsed=True,
            metadata={"tool": call.name, "status": "call"},
        )

    def _append_tool_result(self, call: ToolCall, result: ToolResult) -> int:
        metadata = dict(result.metadata)
        metadata.update({"tool": call.name, "status": "error" if result.is_error else "result"})
        content = result.as_text()
        if call.name == "edit_file" and "before" in result.metadata and "after" in result.metadata:
            content = self._diff_text(
                str(result.metadata.get("before", "")),
                str(result.metadata.get("after", "")),
                str(result.metadata.get("path", call.arguments.get("path", "file"))),
            )
            metadata["syntax"] = "diff"
        return self._append_entry(
            "tool",
            content,
            title=f"{call.name} {'error' if result.is_error else 'result'}",
            collapsed=False,
            metadata=metadata,
        )

    def _append_entry(
        self,
        role: TranscriptRole,
        content: str,
        title: str | None = None,
        collapsed: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        self.transcript.append(
            TranscriptEntry(role=role, content=content, title=title, collapsed=collapsed, metadata=metadata)
        )
        self._render_transcript()
        return len(self.transcript) - 1

    def _append_to_entry(self, index: int, text: str) -> None:
        self.transcript[index].content += text
        self._render_transcript()

    def _render_transcript(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.clear()
        for index, entry in enumerate(self.transcript):
            chat.write(self._format_entry(entry, index), scroll_end=True)

    def _format_entry(self, entry: TranscriptEntry, index: int = 0) -> RenderableType:
        if entry.role == "user":
            return Text.assemble(("User: ", "bold #0070F3"), entry.content)
        if entry.role == "assistant":
            if not entry.content:
                return Text("Libre Claw: streaming...", style=f"bold {ASSISTANT_ACCENT} dim")
            return Group(Text("Libre Claw:", style=f"bold {ASSISTANT_ACCENT}"), Markdown(entry.content))
        if entry.role == "tool":
            title = entry.title or "Tool"
            if entry.collapsed:
                return Text(f"Tool {self._tool_display_index(index)}: {title} - collapsed", style="bold magenta")
            metadata = entry.metadata or {}
            if metadata.get("syntax") == "diff":
                return Group(Text(f"Tool {self._tool_display_index(index)}: {title}", style="bold magenta"), Syntax(entry.content, "diff"))
            return Text.assemble((f"Tool {self._tool_display_index(index)}: {title}\n", "bold magenta"), entry.content)
        if entry.role == "permission":
            return Text.assemble(("Permission: ", "bold yellow"), entry.content)
        if entry.role == "file":
            title = entry.title or "File"
            return Group(Text(f"File: {title}", style="bold blue"), Syntax(entry.content, "text"))
        return Text("System: " + entry.content, style="dim")

    def _tool_display_index(self, transcript_index: int) -> int:
        return sum(1 for entry in self.transcript[: transcript_index + 1] if entry.role == "tool") - 1

    def _format_arguments(self, arguments: object) -> str:
        try:
            return json.dumps(arguments, sort_keys=True)
        except TypeError:
            return str(arguments)

    def _diff_text(self, before: str, after: str, path: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"{path} before",
                tofile=f"{path} after",
                lineterm="",
            )
        )

    def _rebuild_agent(self) -> None:
        self.agent = None
        self.provider_error = None
        try:
            provider = create_provider(self.config)
        except ProviderConfigurationError as exc:
            self.provider_error = str(exc)
            return

        self.agent = Agent(
            session=self.session,
            provider=provider,
            tool_registry=create_builtin_registry(self.config, memory_store=self.memory_store),
            permission_manager=PermissionManager(self.config.permissions),
            system_prompt=self.config.agent.system_prompt,
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            memory_facts=self.memory_facts,
            system_prompt_extra=self.config.agent.system_prompt_extra,
        )

    async def _initialize_memory(self) -> None:
        await self.memory_store.initialize()
        await self._refresh_memory_facts()
        self._rebuild_agent()

    async def _refresh_memory_facts(self) -> None:
        facts = await self.memory_store.list_facts()
        self.memory_facts = [fact.fact for fact in facts]
        self._rebuild_agent()

    def _transcript_from_messages(self, messages: list[ChatMessage]) -> list[TranscriptEntry]:
        entries: list[TranscriptEntry] = []
        for message in messages:
            text_parts: list[str] = []
            tool_parts: list[TranscriptEntry] = []
            for block in message.content:
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block_type == "tool_use":
                    tool_parts.append(
                        TranscriptEntry(
                            role="tool",
                            content=self._format_arguments(block.get("input", {})),
                            title=f"Calling {block.get('name', 'tool')}",
                            collapsed=True,
                        )
                    )
                elif block_type == "tool_result":
                    tool_parts.append(
                        TranscriptEntry(
                            role="tool",
                            content=str(block.get("content", "")),
                            title=f"tool result {block.get('tool_use_id', '')}",
                            collapsed=False,
                        )
                    )

            if text_parts:
                entries.append(TranscriptEntry(role=message.role, content="\n".join(text_parts)))
            entries.extend(tool_parts)
        return entries

    def _sync_sidebar_visibility(self) -> None:
        self.query_one("#sidebar", Vertical).display = self.sidebar_visible

    def _sidebar_root_text(self) -> str:
        return f"cwd: {self.config.general.working_directory}"

    def _update_palette(self, query: str = "") -> None:
        palette = self.query_one("#palette", Static)
        if not self.palette_open:
            palette.add_class("hidden")
            palette.update("")
            return
        palette.remove_class("hidden")
        palette.update(self._palette_text(query))

    def _close_palette(self) -> None:
        self.palette_open = False
        self._update_palette()
        self.query_one("#input", Input).placeholder = self._input_placeholder()

    def _palette_matches(self, query: str) -> list[SlashCommand]:
        normalized = query.lower().strip()
        if not normalized:
            return list(SLASH_COMMANDS)
        return [
            command
            for command in SLASH_COMMANDS
            if normalized in command.name.lower() or normalized in command.description.lower()
        ]

    def _palette_text(self, query: str) -> str:
        lines = ["Command palette - type a command name and press Enter"]
        lines.extend(f"{command.usage:<26} {command.description}" for command in self._palette_matches(query))
        return "\n".join(lines)

    def _help_text(self) -> str:
        command_lines = "\n".join(f"{command.usage} - {command.description}" for command in SLASH_COMMANDS)
        return f"{command_lines}\nCtrl+C exits. Esc or /cancel interrupts. Permission prompts support buttons plus y, n, a, ! shortcuts."

    def _update_slash_suggestions(self, text: str) -> None:
        self._slash_suggestions = self._slash_suggestion_matches(text)
        suggestions = self.query_one("#suggestions", Static)
        if not self._slash_suggestions:
            suggestions.add_class("hidden")
            suggestions.update("")
            return
        suggestions.remove_class("hidden")
        suggestions.update(self._slash_suggestion_text(self._slash_suggestions))

    def _slash_suggestion_matches(self, text: str) -> list[SlashCommand]:
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return []
        argument_suggestions = self._slash_argument_suggestions(stripped)
        if argument_suggestions:
            return argument_suggestions
        if " " in stripped:
            return []
        query = stripped.lower()
        matches = [command for command in SLASH_COMMANDS if command.name.startswith(query)]
        if matches:
            return matches[:6]
        return [command for command in SLASH_COMMANDS if query in command.name.lower()][:6]

    def _slash_argument_suggestions(self, text: str) -> list[SlashCommand]:
        lowered = text.lower()
        if lowered.startswith("/provider "):
            query = lowered.removeprefix("/provider ").strip()
            return [
                SlashCommand(f"/provider {provider}", f"/provider {provider}", f"Switch to {provider}")
                for provider in SUPPORTED_PROVIDERS
                if not query or provider.startswith(query)
            ][:6]

        if lowered.startswith("/model "):
            query = lowered.removeprefix("/model ").strip()
            suggestions = _model_suggestion_commands(self.config)
            if not query:
                return suggestions[:6]
            return [
                suggestion
                for suggestion in suggestions
                if query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        return []

    def _slash_suggestion_text(self, suggestions: list[SlashCommand]) -> str:
        return "\n".join(f"{command.usage:<30} {command.description}" for command in suggestions)

    def _should_complete_on_submit(self, text: str) -> bool:
        if not self._slash_suggestions:
            return False
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return False
        if " " in stripped:
            return stripped.lower() not in {command.name.lower() for command in self._slash_suggestions}
        return all(stripped.lower() != command.name for command in SLASH_COMMANDS)

    def _accept_first_suggestion(self, input_widget: Input) -> None:
        if not self._slash_suggestions:
            return
        command = self._slash_suggestions[0]
        input_widget.value = self._completion_text(command)
        input_widget.cursor_position = len(input_widget.value)
        self._update_slash_suggestions(input_widget.value)

    def _completion_text(self, command: SlashCommand) -> str:
        if " " in command.name:
            return command.name
        return command.name + (" " if _usage_requires_argument(command.usage) else "")

    def _cost_text(self) -> str:
        return f"Tokens: {self.usage.total_tokens} total ({self.usage.input_tokens} input, {self.usage.output_tokens} output). Cost: $0.00."

    def _telegram_status(self) -> str:
        enabled = "enabled" if self.config.telegram.enabled else "disabled"
        return (
            f"Telegram bridge is {enabled}. Run `libre-claw telegram` for the standalone daemon. "
            f"Token env: {self.config.telegram.bot_token_env}."
        )

    def _status_text(self) -> str:
        provider = self.config.general.default_provider
        model = _effective_model(self.config)
        elapsed = int(time.monotonic() - self._started_at)
        active = "running" if self._active_task is not None and not self._active_task.done() else "idle"
        return (
            f"Libre Claw v{__version__} | {provider}:{model} | $0.00 | "
            f"{self.usage.total_tokens} tokens | {elapsed}s | {active}"
        )

    def _update_status(self) -> None:
        if self.config.tui.show_status_bar:
            self.query_one("#status", Static).update(self._status_text())

    def _input_placeholder(self) -> str:
        if self.palette_open:
            return "Command palette query..."
        if self._pending_permission is not None:
            return "Permission prompt active: click a choice or press y/n/a/!"
        return "Type a message... (/help, Ctrl+B files, Ctrl+P palette, Ctrl+C exit)"


def _replace_general(config: LibreClawConfig, **changes: Any) -> LibreClawConfig:
    general_values = {
        "default_provider": config.general.default_provider,
        "default_model": config.general.default_model,
        "working_directory": config.general.working_directory,
        "theme": config.general.theme,
        "log_level": config.general.log_level,
    }
    general_values.update(changes)
    if str(general_values["default_provider"]).lower() == "local":
        general_values["default_provider"] = "ollama"
    general_values["working_directory"] = Path(general_values["working_directory"]).expanduser().resolve()
    general = GeneralConfig(**general_values)
    return LibreClawConfig(
        general=general,
        agent=config.agent,
        permissions=config.permissions,
        sandbox=config.sandbox,
        auth=config.auth,
        tui=config.tui,
        telegram=config.telegram,
        providers=config.providers,
        source_paths=config.source_paths,
    )


def _effective_model(config: LibreClawConfig) -> str:
    provider_name = "ollama" if config.general.default_provider.lower() == "local" else config.general.default_provider.lower()
    provider_config = config.providers.get(provider_name, {})
    provider_default = str(provider_config.get("default_model", config.general.default_model))
    other_defaults = {
        str(other_config.get("default_model"))
        for name, other_config in config.providers.items()
        if name != provider_name and hasattr(other_config, "get") and other_config.get("default_model")
    }
    if config.general.default_model in other_defaults:
        return provider_default
    return config.general.default_model or provider_default


def _parse_model_argument(argument: str, current_provider: str) -> tuple[str, str] | None:
    cleaned = argument.strip()
    if not cleaned or cleaned.lower() == "list":
        return None

    provider = "ollama" if current_provider.lower() == "local" else current_provider.lower()
    model = cleaned
    parts = cleaned.split(maxsplit=1)
    if len(parts) == 2 and _canonical_tui_provider(parts[0]) in SUPPORTED_PROVIDERS:
        provider = _canonical_tui_provider(parts[0])
        model = parts[1].strip()
    else:
        prefix, separator, rest = cleaned.partition(":")
        canonical_prefix = _canonical_tui_provider(prefix)
        if separator and canonical_prefix in SUPPORTED_PROVIDERS and rest.strip():
            provider = canonical_prefix
            model = rest.strip()

    if not model:
        return None
    return provider, model


def _canonical_tui_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "local":
        return "ollama"
    return normalized


def _model_help_text(config: LibreClawConfig) -> str:
    provider = _canonical_tui_provider(config.general.default_provider)
    current_model = _effective_model(config)
    lines = [
        f"Current model: {provider}:{current_model}",
        "Use `/model <name>` for the current provider or `/model <provider>:<name>` to switch both.",
        "Use `Tab` after `/model ` to complete a suggested model.",
        "Provider key setup stays in the secure CLI/keyring path:",
    ]
    lines.extend(f"- libre-claw auth set-key {name}" for name in SUPPORTED_PROVIDERS if name != "ollama")
    lines.append("- libre-claw auth set-key ollama  # required for Ollama Cloud")
    lines.append("")
    lines.append("Suggested models:")
    for suggestion in _model_suggestion_commands(config):
        lines.append(f"- {suggestion.name} - {suggestion.description}")
    return "\n".join(lines)


def _provider_help_text(config: LibreClawConfig) -> str:
    provider = _canonical_tui_provider(config.general.default_provider)
    lines = [
        f"Current provider: {provider}",
        "Use `/provider anthropic|openai|openrouter|ollama`, or use `/model <provider>:<name>` to switch both.",
    ]
    return "\n".join(lines)


def _model_suggestion_commands(config: LibreClawConfig) -> list[SlashCommand]:
    current_provider = _canonical_tui_provider(config.general.default_provider)
    ordered_providers = [current_provider, *(provider for provider in SUPPORTED_PROVIDERS if provider != current_provider)]
    suggestions: list[SlashCommand] = []
    for provider in ordered_providers:
        for model, label in MODEL_PRESETS.get(provider, ()):
            suggestions.append(
                SlashCommand(
                    name=f"/model {provider}:{model}",
                    usage=f"/model {provider}:{model}",
                    description=label,
                )
            )
    return suggestions


def _usage_requires_argument(usage: str) -> bool:
    return " " in usage


def _startup_message() -> str:
    return f"{STARTUP_ASCII.strip()}\n\n{latest_release_notes()}\n\nType /help for commands."


def _permission_label(resolution: PermissionResolution) -> str:
    labels = {
        "allow_once": "approved once",
        "deny": "denied",
        "always_allow_tool": "always allowed for this tool",
        "always_allow_call": "always allowed for this exact command",
    }
    return labels[resolution]
