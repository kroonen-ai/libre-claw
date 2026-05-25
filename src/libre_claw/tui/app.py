# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import difflib
import json
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

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
from libre_claw.auth.codex import CodexCliError, CodexCommandResult, codex_logout, codex_status, stream_codex_command
from libre_claw.config import (
    ConfigError,
    GeneralConfig,
    LibreClawConfig,
    global_config_path,
    load_config,
    set_global_default_model,
)
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    GoalComplete,
    GoalJudgeResult,
    GoalRunner,
    GoalStopped,
    GoalTurnStarted,
    JudgeDecision,
    RunEvent,
    RunRecord,
    RunState,
    RunStore,
    Session,
)
from libre_claw.core.memory import MemoryStore
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.sandbox import SandboxPolicy, SandboxViolation
from libre_claw.core.session import ChatMessage, estimate_context_tokens
from libre_claw.core.tools import ToolCall, ToolResult
from libre_claw.providers import LLMProvider, ProviderConfigurationError, Usage, combine_usage, create_provider
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


@dataclass(frozen=True)
class ContextMeter:
    estimated_tokens: int
    context_window_tokens: int
    ratio: float

    @property
    def percent(self) -> int:
        return min(999, int(round(self.ratio * 100)))

    @property
    def display_percent(self) -> str:
        if self.estimated_tokens > 0 and self.ratio < 0.01:
            return "<1%"
        return f"{self.percent}%"


@dataclass(frozen=True)
class CompactOptions:
    keep_last: int = 8
    force: bool = False
    status: bool = False
    error: str | None = None


@dataclass
class StreamRenderBuffer:
    interval: float
    max_buffered_chars: int
    chunks: list[str] = field(default_factory=list)
    last_flush_at: float = 0.0
    rendered_once: bool = False

    @property
    def pending_text(self) -> str:
        return "".join(self.chunks)

    def append(self, text: str) -> None:
        if text:
            self.chunks.append(text)

    def should_flush(self, now: float) -> bool:
        if not self.chunks:
            return False
        if not self.rendered_once:
            return True
        return now - self.last_flush_at >= self.interval or len(self.pending_text) >= self.max_buffered_chars

    def flush(self, now: float) -> str:
        text = self.pending_text
        self.chunks.clear()
        if text:
            self.last_flush_at = now
            self.rendered_once = True
        return text


@dataclass(frozen=True)
class ProcessCapture:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "/help", "Show available commands"),
    SlashCommand("/clear", "/clear", "Clear transcript and session history"),
    SlashCommand("/cancel", "/cancel", "Cancel active generation or tool execution"),
    SlashCommand("/cost", "/cost", "Show token and cost summary"),
    SlashCommand("/model", "/model [provider:]<name>|list [--global]", "Choose or persist models"),
    SlashCommand("/provider", "/provider anthropic|openai|openrouter|ollama|codex", "Switch provider for new turns"),
    SlashCommand("/codex", "/codex login|status|logout|use [model]", "Manage Codex/ChatGPT login"),
    SlashCommand("/save", "/save [name]", "Save the current session"),
    SlashCommand("/load", "/load <name>", "Load a saved session"),
    SlashCommand("/compact", "/compact [status|--force] [--keep N]", "Show or compact context"),
    SlashCommand("/goal", "/goal <objective>|status|stop|max N", "Run a judged multi-turn goal loop"),
    SlashCommand("/runs", "/runs [N]", "List durable agent runs"),
    SlashCommand("/run", "/run <id>", "Inspect a durable run"),
    SlashCommand("/resume", "/resume <id>", "Load a durable run transcript"),
    SlashCommand("/memory", "/memory list|add <fact>|forget <id>", "Manage persistent memory facts"),
    SlashCommand("/telegram", "/telegram", "Show Telegram bridge status"),
    SlashCommand("/tools", "/tools expand|collapse|toggle <index>", "Control tool call details"),
    SlashCommand("/exit", "/exit", "Exit Libre Claw"),
)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "openrouter", "ollama", "codex")
MODEL_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-20250918", "Claude Opus 4"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1", "GPT-4.1"),
        ("o3", "o3 reasoning"),
        ("o4-mini", "o4-mini reasoning"),
        ("codex-mini", "Codex Mini"),
    ),
    "codex": (
        ("gpt-5.5", "GPT-5.5 through Codex CLI auth"),
        ("gpt-5", "GPT-5 through Codex CLI auth"),
        ("codex-mini", "Codex Mini through Codex CLI auth"),
    ),
    "openrouter": (
        ("qwen/qwen3.7-max", "Qwen3.7 Max through OpenRouter"),
        ("openrouter/auto", "OpenRouter automatic routing"),
        ("anthropic/claude-sonnet-4.5", "Claude through OpenRouter"),
        ("openai/gpt-5.5", "GPT-5.5 through OpenRouter"),
        ("openai/gpt-4o", "GPT-4o through OpenRouter"),
        ("moonshotai/kimi-k2", "Kimi K2 through OpenRouter"),
    ),
    "ollama": (
        ("kimi-k2.6:cloud", "Kimi K2.6 on Ollama Cloud"),
        ("qwen3.6:27b", "Qwen3.6 local daemon"),
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
PROJECT_NOTICE = "Apache-2.0 | Kroonen AI Inc. | hello@kroonen.ai"
STREAM_RENDER_INTERVAL = 0.05
STREAM_RENDER_MAX_BUFFERED_CHARS = 240
RUN_ARTIFACT_TIMEOUT = 10.0
RUN_DIFF_MAX_CHARS = 750_000
RUN_STATUS_MAX_CHARS = 50_000
RUN_ARTIFACT_STDERR_MAX_CHARS = 20_000
STARTUP_ASCII = r"""
 █████        ███  █████                             █████████  ████
░░███        ░░░  ░░███                             ███░░░░░███░░███
 ░███        ████  ░███████  ████████   ██████     ███     ░░░  ░███   ██████   █████ ███ █████
 ░███       ░░███  ░███░░███░░███░░███ ███░░███   ░███          ░███  ░░░░░███ ░░███ ░███░░███
 ░███        ░███  ░███ ░███ ░███ ░░░ ░███████    ░███          ░███   ███████  ░███ ░███ ░███
 ░███      █ ░███  ░███ ░███ ░███     ░███░░░     ░░███     ███ ░███  ███░░███  ░░███████████
 ███████████ █████ ████████  █████    ░░██████     ░░█████████  █████░░████████  ░░████░████
░░░░░░░░░░░ ░░░░░ ░░░░░░░░  ░░░░░      ░░░░░░       ░░░░░░░░░  ░░░░░  ░░░░░░░░    ░░░░ ░░░░
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

    #sidebar-rail {
        width: 8;
        height: 1fr;
        background: #0f151c;
        border: none;
        padding: 1 0;
    }

    #sidebar-actions {
        height: 1;
        padding: 0 1;
    }

    #sidebar-show,
    #sidebar-hide,
    #sidebar-up {
        height: 1;
        min-width: 6;
    }

    #sidebar-hide {
        margin-right: 1;
    }

    #sidebar-up {
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

    Screen.light #sidebar-rail {
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

    #startup-panel {
        height: auto;
        padding: 1 2;
        background: #111820;
        color: #dce3ea;
    }

    Screen.light #startup-panel {
        background: #ffffff;
        color: #18212a;
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
    #sidebar-rail,
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
    Screen.light #sidebar-rail,
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
        self.run_store = RunStore()
        self.memory_facts: list[str] = []
        self.agent: Agent | None = None
        self.provider_error: str | None = None
        self.usage = Usage()
        self.transcript: list[TranscriptEntry] = []
        self.sidebar_visible = False
        self.startup_expanded = False
        self.palette_open = False
        self._slash_suggestions: list[SlashCommand] = []
        self._active_task: asyncio.Task[None] | None = None
        self._pending_permission: AgentPermissionRequest | None = None
        self._tool_entry_by_call_id: dict[str, int] = {}
        self._chat_entry_spans: dict[int, tuple[int, int]] = {}
        self._started_at = time.monotonic()
        self._last_assistant_response = ""
        self._goal_description: str | None = None
        self._goal_turn = 0
        self._goal_max_turns = self.config.goal.max_turns
        self._last_goal_decision: JudgeDecision | None = None
        self._active_run_id: str | None = None
        self._active_run_summary = ""
        self._run_background_tasks: set[asyncio.Task[Any]] = set()

        self._rebuild_agent()

    def compose(self) -> ComposeResult:
        if self.config.tui.show_status_bar:
            yield Static(self._status_text(), id="status")

        with Horizontal(id="workspace"):
            with Vertical(id="sidebar-rail"):
                yield Button("Files", id="sidebar-show", variant="primary", compact=True)
            with Vertical(id="sidebar"):
                with Horizontal(id="sidebar-actions"):
                    yield Button("Hide", id="sidebar-hide", compact=True)
                    yield Button("Up", id="sidebar-up", variant="primary", compact=True)
                yield Static(self._sidebar_root_text(), id="sidebar-root")
                yield DirectoryTree(self.config.general.working_directory, id="file-tree")
            with Vertical(id="main"):
                yield Static("", id="palette", classes="hidden")
                yield Static("", id="startup-panel")
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
        self._update_startup_panel()
        self._update_palette()
        self._update_slash_suggestions("")
        self.set_interval(1, self._update_status)
        await self._initialize_memory()
        self._append_system(f"Libre Claw v{__version__} ready. Type /help for commands.")
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

    def on_click(self, event: events.Click) -> None:
        if getattr(event.widget, "id", None) != "startup-panel":
            return
        event.stop()
        self.startup_expanded = not self.startup_expanded
        self._update_startup_panel()

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
        if button_id in {"sidebar-show", "sidebar-hide"}:
            event.stop()
            self.action_toggle_sidebar()
            return
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
        run = await self._start_run("chat", text)
        await self._record_run_event("user_message", {"content": text})
        if self.agent is None:
            self._append_system(self.provider_error or "No provider is available.")
            await self._record_run_event("error", {"message": self.provider_error or "No provider is available."})
            await self._finish_active_run("failed", summary=self.provider_error or "No provider is available.")
            return

        assistant_index = self._append_assistant("")
        self._append_system(f"Run {run.run_id} started.")
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
            if argument:
                await self._cancel_run_command(argument)
            else:
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
        if command == "/codex":
            await self._handle_codex_command(argument)
            return
        if command == "/save":
            self._save_session(argument)
            return
        if command == "/load":
            self._load_session(argument)
            return
        if command == "/compact":
            self._compact_context(argument)
            return
        if command == "/goal":
            await self._handle_goal_command(argument)
            return
        if command == "/runs":
            await self._handle_runs_command(argument)
            return
        if command == "/run":
            await self._handle_run_command(argument)
            return
        if command == "/resume":
            await self._handle_resume_command(argument)
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

        stream_buffer = StreamRenderBuffer(
            interval=STREAM_RENDER_INTERVAL,
            max_buffered_chars=STREAM_RENDER_MAX_BUFFERED_CHARS,
        )
        run_state = "done"
        run_summary = ""

        try:
            async for event in self.agent.run(user_message):
                handled, should_stop = self._handle_agent_stream_event(
                    event,
                    assistant_index,
                    stream_buffer,
                    stop_on_error=True,
                )
                if handled and should_stop:
                    run_state = "failed"
                    break
        except asyncio.CancelledError:
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._append_system("Generation cancelled.")
            self._record_run_event_later("cancelled", {"reason": "Generation cancelled."})
            run_state = "cancelled"
        finally:
            self._flush_stream_buffer(assistant_index, stream_buffer)
            if assistant_index < len(self.transcript):
                run_summary = self.transcript[assistant_index].content
            self._pending_permission = None
            self._hide_permission_prompt()
            self._active_task = None
            await self._finish_active_run(run_state, summary=run_summary)
            self.query_one("#input", Input).focus()

    async def _stream_goal_response(self, goal: str, assistant_index: int, judge_provider: LLMProvider) -> None:
        if self.agent is None:
            return

        stream_buffer = StreamRenderBuffer(
            interval=STREAM_RENDER_INTERVAL,
            max_buffered_chars=STREAM_RENDER_MAX_BUFFERED_CHARS,
        )
        current_assistant_index = assistant_index
        runner = GoalRunner(
            agent=self.agent,
            judge_provider=judge_provider,
            session=self.session,
            goal=goal,
            max_turns=self._goal_max_turns,
            judge_temperature=self.config.goal.judge_temperature,
            judge_max_tokens=self.config.goal.judge_max_tokens,
        )
        run_state = "done"
        run_summary = ""

        try:
            async for event in runner.run():
                if isinstance(event, GoalTurnStarted):
                    self._flush_stream_buffer(current_assistant_index, stream_buffer)
                    self._goal_turn = event.turn
                    if event.turn > 1:
                        current_assistant_index = self._append_assistant("")
                    self._append_system(f"Goal turn {event.turn}/{event.max_turns} started.")
                    self._record_run_event_later(
                        "goal_turn_started",
                        {"turn": event.turn, "max_turns": event.max_turns, "prompt": event.prompt},
                    )
                    self._update_status()
                    continue

                handled, _ = self._handle_agent_stream_event(
                    event,
                    current_assistant_index,
                    stream_buffer,
                    stop_on_error=False,
                )
                if handled:
                    continue

                if isinstance(event, GoalJudgeResult):
                    self._flush_stream_buffer(current_assistant_index, stream_buffer)
                    self._last_goal_decision = event.decision
                    if event.usage is not None:
                        self.usage = combine_usage(self.usage, event.usage) or self.usage
                    self._append_system(_goal_judge_text(event.turn, event.decision))
                    self._record_run_event_later(
                        "goal_judge",
                        {
                            "turn": event.turn,
                            "done": event.decision.done,
                            "confidence": event.decision.confidence,
                            "reason": event.decision.reason,
                            "next_prompt": event.decision.next_prompt,
                        },
                    )
                    self._update_status()
                    continue

                if isinstance(event, GoalComplete):
                    self._flush_stream_buffer(current_assistant_index, stream_buffer)
                    self._append_system(
                        f"Goal complete after {event.turns} turn(s). Judge: {event.decision.reason}"
                    )
                    self._record_run_event_later(
                        "goal_complete",
                        {"turns": event.turns, "reason": event.decision.reason},
                    )
                    run_state = "done"
                    continue

                if isinstance(event, GoalStopped):
                    self._flush_stream_buffer(current_assistant_index, stream_buffer)
                    self._append_system(f"Goal stopped after {event.turns} turn(s): {event.reason}")
                    self._record_run_event_later(
                        "goal_stopped",
                        {"turns": event.turns, "reason": event.reason},
                    )
                    run_state = "failed"
                    continue
        except asyncio.CancelledError:
            self._flush_stream_buffer(current_assistant_index, stream_buffer)
            self._append_system("Goal cancelled.")
            self._record_run_event_later("cancelled", {"reason": "Goal cancelled."})
            run_state = "cancelled"
        finally:
            self._flush_stream_buffer(current_assistant_index, stream_buffer)
            if current_assistant_index < len(self.transcript):
                run_summary = self.transcript[current_assistant_index].content
            self._pending_permission = None
            self._hide_permission_prompt()
            self._active_task = None
            self._goal_description = None
            self._goal_turn = 0
            self._update_status()
            await self._finish_active_run(run_state, summary=run_summary)
            self.query_one("#input", Input).focus()

    def _handle_agent_stream_event(
        self,
        event: object,
        assistant_index: int,
        stream_buffer: StreamRenderBuffer,
        *,
        stop_on_error: bool,
    ) -> tuple[bool, bool]:
        if isinstance(event, AgentTextDelta):
            stream_buffer.append(event.text)
            if stream_buffer.should_flush(time.monotonic()):
                self._flush_stream_buffer(assistant_index, stream_buffer)
            return True, False

        if isinstance(event, AgentToolCall):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._append_tool_call(event.call)
            self._record_run_event_later(
                "tool_call",
                {"id": event.call.id, "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            return True, False

        if isinstance(event, AgentPermissionRequest):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._pending_permission = event
            self._show_permission_prompt(event)
            self._record_run_event_later(
                "permission_request",
                {"tool_call_id": event.call.id, "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            self._set_run_state_later("blocked")
            return True, False

        if isinstance(event, AgentToolResult):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._append_tool_result(event.call, event.result)
            self._record_run_event_later(
                "tool_result",
                {
                    "tool_call_id": event.call.id,
                    "name": event.call.name,
                    "arguments": dict(event.call.arguments),
                    "is_error": event.result.is_error,
                    "content": event.result.as_text(),
                    "metadata": dict(event.result.metadata),
                },
            )
            return True, False

        if isinstance(event, AgentDone):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            if event.usage is not None:
                self.usage = combine_usage(self.usage, event.usage) or self.usage
                self._record_run_event_later("usage", _usage_payload(event.usage))
                self._update_status()
            return True, False

        if isinstance(event, AgentError):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            if assistant_index < len(self.transcript) and not self.transcript[assistant_index].content:
                self.transcript.pop(assistant_index)
                self._render_transcript()
            self._append_system(event.message)
            self._record_run_event_later("error", {"message": event.message})
            return True, stop_on_error

        return False, False

    def _flush_stream_buffer(self, assistant_index: int, stream_buffer: StreamRenderBuffer) -> None:
        text = stream_buffer.flush(time.monotonic())
        if not text or assistant_index >= len(self.transcript):
            return
        self._append_to_entry(assistant_index, text)
        self._last_assistant_response = self.transcript[assistant_index].content
        self._active_run_summary = self.transcript[assistant_index].content
        self._record_run_event_later("assistant_delta", {"text": text})

    async def _start_run(self, kind: str, title: str) -> RunRecord:
        run = await self.run_store.create_run(
            title,
            kind=kind,
            provider=_canonical_tui_provider(self.config.general.default_provider),
            model=_effective_model(self.config),
            working_directory=self.config.general.working_directory,
        )
        self._active_run_id = run.run_id
        self._active_run_summary = ""
        await self.run_store.append_event(
            run.run_id,
            "run_started",
            {
                "kind": kind,
                "provider": run.provider,
                "model": run.model,
                "working_directory": run.working_directory,
            },
        )
        return run

    async def _record_run_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._active_run_id is None:
            return
        await self.run_store.append_event(self._active_run_id, event_type, data)

    def _record_run_event_later(self, event_type: str, data: dict[str, Any]) -> None:
        if self._active_run_id is None:
            return
        self._track_run_background_task(self.run_store.append_event(self._active_run_id, event_type, data))

    def _set_run_state_later(self, state: str) -> None:
        if self._active_run_id is None:
            return
        if state in {"queued", "running", "blocked", "done", "failed", "cancelled"}:
            self._track_run_background_task(
                self.run_store.update_state(self._active_run_id, cast(RunState, state))
            )

    def _track_run_background_task(self, awaitable: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(awaitable)
        self._run_background_tasks.add(task)
        task.add_done_callback(self._run_background_tasks.discard)

    async def _finish_active_run(self, state: str, *, summary: str = "") -> None:
        run_id = self._active_run_id
        if run_id is None:
            return
        if state not in {"queued", "running", "blocked", "done", "failed", "cancelled"}:
            state = "failed"
        if self._run_background_tasks:
            await asyncio.gather(*self._run_background_tasks, return_exceptions=True)
        run = await self.run_store.load_run(run_id)
        events = await self.run_store.load_events(run_id)
        working_directory = (
            Path(run.working_directory).expanduser()
            if run is not None and run.working_directory
            else self.config.general.working_directory
        )
        verification, diff = await _collect_run_artifacts(working_directory, state, events)
        summary_text = summary or self._active_run_summary
        await self.run_store.finish_run(
            run_id,
            cast(RunState, state),
            summary=summary_text,
            verification=verification,
            diff=diff,
        )
        await self.run_store.append_event(run_id, "run_finished", {"state": state})
        self._active_run_id = None
        self._active_run_summary = ""

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
        self._record_run_event_later(
            "permission_response",
            {"tool_call_id": request.call.id, "name": request.call.name, "resolution": resolution},
        )
        self._set_run_state_later("running")
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
        self._tool_entry_by_call_id.clear()
        self._render_transcript()
        self._append_system("Transcript cleared.")

    def _set_model(self, model: str) -> None:
        model, persist_global = _strip_global_flag(model)
        if not model:
            self._append_system(_model_help_text(self.config))
            return

        parsed = _parse_model_argument(model, self.config.general.default_provider)
        if parsed is None:
            self._append_system(_model_help_text(self.config))
            return

        provider, selected_model = parsed
        self.config = _replace_general(self.config, default_provider=provider, default_model=selected_model)
        persisted_path: Path | None = None
        if persist_global:
            try:
                persisted_path = set_global_default_model(
                    provider,
                    selected_model,
                    config_path=global_config_path(self.config),
                )
                self.config = self._verified_global_model_config(provider, selected_model, persisted_path)
            except ConfigError as exc:
                self._append_system(f"Model set for this session, but global config was not updated: {exc}")
        self._rebuild_agent()
        self._update_status()
        suffix = f"\nSaved as global default in {persisted_path}." if persisted_path is not None else ""
        if self.provider_error:
            self._append_system(
                f"Model set to {provider}:{selected_model}, but provider setup is incomplete.\n"
                f"{self.provider_error}{suffix}"
            )
        else:
            self._append_system(f"Model set to {provider}:{selected_model}.{suffix}")

    def _verified_global_model_config(
        self,
        provider: str,
        selected_model: str,
        persisted_path: Path,
    ) -> LibreClawConfig:
        reloaded = load_config(config_path=persisted_path, working_directory=self.config.general.working_directory)
        resolved_provider = _canonical_tui_provider(reloaded.general.default_provider)
        resolved_model = _effective_model(reloaded)
        if resolved_provider != provider or resolved_model != selected_model:
            raise ConfigError(
                f"wrote {persisted_path}, but the next launch resolves to "
                f"{resolved_provider}:{resolved_model}. Check LIBRE_CLAW_DEFAULT_PROVIDER "
                "or LIBRE_CLAW_DEFAULT_MODEL environment overrides."
            )
        return reloaded

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

    def _compact_context(self, argument: str = "") -> None:
        options = _parse_compact_options(argument)
        if options.error is not None:
            self._append_system(options.error)
            return
        if options.status:
            self._append_system(self._context_report())
            return

        before = len(self.session.messages)
        keep_last = options.keep_last
        if options.force and before <= keep_last:
            keep_last = max(1, before - 1)
        before_meter = self._context_meter()
        summary = self.session.compact(keep_last=keep_last)
        after = len(self.session.messages)
        if summary is None or before == after:
            self._append_system("Context is already compact enough. Try `/compact --force --keep 1` if you want to summarize more.")
            return
        self._rebuild_agent()
        self._update_status()
        after_meter = self._context_meter()
        self._append_system(
            f"Compacted context from {before} messages to {after}. "
            f"Context {before_meter.percent}% -> {after_meter.percent}%."
        )

    async def _handle_goal_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        value = parts[1].strip() if len(parts) > 1 else ""

        if action in {"", "help"}:
            self._append_system(_goal_help_text(self.config, self._goal_max_turns))
            return

        if action == "status":
            self._append_system(self._goal_status_text())
            return

        if action == "stop":
            if self._goal_description is None:
                self._append_system("No active goal to stop.")
                return
            self._cancel_active_generation()
            return

        if action == "max":
            if not value.isdigit() or int(value) < 1:
                self._append_system("Usage: /goal max <turns>, with turns >= 1")
                return
            self._goal_max_turns = int(value)
            self._append_system(f"Goal max turns set to {self._goal_max_turns} for this session.")
            return

        if self._active_task is not None and not self._active_task.done():
            self._append_system("A response is already streaming. Use /cancel or /goal stop to stop it.")
            return
        goal = argument.strip()
        run = await self._start_run("goal", goal)
        await self._record_run_event("user_goal", {"goal": goal, "max_turns": self._goal_max_turns})
        if self.agent is None:
            self._append_system(self.provider_error or "No provider is available.")
            await self._record_run_event("error", {"message": self.provider_error or "No provider is available."})
            await self._finish_active_run("failed", summary=self.provider_error or "No provider is available.")
            return

        try:
            judge_provider, judge_label = self._create_goal_judge_provider()
        except ProviderConfigurationError as exc:
            self._append_system(f"Goal judge provider is not available: {exc}")
            await self._record_run_event("error", {"message": str(exc)})
            await self._finish_active_run("failed", summary=str(exc))
            return

        self._goal_description = goal
        self._goal_turn = 0
        self._last_goal_decision = None
        self._append_user(f"Goal: {goal}")
        self._append_system(
            f"Run {run.run_id} started. Goal max {self._goal_max_turns} turn(s). Judge: {judge_label}. "
            "Use /goal stop to cancel."
        )
        assistant_index = self._append_assistant("")
        self._active_task = asyncio.create_task(self._stream_goal_response(goal, assistant_index, judge_provider))

    def _create_goal_judge_provider(self) -> tuple[LLMProvider, str]:
        configured_provider = self.config.goal.judge_provider.strip().lower()
        provider = (
            _canonical_tui_provider(self.config.general.default_provider)
            if configured_provider in {"", "current"}
            else _canonical_tui_provider(configured_provider)
        )
        if provider not in SUPPORTED_PROVIDERS:
            raise ProviderConfigurationError(
                "[goal].judge_provider must be 'current', 'anthropic', 'openai', 'openrouter', 'ollama', or 'codex'."
            )

        model = self.config.goal.judge_model.strip()
        if not model:
            model = _effective_model(_replace_general(self.config, default_provider=provider))
        judge_config = _replace_general(self.config, default_provider=provider, default_model=model)
        return create_provider(judge_config), f"{provider}:{model}"

    def _goal_status_text(self) -> str:
        if self._goal_description is None:
            return f"No active goal. Max turns: {self._goal_max_turns}."
        lines = [
            f"Goal active: turn {self._goal_turn}/{self._goal_max_turns}",
            self._goal_description,
        ]
        if self._last_goal_decision is not None:
            lines.append(f"Last judge: {_goal_judge_text(self._goal_turn, self._last_goal_decision)}")
        return "\n".join(lines)

    async def _handle_runs_command(self, argument: str) -> None:
        limit = 20
        if argument:
            if not argument.isdigit() or int(argument) < 1:
                self._append_system("Usage: /runs [N]")
                return
            limit = int(argument)
        runs = await self.run_store.list_runs(limit=limit)
        if not runs:
            self._append_system("No durable runs yet.")
            return
        self._append_system("\n".join(_run_list_line(run) for run in runs))

    async def _handle_run_command(self, argument: str) -> None:
        run_id = await self._resolve_run_id(argument)
        if run_id is None:
            self._append_system("Usage: /run <id>")
            return
        run = await self.run_store.load_run(run_id)
        if run is None:
            self._append_system(f"No durable run found for: {argument}")
            return
        events = await self.run_store.load_events(run.run_id)
        self._append_system(_run_detail_text(run, events))

    async def _handle_resume_command(self, argument: str) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._append_system("A response is already streaming. Use /cancel before resuming a run.")
            return
        run_id = await self._resolve_run_id(argument)
        if run_id is None:
            self._append_system("Usage: /resume <id>")
            return
        run = await self.run_store.load_run(run_id)
        if run is None:
            self._append_system(f"No durable run found for: {argument}")
            return
        events = await self.run_store.load_events(run.run_id)
        self.transcript = _transcript_from_run_events(events)
        self._tool_entry_by_call_id.clear()
        self._active_run_id = run.run_id if run.state in {"running", "blocked"} else None
        self._render_transcript()
        self._append_system(f"Loaded run {run.run_id} ({run.state}) from {run.path}.")

    async def _cancel_run_command(self, argument: str) -> None:
        run_id = await self._resolve_run_id(argument)
        if run_id is None:
            self._append_system("Usage: /cancel [run-id]")
            return
        if self._active_run_id == run_id and self._active_task is not None and not self._active_task.done():
            self._cancel_active_generation()
            return
        run = await self.run_store.load_run(run_id)
        if run is None:
            self._append_system(f"No durable run found for: {argument}")
            return
        summary_path = run.path / "summary.md"
        diff_path = run.path / "diff.patch"
        summary = await asyncio.to_thread(summary_path.read_text, encoding="utf-8") if summary_path.exists() else ""
        diff = await asyncio.to_thread(diff_path.read_text, encoding="utf-8") if diff_path.exists() else ""
        await self.run_store.append_event(run.run_id, "cancelled", {"reason": "Cancelled by user command."})
        await self.run_store.finish_run(
            run.run_id,
            "cancelled",
            summary=summary,
            verification="Run cancelled by user command.\n",
            diff=diff,
        )
        self._append_system(f"Run {run.run_id} marked cancelled.")

    async def _resolve_run_id(self, value: str) -> str | None:
        query = value.strip()
        if not query:
            return None
        exact = await self.run_store.load_run(query)
        if exact is not None:
            return exact.run_id
        matches = [run.run_id for run in await self.run_store.list_runs(limit=100) if run.run_id.startswith(query)]
        return matches[0] if len(matches) == 1 else None

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

    async def _handle_codex_command(self, argument: str) -> None:
        parts = argument.split()
        action = parts[0].lower() if parts else "status"
        value = " ".join(parts[1:]).strip()

        if action == "status":
            status = await codex_status()
            self._append_system(status.detail)
            return

        if action == "login":
            browser_login = value == "browser"
            self._append_system(
                "Starting Codex login. Use the code/link Codex prints, then return here. "
                "Libre Claw will use that Codex auth when provider is `codex`."
            )
            try:
                result = await self._stream_codex_login(browser_login=browser_login)
            except CodexCliError as exc:
                self._append_system(str(exc))
                return
            self._append_system(f"Codex login exited with {result.exit_code}.")
            self._rebuild_agent()
            self._update_status()
            return

        if action == "logout":
            try:
                result = await codex_logout()
            except CodexCliError as exc:
                self._append_system(str(exc))
                return
            self._append_system(result.output or f"Codex logout exited with {result.exit_code}.")
            self._rebuild_agent()
            self._update_status()
            return

        if action == "use":
            model = value or "gpt-5.5"
            self._set_model(f"codex:{model}")
            return

        self._append_system("Usage: /codex login [browser]|status|logout|use [model]")

    async def _stream_codex_login(self, browser_login: bool) -> CodexCommandResult:
        args = ["codex", "login"]
        if not browser_login:
            args.append("--device-auth")

        final: CodexCommandResult | None = None
        async for event in stream_codex_command(args):
            if isinstance(event, CodexCommandResult):
                final = event
                continue
            self._append_system(event.text.rstrip())

        if final is None:
            raise CodexCliError("Codex login ended without a result.")
        return final

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
        index = self._append_entry(
            "tool",
            self._format_arguments(call.arguments),
            title=f"{call.name} pending",
            collapsed=True,
            metadata={"tool": call.name, "status": "pending", "call": call},
        )
        self._tool_entry_by_call_id[call.id] = index
        return index

    def _append_tool_result(self, call: ToolCall, result: ToolResult) -> int:
        metadata = dict(result.metadata)
        metadata.update({"tool": call.name, "status": "error" if result.is_error else "result", "call": call})
        content = result.as_text()
        if call.name == "edit_file" and "before" in result.metadata and "after" in result.metadata:
            content = self._diff_text(
                str(result.metadata.get("before", "")),
                str(result.metadata.get("after", "")),
                str(result.metadata.get("path", call.arguments.get("path", "file"))),
            )
            metadata["syntax"] = "diff"
        index = self._tool_entry_by_call_id.pop(call.id, None)
        title = f"{call.name} {'error' if result.is_error else 'result'}"
        if index is None or index >= len(self.transcript):
            return self._append_entry("tool", content, title=title, collapsed=True, metadata=metadata)

        entry = self.transcript[index]
        entry.content = content
        entry.title = title
        entry.collapsed = True
        entry.metadata = metadata
        self._render_transcript()
        return index

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
        if not self._replace_rendered_tail_entry(index):
            self._render_transcript()

    def _render_transcript(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.clear()
        self._chat_entry_spans.clear()
        for index, entry in enumerate(self.transcript):
            start_line = len(chat.lines)
            chat.write(self._format_entry(entry, index), scroll_end=True)
            self._chat_entry_spans[index] = (start_line, len(chat.lines))

    def _replace_rendered_tail_entry(self, index: int) -> bool:
        if index != len(self.transcript) - 1:
            return False

        span = self._chat_entry_spans.get(index)
        if span is None:
            return False

        chat = self.query_one("#chat", RichLog)
        start_line, end_line = span
        if start_line < 0 or end_line < start_line or end_line > len(chat.lines):
            return False

        del chat.lines[start_line:end_line]
        if hasattr(chat, "_line_cache"):
            chat._line_cache.clear()
        chat.write(self._format_entry(self.transcript[index], index), scroll_end=True)
        self._chat_entry_spans[index] = (start_line, len(chat.lines))
        return True

    def _format_entry(self, entry: TranscriptEntry, index: int = 0) -> RenderableType:
        if entry.role == "user":
            return Text.assemble(("User: ", "bold #0070F3"), entry.content)
        if entry.role == "assistant":
            if not entry.content:
                return Text("Libre Claw: streaming...", style=f"bold {ASSISTANT_ACCENT} dim")
            return Group(Text("Libre Claw:", style=f"bold {ASSISTANT_ACCENT}"), Markdown(entry.content))
        if entry.role == "tool":
            title = entry.title or "Tool"
            metadata = entry.metadata or {}
            status = str(metadata.get("status", ""))
            style = _tool_style(status)
            if entry.collapsed:
                return Text.assemble(
                    (f"Tool {self._tool_display_index(index)}: ", f"bold {style}"),
                    (title, f"bold {style}"),
                    (" - ", "dim"),
                    (_tool_preview(entry), "dim"),
                )
            if metadata.get("syntax") == "diff":
                return Group(Text(f"Tool {self._tool_display_index(index)}: {title}", style=f"bold {style}"), Syntax(entry.content, "diff"))
            return Text.assemble(
                (f"Tool {self._tool_display_index(index)}: {title}\n", f"bold {style}"),
                _compact_tool_output(entry.content, expanded=True),
            )
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
            context_window_tokens=self.config.agent.context_window_tokens,
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
        self.query_one("#sidebar-rail", Vertical).display = not self.sidebar_visible

    def _update_startup_panel(self) -> None:
        self.query_one("#startup-panel", Static).update(_startup_renderable(self.startup_expanded))

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

        if lowered.startswith("/codex "):
            query = lowered.removeprefix("/codex ").strip()
            suggestions = [
                SlashCommand("/codex login", "/codex login", "Start Codex device auth inside Libre Claw"),
                SlashCommand("/codex status", "/codex status", "Show Codex login status"),
                SlashCommand("/codex logout", "/codex logout", "Log out of Codex"),
                SlashCommand("/codex use gpt-5.5", "/codex use gpt-5.5", "Switch to Codex provider"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/goal "):
            query = lowered.removeprefix("/goal ").strip()
            suggestions = [
                SlashCommand("/goal status", "/goal status", "Show active goal progress"),
                SlashCommand("/goal stop", "/goal stop", "Cancel active goal mode"),
                SlashCommand("/goal max 20", "/goal max 20", "Set max turns for this session"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
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
        meter = self._context_meter()
        lines = [
            "Session usage:",
            f"- Tokens: {self.usage.total_tokens} total",
            f"- Input: {self.usage.input_tokens}",
            f"- Output: {self.usage.output_tokens}",
        ]
        if self.usage.cached_tokens:
            lines.append(f"- Cached input: {self.usage.cached_tokens}")
        if self.usage.reasoning_tokens:
            lines.append(f"- Reasoning output: {self.usage.reasoning_tokens}")
        lines.append(f"- Cost: {_format_usage_cost(self.usage)}")
        lines.append(
            f"- Context estimate: {_format_token_count(meter.estimated_tokens)}/"
            f"{_format_token_count(meter.context_window_tokens)} tokens ({meter.display_percent})"
        )
        if self.usage.cost is None:
            lines.append(
                "Provider token/cost totals are shown when the provider reports them. "
                "Codex CLI currently does not return usage, so Libre Claw shows estimated context tokens instead."
            )
        return "\n".join(lines)

    def _context_meter(self) -> ContextMeter:
        extra_texts = tuple(
            text
            for text in (
                self.config.agent.system_prompt,
                self.config.agent.system_prompt_extra,
                *self.memory_facts,
            )
            if text
        )
        estimated_tokens = estimate_context_tokens(
            self.session.messages,
            summary=self.session.summary,
            extra_texts=extra_texts,
        )
        context_window = max(1, self.config.agent.context_window_tokens)
        return ContextMeter(
            estimated_tokens=estimated_tokens,
            context_window_tokens=context_window,
            ratio=estimated_tokens / context_window,
        )

    def _context_report(self) -> str:
        meter = self._context_meter()
        return (
            f"Context: {_context_status_text(meter)} "
            f"({_format_token_count(meter.estimated_tokens)}/"
            f"{_format_token_count(meter.context_window_tokens)} estimated tokens). "
            f"Auto compact threshold: {int(self.config.agent.auto_compact_threshold * 100)}%."
        )

    def _telegram_status(self) -> str:
        enabled = "enabled" if self.config.telegram.enabled else "disabled"
        return (
            f"Telegram bridge is {enabled}. Run `libre-claw telegram` for the standalone daemon. "
            f"Token env: {self.config.telegram.bot_token_env}."
        )

    def _status_text(self) -> str:
        provider = self.config.general.default_provider
        model = _effective_model(self.config)
        meter = self._context_meter()
        elapsed = int(time.monotonic() - self._started_at)
        if self._goal_description is not None and self._active_task is not None and not self._active_task.done():
            active = f"goal {self._goal_turn}/{self._goal_max_turns}"
        else:
            active = "running" if self._active_task is not None and not self._active_task.done() else "idle"
        return (
            f"Libre Claw v{__version__} | {provider}:{model} | {_format_usage_cost(self.usage)} | "
            f"{_token_status_text(self.usage, meter)} | ctx {_context_status_text(meter)} | {elapsed}s | {active}"
        )

    def _update_status(self) -> None:
        if self.config.tui.show_status_bar:
            self.query_one("#status", Static).update(self._status_text())

    def _input_placeholder(self) -> str:
        if self.palette_open:
            return "Command palette query..."
        if self._pending_permission is not None:
            return "Permission prompt active: click a choice or press y/n/a/!"
        if self._goal_description is not None:
            return "Goal mode active... (/goal status, /goal stop)"
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
        goal=config.goal,
        daemon=config.daemon,
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


def _strip_global_flag(argument: str) -> tuple[str, bool]:
    parts = argument.split()
    if "--global" not in parts:
        return argument.strip(), False
    cleaned = " ".join(part for part in parts if part != "--global")
    return cleaned.strip(), True


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
        "Add `--global` to save the provider and model in ~/.libre-claw/config.toml.",
        "Use `Tab` after `/model ` to complete a suggested model.",
        "Provider key setup stays in the secure CLI/keyring path:",
    ]
    lines.extend(f"- libre-claw auth set-key {name}" for name in SUPPORTED_PROVIDERS if name not in {"ollama", "codex"})
    lines.append("- libre-claw auth set-key ollama  # required for Ollama Cloud")
    lines.append("- /codex login  # ChatGPT/Codex auth, no OpenAI API key")
    lines.append("")
    lines.append("Suggested models:")
    for suggestion in _model_suggestion_commands(config):
        lines.append(f"- {suggestion.name} - {suggestion.description}")
    return "\n".join(lines)


def _provider_help_text(config: LibreClawConfig) -> str:
    provider = _canonical_tui_provider(config.general.default_provider)
    lines = [
        f"Current provider: {provider}",
        "Use `/provider anthropic|openai|openrouter|ollama|codex`, or use `/model <provider>:<name>` to switch both.",
        "For Codex/ChatGPT auth, run `/codex login` then `/provider codex`.",
    ]
    return "\n".join(lines)


def _goal_help_text(config: LibreClawConfig, session_max_turns: int) -> str:
    configured_provider = config.goal.judge_provider or "current"
    judge_provider = (
        _canonical_tui_provider(config.general.default_provider)
        if configured_provider == "current"
        else _canonical_tui_provider(configured_provider)
    )
    judge_model = config.goal.judge_model or _effective_model(_replace_general(config, default_provider=judge_provider))
    return "\n".join(
        [
            "Goal mode runs the agent repeatedly until a separate judge marks the goal done.",
            f"Usage: /goal <objective>",
            f"Session max turns: {session_max_turns}",
            f"Configured max turns: {config.goal.max_turns}",
            f"Judge: {judge_provider}:{judge_model}",
            "Commands: /goal status, /goal stop, /goal max <turns>",
        ]
    )


def _goal_judge_text(turn: int, decision: JudgeDecision) -> str:
    state = "done" if decision.done else "continue"
    confidence = int(round(decision.confidence * 100))
    lines = [f"Judge turn {turn}: {state} ({confidence}% confidence).", decision.reason]
    if not decision.done and decision.next_prompt:
        lines.append(f"Next: {decision.next_prompt}")
    return "\n".join(lines)


def _usage_payload(usage: Usage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost": usage.cost,
    }


async def _collect_run_artifacts(
    working_directory: Path,
    state: str,
    events: list[RunEvent],
) -> tuple[str, str]:
    status_capture = await _run_git_command(
        working_directory,
        ("status", "--short"),
        max_stdout_chars=RUN_STATUS_MAX_CHARS,
    )
    if status_capture.exit_code != 0:
        verification = _run_verification_text(
            state,
            events,
            working_directory,
            git_status="",
            git_error=status_capture.stderr.strip() or "git status failed",
            diff_truncated=False,
        )
        return verification, ""

    diff_capture = await _run_git_command(
        working_directory,
        ("diff", "--no-ext-diff", "--binary", "HEAD", "--"),
        max_stdout_chars=RUN_DIFF_MAX_CHARS,
    )
    if diff_capture.exit_code != 0:
        diff_capture = await _run_git_command(
            working_directory,
            ("diff", "--no-ext-diff", "--binary", "--"),
            max_stdout_chars=RUN_DIFF_MAX_CHARS,
        )

    diff = diff_capture.stdout if diff_capture.exit_code == 0 else ""
    diff_error = "" if diff_capture.exit_code == 0 else diff_capture.stderr.strip()
    if diff_capture.stdout_truncated:
        diff += "\n\n# Libre Claw: diff.patch was truncated at the run artifact size limit.\n"

    verification = _run_verification_text(
        state,
        events,
        working_directory,
        git_status=status_capture.stdout,
        git_error=diff_error,
        diff_truncated=diff_capture.stdout_truncated,
    )
    return verification, diff


def _run_verification_text(
    state: str,
    events: list[RunEvent],
    working_directory: Path,
    *,
    git_status: str,
    git_error: str = "",
    diff_truncated: bool = False,
) -> str:
    tool_results = [event for event in events if event.type == "tool_result"]
    errors = [event for event in events if event.type == "error"]
    lines = [
        f"Run finished with state: {state}",
        f"Working directory: {working_directory}",
        "",
        "Tool results:",
    ]

    if not tool_results:
        lines.append("- No tool results were recorded.")
    else:
        for event in tool_results[-12:]:
            lines.append("- " + _tool_result_verification_line(event))
        omitted = len(tool_results) - 12
        if omitted > 0:
            lines.append(f"- ... {omitted} earlier tool result(s) omitted from this summary.")

    if errors:
        lines.extend(["", "Errors:"])
        for event in errors[-5:]:
            lines.append("- " + str(event.data.get("message", "Unknown error")))

    lines.extend(["", "Git status at finish:"])
    if git_error:
        lines.append(f"Git artifacts unavailable or partial: {git_error}")
    elif git_status.strip():
        lines.append(git_status.rstrip())
    else:
        lines.append("Clean working tree for tracked files.")

    lines.extend(
        [
            "",
            "Diff artifact:",
            "Captured with `git diff --no-ext-diff --binary HEAD --` for tracked files when available.",
            "Untracked files are listed in status but are not embedded in diff.patch.",
        ]
    )
    if diff_truncated:
        lines.append("diff.patch was truncated at the configured artifact size limit.")
    return "\n".join(lines).rstrip() + "\n"


def _tool_result_verification_line(event: RunEvent) -> str:
    name = str(event.data.get("name", "tool"))
    result = "error" if event.data.get("is_error") else "ok"
    metadata = event.data.get("metadata", {})
    arguments = event.data.get("arguments", {})
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(arguments, dict):
        arguments = {}
    exit_code = metadata.get("exit_code")
    duration_ms = metadata.get("duration_ms")
    suffixes: list[str] = []
    if exit_code is not None:
        suffixes.append(f"exit_code={exit_code}")
    if duration_ms is not None:
        suffixes.append(f"duration_ms={duration_ms}")
    command = arguments.get("command")
    if isinstance(command, str) and command:
        suffixes.append(f"command={command!r}")
    suffix = f" ({', '.join(suffixes)})" if suffixes else ""
    return f"{name}: {result}{suffix}"


async def _run_git_command(
    working_directory: Path,
    args: tuple[str, ...],
    *,
    max_stdout_chars: int,
) -> ProcessCapture:
    return await _run_process(
        ("git", "-C", str(working_directory), *args),
        timeout=RUN_ARTIFACT_TIMEOUT,
        max_stdout_chars=max_stdout_chars,
        max_stderr_chars=RUN_ARTIFACT_STDERR_MAX_CHARS,
    )


async def _run_process(
    args: tuple[str, ...],
    *,
    timeout: float,
    max_stdout_chars: int,
    max_stderr_chars: int,
) -> ProcessCapture:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(_read_capped_text(process.stdout, max_stdout_chars))
        stderr_task = asyncio.create_task(_read_capped_text(process.stderr, max_stderr_chars))
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await _cancel_capture_tasks(stdout_task, stderr_task)
            return ProcessCapture(
                exit_code=124,
                stdout="",
                stderr=f"command timed out after {timeout:.0f}s",
            )
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        return ProcessCapture(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
    except OSError as exc:
        return ProcessCapture(exit_code=127, stdout="", stderr=str(exc))
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if "stdout_task" in locals() and "stderr_task" in locals():
            await _cancel_capture_tasks(stdout_task, stderr_task)
        raise


async def _read_capped_text(
    stream: asyncio.StreamReader | None,
    max_chars: int,
) -> tuple[str, bool]:
    if stream is None:
        return "", False
    parts: list[str] = []
    stored = 0
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        total += len(text)
        if stored < max_chars:
            piece = text[: max_chars - stored]
            parts.append(piece)
            stored += len(piece)
    return "".join(parts), total > stored


async def _cancel_capture_tasks(*tasks: asyncio.Task[tuple[str, bool]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _run_list_line(run: RunRecord) -> str:
    title = run.title.replace("\n", " ")
    return f"{run.run_id} [{run.state}] {run.kind} {run.provider}:{run.model} - {title}"


def _run_detail_text(run: RunRecord, events: list[RunEvent]) -> str:
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.type] = event_counts.get(event.type, 0) + 1
    counts = ", ".join(f"{key}={value}" for key, value in sorted(event_counts.items())) or "no events"
    return "\n".join(
        [
            f"Run {run.run_id}",
            f"State: {run.state}",
            f"Kind: {run.kind}",
            f"Model: {run.provider}:{run.model}",
            f"Working directory: {run.working_directory or 'unknown'}",
            f"Created: {run.created_at}",
            f"Updated: {run.updated_at}",
            f"Path: {run.path}",
            f"Title: {run.title}",
            f"Events: {len(events)} ({counts})",
            "Artifacts: " + _run_artifact_summary(run),
        ]
    )


def _run_artifact_summary(run: RunRecord) -> str:
    parts: list[str] = []
    for name in ("events.jsonl", "summary.md", "verification.md", "diff.patch"):
        path = run.path / name
        try:
            size = path.stat().st_size
        except OSError:
            parts.append(f"{name}=missing")
        else:
            parts.append(f"{name}={size}B")
    return ", ".join(parts)


def _transcript_from_run_events(events: list[RunEvent]) -> list[TranscriptEntry]:
    entries: list[TranscriptEntry] = []
    assistant_index: int | None = None
    tool_index_by_call_id: dict[str, int] = {}

    for event in events:
        if event.type == "user_message":
            entries.append(TranscriptEntry(role="user", content=str(event.data.get("content", ""))))
            assistant_index = None
            continue
        if event.type == "user_goal":
            entries.append(TranscriptEntry(role="user", content="Goal: " + str(event.data.get("goal", ""))))
            assistant_index = None
            continue
        if event.type == "assistant_delta":
            if assistant_index is None or assistant_index >= len(entries) or entries[assistant_index].role != "assistant":
                entries.append(TranscriptEntry(role="assistant", content=""))
                assistant_index = len(entries) - 1
            entries[assistant_index].content += str(event.data.get("text", ""))
            continue
        if event.type == "tool_call":
            call_id = str(event.data.get("id", ""))
            entries.append(
                TranscriptEntry(
                    role="tool",
                    content=json.dumps(event.data.get("arguments", {}), sort_keys=True, default=str),
                    title=f"{event.data.get('name', 'tool')} pending",
                    collapsed=True,
                    metadata={"status": "pending", "tool": event.data.get("name", "tool")},
                )
            )
            if call_id:
                tool_index_by_call_id[call_id] = len(entries) - 1
            continue
        if event.type == "tool_result":
            call_id = str(event.data.get("tool_call_id", ""))
            index = tool_index_by_call_id.get(call_id)
            title = f"{event.data.get('name', 'tool')} {'error' if event.data.get('is_error') else 'result'}"
            entry = TranscriptEntry(
                role="tool",
                content=str(event.data.get("content", "")),
                title=title,
                collapsed=True,
                metadata={"status": "error" if event.data.get("is_error") else "result", "tool": event.data.get("name", "tool")},
            )
            if index is None or index >= len(entries):
                entries.append(entry)
            else:
                entries[index] = entry
            continue
        if event.type in {"error", "cancelled", "goal_judge", "goal_complete", "goal_stopped"}:
            entries.append(TranscriptEntry(role="system", content=_run_event_summary(event)))

    return entries


def _run_event_summary(event: RunEvent) -> str:
    if event.type == "error":
        return str(event.data.get("message", "Run error"))
    if event.type == "cancelled":
        return "Run cancelled: " + str(event.data.get("reason", "cancelled"))
    if event.type == "goal_judge":
        state = "done" if event.data.get("done") else "continue"
        return f"Judge: {state} - {event.data.get('reason', '')}"
    if event.type == "goal_complete":
        return "Goal complete: " + str(event.data.get("reason", "done"))
    if event.type == "goal_stopped":
        return "Goal stopped: " + str(event.data.get("reason", "stopped"))
    return f"{event.type}: {event.data}"


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


def _format_usage_cost(usage: Usage) -> str:
    if usage.cost is None or usage.cost == 0:
        return "$0.00"
    if usage.cost < 0.01:
        return f"${usage.cost:.6f}"
    return f"${usage.cost:.2f}"


def _format_token_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(value)


def _token_status_text(usage: Usage, meter: ContextMeter) -> str:
    if usage.total_tokens:
        return f"{_format_token_count(usage.total_tokens)} provider tokens"
    return f"~{_format_token_count(meter.estimated_tokens)} est tokens"


def _context_status_text(meter: ContextMeter) -> str:
    return f"{_context_bar(meter)} {meter.display_percent}"


def _context_bar(meter: ContextMeter, width: int = 10) -> str:
    filled = max(0, min(width, int(round(min(meter.ratio, 1.0) * width))))
    if meter.estimated_tokens > 0 and filled == 0:
        filled = 1
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _parse_compact_options(argument: str) -> CompactOptions:
    tokens = argument.split()
    if not tokens:
        return CompactOptions()
    if tokens == ["status"]:
        return CompactOptions(status=True)

    keep_last = 8
    force = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"status", "--status"}:
            return CompactOptions(status=True)
        if token in {"force", "--force"}:
            force = True
            index += 1
            continue
        if token == "--keep":
            if index + 1 >= len(tokens) or not tokens[index + 1].isdigit():
                return CompactOptions(error="Usage: /compact [status|--force] [--keep N]")
            keep_last = int(tokens[index + 1])
            index += 2
            continue
        if token.startswith("--keep="):
            value = token.removeprefix("--keep=")
            if not value.isdigit():
                return CompactOptions(error="Usage: /compact [status|--force] [--keep N]")
            keep_last = int(value)
            index += 1
            continue
        return CompactOptions(error="Usage: /compact [status|--force] [--keep N]")

    if keep_last < 1:
        return CompactOptions(error="Keep count must be at least 1.")
    return CompactOptions(keep_last=keep_last, force=force)


def _tool_style(status: str) -> str:
    if status == "error":
        return "red"
    if status == "pending":
        return "#0070F3"
    return "#8B5CF6"


def _tool_preview(entry: TranscriptEntry, max_length: int = 120) -> str:
    metadata = entry.metadata or {}
    if metadata.get("syntax") == "diff":
        preview = "diff ready"
    else:
        preview = _compact_tool_output(entry.content, expanded=False)
    preview = " ".join(preview.split())
    if len(preview) > max_length:
        return preview[: max_length - 1].rstrip() + "..."
    return preview or "no output"


def _compact_tool_output(content: str, expanded: bool) -> str:
    limit = 12 if expanded else 3
    lines = content.splitlines()
    if len(lines) <= limit:
        return content
    shown = "\n".join(lines[:limit])
    hidden = len(lines) - limit
    return f"{shown}\n... {hidden} more lines hidden; use /tools expand <index> to show all"


def _startup_renderable(expanded: bool) -> RenderableType:
    banner = Text(STARTUP_ASCII.strip(), style=ASSISTANT_ACCENT)
    if not expanded:
        return Group(
            banner,
            Text(
                f"Libre Claw v{__version__} - release notes collapsed. Click this header to expand.",
                style="dim",
            ),
            Text(PROJECT_NOTICE, style="dim"),
        )
    return Group(
        banner,
        Text(f"Libre Claw v{__version__}", style=f"bold {ASSISTANT_ACCENT}"),
        Text(PROJECT_NOTICE, style="dim"),
        Markdown(latest_release_notes()),
        Text("Click this header to collapse. Type /help for commands.", style="dim"),
    )


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
