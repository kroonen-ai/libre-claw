# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import mimetypes
import shlex
import time
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import unquote, urlparse

from pygments.token import Token as PygmentsToken
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.style import Style
from rich.syntax import Syntax, SyntaxTheme
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.selection import Selection
from textual.widgets import Button, DirectoryTree, Input, RichLog, Static

from libre_claw import __version__
from libre_claw.auth.api_keys import ApiKeyStore, KeyStorageError
from libre_claw.auth.codex import CodexCliError, CodexCommandResult, codex_logout, codex_status, stream_codex_command
from libre_claw.config import (
    ConfigError,
    FallbackConfig,
    FallbackRouteConfig,
    GeneralConfig,
    LibreClawConfig,
    global_config_path,
    load_config,
    set_global_fallback_config,
    set_global_default_model,
    set_global_theme,
    set_global_working_directory,
)
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentFallback,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    AutomationError,
    AutomationRecord,
    AutomationRoute,
    AutomationStore,
    GoalComplete,
    GoalJudgeResult,
    GoalRunner,
    GoalStopped,
    GoalTurnStarted,
    HeartbeatError,
    JudgeDecision,
    RunEvent,
    RunRecord,
    RunState,
    RunStore,
    Session,
    automation_examples,
    heartbeat_prompt,
    parse_heartbeat_interval,
)
from libre_claw.core.memory import (
    MemoryItem,
    MemoryStore,
    extract_memories_with_provider,
    new_session_archive_id,
    redact_secrets,
    summarize_session_for_memory,
)
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.review import RUN_ARTIFACT_NAMES, browser_artifact_text, pending_approvals, run_changes_text, run_plan_text
from libre_claw.core.sandbox import SandboxPolicy, SandboxViolation
from libre_claw.core.session import ChatMessage, UserAttachment, estimate_context_tokens, session_to_payload
from libre_claw.core.skills import Skill, SkillError, SkillScope, SkillStore
from libre_claw.core.soul import SoulError, SoulStore
from libre_claw.core.themes import THEME_ALIASES, THEME_PALETTES, normalize_theme, tui_theme_palette
from libre_claw.core.tools import ToolCall, ToolResult
from libre_claw.core.usage import (
    load_usage_records,
    openrouter_attribution_text,
    openrouter_model_presets_text,
    usage_report_text,
)
from libre_claw.core.workspace import (
    default_claw_workspace_path,
    initialize_claw_workspace,
    workspace_result_text,
    workspace_status_text,
)
from libre_claw.daemon import DaemonClient, daemon_base_url
from libre_claw.integrations.petdex import PetdexClient, petdex_message_preview, petdex_tool_details
from libre_claw.providers import (
    LLMProvider,
    ProviderConfigurationError,
    Usage,
    combine_usage,
    create_fallback_providers,
    create_provider,
)
from libre_claw.providers.anthropic_catalog import ANTHROPIC_MODEL_PRESETS
from libre_claw.providers.codex_catalog import CODEX_MODEL_PRESETS
from libre_claw.providers.ollama_catalog import OLLAMA_MODEL_PRESETS
from libre_claw.providers.moonshot_catalog import MOONSHOT_MODEL_PRESETS
from libre_claw.providers.moonshot_metadata import apply_moonshot_model_limits
from libre_claw.providers.openrouter_catalog import OPENROUTER_MODEL_PRESETS
from libre_claw.providers.openrouter_metadata import apply_openrouter_model_limits, detect_openrouter_model_limits
from libre_claw.release import latest_release_notes
from libre_claw.tools_builtin import create_builtin_registry
from libre_claw.updater import (
    UpdateError,
    libre_claw_checkout_path,
    update_checkout,
    update_result_text,
)


TranscriptRole = Literal["startup", "user", "assistant", "system", "tool", "permission", "file", "attachment"]
ArtifactTab = Literal["plan", "summary", "verify", "diff", "browser"]


@dataclass
class TranscriptEntry:
    role: TranscriptRole
    content: str
    title: str | None = None
    collapsed: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class FileChangeStats:
    additions: int
    deletions: int
    hunks: int

    @property
    def compact(self) -> str:
        hunk_label = "hunk" if self.hunks == 1 else "hunks"
        return f"+{self.additions} -{self.deletions} · {self.hunks} {hunk_label}"


@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    description: str


@dataclass(frozen=True)
class PetdexTUISnapshot:
    display_name: str
    state: str
    bubble_text: str
    spritesheet_path: Path


@dataclass(frozen=True)
class ParsedTUIInput:
    message: str = ""
    attachments: tuple[UserAttachment, ...] = ()
    warnings: tuple[str, ...] = ()


class SelectableRichLog(RichLog):
    """RichLog variant that exposes rendered lines to Textual text selection."""

    ALLOW_SELECT = True

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        text = _rich_log_selection_text(self.lines)
        if not text:
            return None
        return selection.extract(text), "\n"


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


@dataclass(frozen=True)
class PendingProviderKeySetup:
    provider: str


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
    SlashCommand("/status", "/status", "Show model, context, token, cost, and daemon status"),
    SlashCommand("/update", "/update [--dry-run]", "Safely update Libre Claw from origin/main"),
    SlashCommand("/clear", "/clear", "Clear transcript and session history"),
    SlashCommand("/new", "/new", "Start a fresh TUI session"),
    SlashCommand("/restart", "/restart", "Start a fresh TUI session"),
    SlashCommand("/cancel", "/cancel", "Cancel active generation or tool execution"),
    SlashCommand("/stop", "/stop [run_id]", "Stop the current turn without exiting Libre Claw"),
    SlashCommand("/btw", "/btw <note>", "Add a side note for future turns"),
    SlashCommand("/steer", "/steer <instruction>", "Steer future agent turns"),
    SlashCommand("/attach", "/attach <image-path>|list|clear", "Attach images to the next TUI message"),
    SlashCommand("/paste-image", "/paste-image", "Attach an image from the OS clipboard"),
    SlashCommand("/cost", "/cost", "Show token and cost summary"),
    SlashCommand("/usage", "/usage openrouter|attribution|presets", "Show provider usage analytics"),
    SlashCommand("/model", "/model [provider:]<name>|list [--global]", "Choose or persist models"),
    SlashCommand("/models", "/models", "Show model presets"),
    SlashCommand("/fallback", "/fallback list|set|clear", "Manage fallback provider/model slots"),
    SlashCommand("/theme", "/theme list|<name> [--global]", "Switch or persist the TUI/dashboard theme"),
    SlashCommand(
        "/provider",
        "/provider anthropic|openai|openrouter|moonshot|ollama|codex",
        "Switch provider for new turns",
    ),
    SlashCommand(
        "/setup",
        "/setup status|provider|key|model|openrouter|moonshot|ollama-cloud|codex",
        "First-run provider and key setup",
    ),
    SlashCommand("/codex", "/codex login|status|logout|use [model]", "Manage Codex/ChatGPT login"),
    SlashCommand("/save", "/save [name]", "Save the current session"),
    SlashCommand("/load", "/load <name>", "Load a saved session"),
    SlashCommand("/compact", "/compact [status|--force] [--keep N]", "Show or compact context"),
    SlashCommand("/goal", "/goal <objective>|status|stop|max N", "Run a judged multi-turn goal loop"),
    SlashCommand("/runs", "/runs [N]", "List durable agent runs"),
    SlashCommand("/run", "/run <id>", "Inspect a durable run"),
    SlashCommand("/resume", "/resume <id>", "Load a durable run transcript"),
    SlashCommand("/artifacts", "/artifacts [plan|summary|verify|diff] [id]", "Open the run artifact panel"),
    SlashCommand("/review", "/review [latest|previous|next|close]", "Review file edits in a focused diff drawer"),
    SlashCommand("/approvals", "/approvals", "Show blocked tool approvals"),
    SlashCommand("/changes", "/changes [id]", "Show what changed since your last review"),
    SlashCommand("/skills", "/skills list|add|edit|delete", "Manage Libre Claw skills"),
    SlashCommand("/soul", "/soul status|show|init|reload", "Manage SOUL.md persona injection"),
    SlashCommand("/schedule", "/schedule list|add|pause|resume|delete|examples", "Manage recurring local runs"),
    SlashCommand("/heartbeat", "/heartbeat status|once|start [every 30 minutes]|stop", "Run recurring check-ins"),
    SlashCommand("/memory", "/memory status|list|search|add|forget|summarize", "Manage persistent memory"),
    SlashCommand("/workspace", "/workspace status|init|use <path>", "Manage the Libre Claw runtime workspace"),
    SlashCommand("/daemon", "/daemon", "Show daemon connection health"),
    SlashCommand("/telegram", "/telegram", "Show Telegram bridge status"),
    SlashCommand("/petdex", "/petdex status|state <name> [message]", "Inspect or update the Petdex companion"),
    SlashCommand("/tools", "/tools list|expand|collapse|toggle <index>", "Inspect and control tool details"),
    SlashCommand("/exit", "/exit", "Exit Libre Claw"),
)
SLASH_COMMAND_NAMES = frozenset(command.name for command in SLASH_COMMANDS)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "openrouter", "moonshot", "ollama", "codex")
MODEL_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (
        *((preset.model, preset.label) for preset in ANTHROPIC_MODEL_PRESETS),
    ),
    "openai": (
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1", "GPT-4.1"),
        ("o3", "o3 reasoning"),
        ("o4-mini", "o4-mini reasoning"),
        ("codex-mini", "Codex Mini"),
    ),
    "codex": (
        *((preset.model, f"{preset.label} through Codex CLI auth") for preset in CODEX_MODEL_PRESETS),
    ),
    "openrouter": (
        *((preset.model, f"{preset.label} through OpenRouter") for preset in OPENROUTER_MODEL_PRESETS),
    ),
    "moonshot": (
        *((preset.model, f"{preset.label} through Moonshot AI") for preset in MOONSHOT_MODEL_PRESETS),
    ),
    "ollama": (
        *((preset.model, preset.label) for preset in OLLAMA_MODEL_PRESETS),
    ),
}


PERMISSION_KEYS: dict[str, PermissionResolution] = {
    "y": "allow_once",
    "n": "deny",
    "a": "always_allow_tool",
    "!": "always_allow_call",
    "exclamation_mark": "always_allow_call",
}

ASSISTANT_ACCENT = "#FF5C5C"
PROJECT_NOTICE = "Apache-2.0 | Kroonen AI | hello@kroonen.ai"
PROJECT_LINKS = "Website: https://libreclaw.sh | GitHub: https://github.com/kroonen-ai/libre-claw"
LOBSTER_CODE_BACKGROUND = "#0b1020"
LOBSTER_CODE_TEXT = "#f8fafc"
LOBSTER_CODE_MUTED = "#9ca3af"
LOBSTER_CODE_ORANGE = "#f59e0b"
LOBSTER_CODE_CYAN = "#14b8a6"
LOBSTER_CODE_RED = "#ff5c5c"
LOBSTER_CODE_GREEN = "#22c55e"
LOBSTER_CODE_PURPLE = "#a78bfa"
LOBSTER_DIFF_ADDED_BACKGROUND = "#14372f"
LOBSTER_DIFF_REMOVED_BACKGROUND = "#3c171c"
LOBSTER_LIGHT_CODE_BACKGROUND = "#fffaf0"
LOBSTER_LIGHT_CODE_TEXT = "#073642"
LOBSTER_LIGHT_CODE_MUTED = "#657b83"
LOBSTER_LIGHT_CODE_ORANGE = "#b58900"
LOBSTER_LIGHT_CODE_CYAN = "#2aa198"
LOBSTER_LIGHT_CODE_RED = "#ff5c5c"
LOBSTER_LIGHT_CODE_GREEN = "#859900"
LOBSTER_LIGHT_CODE_PURPLE = "#6c71c4"
LOBSTER_LIGHT_DIFF_ADDED_BACKGROUND = "#eef3d2"
LOBSTER_LIGHT_DIFF_REMOVED_BACKGROUND = "#f8ddcf"
STREAM_RENDER_INTERVAL = 0.05
STREAM_RENDER_MAX_BUFFERED_CHARS = 240
RUN_ARTIFACT_TIMEOUT = 10.0
RUN_DIFF_MAX_CHARS = 750_000
RUN_STATUS_MAX_CHARS = 50_000
RUN_ARTIFACT_STDERR_MAX_CHARS = 20_000
TUI_IMAGE_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
TUI_IMAGE_ATTACHMENT_PROMPT = "Please inspect the attached image."
TUI_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TUI_CLIPBOARD_IMAGE_DIR = Path.home() / ".libre-claw" / "tui" / "uploads"


class LobsterSyntaxTheme(SyntaxTheme):
    """Rich syntax theme matching Libre Claw's Lobster website code blocks."""

    def __init__(
        self,
        *,
        background: str,
        text: str,
        muted: str,
        orange: str,
        cyan: str,
        red: str,
        green: str,
        purple: str,
        diff_added_background: str,
        diff_removed_background: str,
    ) -> None:
        base = Style(color=text, bgcolor=background)
        muted_style = Style(color=muted, bgcolor=background, italic=True)
        orange_style = Style(color=orange, bgcolor=background)
        cyan_style = Style(color=cyan, bgcolor=background)
        red_style = Style(color=red, bgcolor=background)
        green_style = Style(color=green, bgcolor=background)
        purple_style = Style(color=purple, bgcolor=background)

        self.background_color = background
        self._background_style = Style(bgcolor=background)
        self._missing_style = base
        self._style_cache: dict[object, Style] = {}
        self._styles: dict[object, Style] = {
            PygmentsToken.Text: base,
            PygmentsToken.Whitespace: base,
            PygmentsToken.Comment: muted_style,
            PygmentsToken.Keyword: orange_style,
            PygmentsToken.Name.Tag: orange_style,
            PygmentsToken.Name.Attribute: orange_style,
            PygmentsToken.Name.Function: orange_style,
            PygmentsToken.Name.Class: purple_style,
            PygmentsToken.Name.Decorator: purple_style,
            PygmentsToken.Name.Variable: cyan_style,
            PygmentsToken.Literal.String: cyan_style,
            PygmentsToken.Literal.Number: purple_style,
            PygmentsToken.Operator: red_style,
            PygmentsToken.Punctuation: Style(color=muted, bgcolor=background),
            PygmentsToken.Generic.Heading: Style(color=orange, bgcolor=background, bold=True),
            PygmentsToken.Generic.Subheading: Style(color=purple, bgcolor=background, bold=True),
            PygmentsToken.Generic.Inserted: Style(color=green, bgcolor=diff_added_background),
            PygmentsToken.Generic.Deleted: Style(color=red, bgcolor=diff_removed_background),
            PygmentsToken.Generic.Error: red_style,
            PygmentsToken.Generic.Prompt: Style(color=muted, bgcolor=background, bold=True),
            PygmentsToken.Generic.Output: base,
            PygmentsToken.Generic.Traceback: red_style,
        }

    def get_style_for_token(self, token_type: object) -> Style:
        cached = self._style_cache.get(token_type)
        if cached is not None:
            return cached

        token = tuple(cast(Any, token_type))
        style = self._missing_style
        while token:
            found = self._styles.get(token)
            if found is not None:
                style = found
                break
            token = token[:-1]
        self._style_cache[token_type] = style
        return style

    def get_background_style(self) -> Style:
        return self._background_style


_LOBSTER_SYNTAX_THEME = LobsterSyntaxTheme(
    background=LOBSTER_CODE_BACKGROUND,
    text=LOBSTER_CODE_TEXT,
    muted=LOBSTER_CODE_MUTED,
    orange=LOBSTER_CODE_ORANGE,
    cyan=LOBSTER_CODE_CYAN,
    red=LOBSTER_CODE_RED,
    green=LOBSTER_CODE_GREEN,
    purple=LOBSTER_CODE_PURPLE,
    diff_added_background=LOBSTER_DIFF_ADDED_BACKGROUND,
    diff_removed_background=LOBSTER_DIFF_REMOVED_BACKGROUND,
)
_LOBSTER_LIGHT_SYNTAX_THEME = LobsterSyntaxTheme(
    background=LOBSTER_LIGHT_CODE_BACKGROUND,
    text=LOBSTER_LIGHT_CODE_TEXT,
    muted=LOBSTER_LIGHT_CODE_MUTED,
    orange=LOBSTER_LIGHT_CODE_ORANGE,
    cyan=LOBSTER_LIGHT_CODE_CYAN,
    red=LOBSTER_LIGHT_CODE_RED,
    green=LOBSTER_LIGHT_CODE_GREEN,
    purple=LOBSTER_LIGHT_CODE_PURPLE,
    diff_added_background=LOBSTER_LIGHT_DIFF_ADDED_BACKGROUND,
    diff_removed_background=LOBSTER_LIGHT_DIFF_REMOVED_BACKGROUND,
)


def _lobster_code_theme(*, light: bool = False) -> LobsterSyntaxTheme:
    return _LOBSTER_LIGHT_SYNTAX_THEME if light else _LOBSTER_SYNTAX_THEME


def _lobster_markdown(markup: str, *, light: bool = False) -> Markdown:
    code_theme = _lobster_code_theme(light=light)
    return Markdown(
        markup,
        code_theme=code_theme,  # type: ignore[arg-type]
        inline_code_lexer="text",
        inline_code_theme=code_theme,  # type: ignore[arg-type]
    )


def _lobster_syntax(code: str, lexer: str, *, light: bool = False, word_wrap: bool = True) -> Syntax:
    code_theme = _lobster_code_theme(light=light)
    return Syntax(
        code,
        lexer,
        theme=code_theme,
        word_wrap=word_wrap,
        padding=1,
        background_color=code_theme.background_color,
    )


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
        background: #0b1020;
        color: #e4e4e7;
        scrollbar-color: #FF5C5C;
        scrollbar-color-hover: #FF5C5C;
        scrollbar-color-active: #FF5C5C;
        scrollbar-background: #0b1020;
        scrollbar-background-hover: #0b1020;
        scrollbar-background-active: #0b1020;
        scrollbar-corner-color: #0b1020;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    Screen.light {
        background: #f7f7f2;
        color: #111827;
        scrollbar-color: #FF5C5C;
        scrollbar-color-hover: #FF5C5C;
        scrollbar-color-active: #FF5C5C;
        scrollbar-background: #f7f7f2;
        scrollbar-background-hover: #f7f7f2;
        scrollbar-background-active: #f7f7f2;
        scrollbar-corner-color: #f7f7f2;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    #status {
        height: 1;
        background: #111827;
        color: #f2f5f8;
        padding: 0 1;
    }

    Screen.light #status {
        background: #d9e2ec;
        color: #0b1020;
    }

    #workspace {
        height: 1fr;
        border: none;
        border-top: solid #FF5C5C;
        border-bottom: solid #FF5C5C;
        background: #111827;
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
        background: #0b1020;
    }

    #sidebar-rail {
        width: 8;
        height: 1fr;
        background: #0b1020;
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
        background: #0b1020;
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
        background: #111827;
    }

    #workspace-bar {
        height: 2;
        padding: 0 2;
        content-align: left middle;
        border-bottom: solid #FF5C5C;
        background: #1f2937;
        color: #a1a1aa;
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
        border: solid #FF5C5C;
        background: #111827;
        color: #dbeafe;
    }

    #suggestions.hidden {
        display: none;
    }

    #petdex-panel {
        height: auto;
        max-height: 14;
        padding: 1 2;
        border-bottom: solid #FF5C5C;
        background: #111827;
        color: #e4e4e7;
    }

    #petdex-panel.hidden {
        display: none;
    }

    .reviewing-changes #petdex-panel {
        display: none;
    }

    Screen.light #petdex-panel {
        background: #ffffff;
        color: #111827;
    }

    #chat {
        height: 1fr;
        padding: 1 2;
        border: none;
        background: #111827;
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
        max-height: 22;
        padding: 1 2;
        border-top: solid #FF5C5C;
        background: #111827;
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

    #permission-preview {
        height: auto;
        max-height: 10;
        margin-top: 1;
        padding: 0 1;
        border-left: solid #FF5C5C;
        background: #0b1020;
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
        color: #0b1020;
    }

    Screen.light #permission-title {
        color: #0b1020;
    }

    Screen.light #permission-preview {
        background: #fffaf0;
    }

    #change-panel {
        height: 40%;
        min-height: 10;
        max-height: 19;
        border-top: solid #FF5C5C;
        background: #0b1020;
        padding: 0 1;
    }

    #change-panel.hidden {
        display: none;
    }

    #change-toolbar {
        height: 3;
        padding-top: 1;
    }

    #change-title {
        width: 1fr;
        height: 2;
        content-align: left middle;
        color: #fecaca;
        text-style: bold;
    }

    #change-toolbar Button {
        min-width: 8;
        margin-left: 1;
    }

    #change-content {
        height: 1fr;
        border: none;
        background: #0b1020;
    }

    Screen.light #change-panel,
    Screen.light #change-content {
        background: #fffaf0;
        color: #0b1020;
    }

    #artifact-panel {
        height: 16;
        border-top: solid #FF5C5C;
        background: #0b1020;
        padding: 0 1;
    }

    #artifact-panel.hidden {
        display: none;
    }

    #artifact-tabs {
        height: 3;
        padding-top: 1;
    }

    #artifact-tabs Button {
        margin-right: 1;
        min-width: 10;
    }

    #artifact-title {
        height: 1;
        color: #dbeafe;
        text-style: bold;
    }

    #artifact-content {
        height: 1fr;
        background: #0b1020;
        border: none;
    }

    Screen.light #artifact-panel,
    Screen.light #artifact-content {
        background: #f8fbff;
        color: #0b1020;
    }

    #composer {
        height: 4;
        border-top: solid #FF5C5C;
        background: #1f2937;
    }

    #composer-meta {
        height: 1;
        padding: 0 2;
        color: #a1a1aa;
    }

    #workspace,
    #sidebar-rail,
    #sidebar,
    #file-tree,
    #main,
    #workspace-bar,
    #palette,
    #suggestions,
    #permission-panel,
    #permission-preview,
    #change-panel,
    #change-content,
    #artifact-panel,
    #artifact-content,
    #chat,
    #petdex-panel,
    #composer,
    #input {
        scrollbar-color: #FF5C5C;
        scrollbar-color-hover: #FF5C5C;
        scrollbar-color-active: #FF5C5C;
        scrollbar-background: #0b1020;
        scrollbar-background-hover: #0b1020;
        scrollbar-background-active: #0b1020;
        scrollbar-corner-color: #0b1020;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    Screen.light #workspace,
    Screen.light #sidebar-rail,
    Screen.light #sidebar,
    Screen.light #file-tree,
    Screen.light #main,
    Screen.light #workspace-bar,
    Screen.light #palette,
    Screen.light #suggestions,
    Screen.light #permission-panel,
    Screen.light #permission-preview,
    Screen.light #change-panel,
    Screen.light #change-content,
    Screen.light #artifact-panel,
    Screen.light #artifact-content,
    Screen.light #chat,
    Screen.light #petdex-panel,
    Screen.light #composer,
    Screen.light #input {
        scrollbar-color: #FF5C5C;
        scrollbar-color-hover: #FF5C5C;
        scrollbar-color-active: #FF5C5C;
        scrollbar-background: #f7f7f2;
        scrollbar-background-hover: #f7f7f2;
        scrollbar-background-active: #f7f7f2;
        scrollbar-corner-color: #f7f7f2;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }

    #chat {
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }

    #input {
        height: 3;
        border: none;
        background: #1f2937;
        padding: 0 1;
    }

    Screen.light #input {
        background: #ffffff;
    }

    Screen.light #workspace-bar,
    Screen.light #composer {
        background: #eee8d5;
        color: #657b83;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Exit", show=True),
        Binding("escape", "interrupt", "Interrupt", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Files", show=True),
        Binding("ctrl+p", "command_palette", "Palette", show=True),
        Binding("ctrl+e", "toggle_change_review", "Review Edits", show=True),
        Binding("ctrl+r", "toggle_release_notes", "Release Notes", show=True),
        Binding("pageup", "scroll_chat_up", "Scroll Up", show=True, priority=True),
        Binding("pagedown", "scroll_chat_down", "Scroll Down", show=True, priority=True),
        Binding("ctrl+home", "scroll_chat_top", "Top", show=False, priority=True),
        Binding("ctrl+end", "scroll_chat_bottom", "Bottom", show=False, priority=True),
        Binding("ctrl+shift+c", "copy_last_response", "Copy Last", show=True),
        Binding("tab", "accept_suggestion", "Complete", show=False),
    ]

    def __init__(self, config: LibreClawConfig | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        if _canonical_tui_provider(self.config.general.default_provider) == "moonshot":
            self.config = apply_moonshot_model_limits(self.config)
        self._theme = tui_theme_palette(self.config.general.theme)
        self.session = Session()
        self.memory_store = MemoryStore()
        self.skill_store = SkillStore(self.config.general.working_directory, skills_config=self.config.skills)
        self.soul_store = SoulStore(self.config.general.working_directory)
        self.run_store = RunStore()
        self.automation_store = AutomationStore(self.config.automations.root)
        self.petdex_client = PetdexClient(self.config.petdex)
        self.daemon_client = DaemonClient(daemon_base_url(self.config)) if self.config.tui.use_daemon else None
        self.memory_facts: list[str] = []
        self.memory_enabled = self.config.memory.enabled
        self.session_archive_id = new_session_archive_id("tui")
        self.agent: Agent | None = None
        self.provider_error: str | None = None
        self.usage = Usage()
        self.transcript: list[TranscriptEntry] = []
        self.sidebar_visible = False
        self.startup_expanded = False
        self._startup_entry_index: int | None = None
        self.palette_open = False
        self._slash_suggestions: list[SlashCommand] = []
        self._slash_suggestion_index = 0
        self._palette_selected_index = 0
        self._active_task: asyncio.Task[None] | None = None
        self._pending_permission: AgentPermissionRequest | None = None
        self._pending_key_setup: PendingProviderKeySetup | None = None
        self._pending_daemon_permission_run_id: str | None = None
        self._tool_entry_by_call_id: dict[str, int] = {}
        self._chat_entry_spans: dict[int, tuple[int, int]] = {}
        self._started_at = time.monotonic()
        self._last_status_text: str | None = None
        self._last_assistant_response = ""
        self._pending_attachments: list[UserAttachment] = []
        self._goal_description: str | None = None
        self._goal_turn = 0
        self._goal_max_turns = self.config.goal.max_turns
        self._last_goal_decision: JudgeDecision | None = None
        self._active_run_id: str | None = None
        self._active_run_summary = ""
        self._daemon_poll_after = 0
        self._artifact_run_id: str | None = None
        self._artifact_tab: ArtifactTab = "summary"
        self._artifact_visible = False
        self._change_review_visible = False
        self._change_review_cursor = -1
        self._run_background_tasks: set[asyncio.Task[Any]] = set()
        self._memory_background_tasks: set[asyncio.Task[Any]] = set()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_interval_minutes = max(1, self.config.heartbeat.interval_minutes)
        self._global_model_config_path = global_config_path(self.config)
        self._global_model_config_mtime_ns = _path_mtime_ns(self._global_model_config_path)
        self._daemon_model_sync_task: asyncio.Task[None] | None = None

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
                yield Static("", id="workspace-bar")
                yield Static("", id="palette", classes="hidden")
                yield Static("", id="petdex-panel", classes="hidden")
                yield SelectableRichLog(id="chat", wrap=True, highlight=True, markup=True)
                with Vertical(id="permission-panel", classes="hidden"):
                    yield Static("", id="permission-title")
                    yield SelectableRichLog(id="permission-preview", wrap=False, highlight=True, markup=True)
                    yield Static("", id="permission-warning")
                    with Horizontal(id="permission-actions"):
                        yield Button("Allow once", id="permission-allow-once", variant="success", compact=True)
                        yield Button("Always Tool", id="permission-always-tool", compact=True)
                        yield Button("Always Exact", id="permission-always-call", compact=True)
                        yield Button("Deny", id="permission-deny", variant="error", compact=True)
                with Vertical(id="change-panel", classes="hidden"):
                    with Horizontal(id="change-toolbar"):
                        yield Static("", id="change-title")
                        yield Button("Prev", id="change-previous", compact=True)
                        yield Button("Next", id="change-next", compact=True)
                        yield Button("Close", id="change-close", compact=True)
                    yield SelectableRichLog(id="change-content", wrap=False, highlight=True, markup=True)
                with Vertical(id="artifact-panel", classes="hidden"):
                    with Horizontal(id="artifact-tabs"):
                        yield Button("Plan", id="artifact-plan", compact=True)
                        yield Button("Summary", id="artifact-summary", compact=True)
                        yield Button("Verify", id="artifact-verify", compact=True)
                        yield Button("Diff", id="artifact-diff", compact=True)
                        yield Button("Browser", id="artifact-browser", compact=True)
                        yield Button("Close", id="artifact-close", compact=True)
                    yield Static("", id="artifact-title")
                    yield SelectableRichLog(id="artifact-content", wrap=True, highlight=True, markup=True)
                yield Static("", id="suggestions", classes="hidden")
                with Vertical(id="composer"):
                    yield Static("", id="composer-meta")
                    yield Input(placeholder=self._input_placeholder(), id="input")

    async def on_mount(self) -> None:
        self.add_class(self._theme.theme_id)
        if self._theme.is_light:
            self.add_class("light")
        self._apply_tui_theme()
        input_widget = self.query_one("#input", Input)
        input_widget.cursor_blink = False
        input_widget.focus()
        self._sync_sidebar_visibility()
        self._update_shell_chrome()
        self._append_startup_entry()
        self._update_palette()
        self._update_slash_suggestions("")
        self.set_interval(1, self._update_status)
        self.set_interval(0.75, self._update_petdex_panel)
        self._update_petdex_panel()
        await self._initialize_memory()
        self._append_system(f"Libre Claw v{__version__} ready. Type /help for commands.")
        if self.daemon_client is not None:
            self._append_system(f"TUI daemon mode enabled: {daemon_base_url(self.config)}")
        if self.provider_error is not None:
            self._append_system(self.provider_error)
            self._append_system(_setup_first_run_hint())
        if self.config.heartbeat.enabled and self.config.heartbeat.route == "tui":
            self._start_tui_heartbeat(self._heartbeat_interval_minutes)

    async def on_unmount(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._daemon_model_sync_task is not None and not self._daemon_model_sync_task.done():
            self._daemon_model_sync_task.cancel()
        for task in self._memory_background_tasks:
            task.cancel()

    def _apply_tui_theme(self) -> None:
        palette = self._theme
        accent = Color.parse(palette.accent)
        text = Color.parse(palette.text)
        muted = Color.parse(palette.muted)
        background = Color.parse(palette.background)
        surface = Color.parse(palette.surface)
        surface_2 = Color.parse(palette.surface_2)
        panel = Color.parse(palette.panel)
        sidebar = Color.parse(palette.sidebar)
        status_bg = Color.parse(palette.status_bg)

        self.styles.background = background
        self.styles.color = text
        self.styles.scrollbar_color = accent
        self.styles.scrollbar_color_hover = accent
        self.styles.scrollbar_color_active = accent
        self.styles.scrollbar_background = background
        self.styles.scrollbar_background_hover = background
        self.styles.scrollbar_background_active = background
        self.styles.scrollbar_corner_color = background

        themed_nodes: tuple[tuple[str, Color, Color], ...] = (
            ("#status", status_bg, text),
            ("#workspace", surface, text),
            ("#sidebar-rail", sidebar, text),
            ("#sidebar", sidebar, text),
            ("#sidebar-root", sidebar, muted),
            ("#file-tree", sidebar, text),
            ("#main", surface, text),
            ("#workspace-bar", surface_2, muted),
            ("#chat", surface, text),
            ("#petdex-panel", surface, text),
            ("#composer", surface_2, text),
            ("#composer-meta", surface_2, muted),
            ("#input", surface_2, text),
            ("#suggestions", panel, text),
            ("#permission-panel", panel, text),
            ("#permission-preview", sidebar, text),
            ("#change-panel", sidebar, text),
            ("#change-content", sidebar, text),
            ("#artifact-panel", sidebar, text),
            ("#artifact-content", sidebar, text),
            ("#palette", panel, text),
        )
        for selector, bg, fg in themed_nodes:
            for widget in self.query(selector):
                widget.styles.background = bg
                widget.styles.color = fg
                widget.styles.scrollbar_color = accent
                widget.styles.scrollbar_color_hover = accent
                widget.styles.scrollbar_color_active = accent
                widget.styles.scrollbar_background = background
                widget.styles.scrollbar_background_hover = background
                widget.styles.scrollbar_background_active = background
                widget.styles.scrollbar_corner_color = background

        for selector in ("#workspace", "#composer", "#permission-panel", "#change-panel", "#artifact-panel", "#suggestions"):
            for widget in self.query(selector):
                widget.styles.border_top = ("solid", accent)
        for selector in ("#petdex-panel", "#workspace-bar"):
            for widget in self.query(selector):
                widget.styles.border_bottom = ("solid", accent)
        for widget in self.query("#permission-preview"):
            widget.styles.border_left = ("solid", accent)
        for widget in self.query("#workspace"):
            widget.styles.border_bottom = ("solid", accent)
        for selector in ("#suggestions", "#palette"):
            for widget in self.query(selector):
                widget.styles.border = ("solid", accent)

        self.query_one("#permission-warning", Static).styles.color = Color.parse(palette.warn)
        self.query_one("#artifact-title", Static).styles.color = Color.parse(palette.accent_strong)
        self.query_one("#change-title", Static).styles.color = Color.parse(palette.accent_strong)
        self._update_shell_chrome()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self._should_complete_on_submit(text):
            self._accept_selected_suggestion(event.input)
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
        if event.key == "up" and self._move_menu_selection(-1):
            event.stop()
            return
        if event.key == "down" and self._move_menu_selection(1):
            event.stop()
            return
        if self._scroll_chat_with_arrow_key(event):
            event.stop()
            return

        if self._pending_permission is None:
            return

        resolution = PERMISSION_KEYS.get(event.key) or PERMISSION_KEYS.get(event.character or "")
        if resolution is None:
            return

        event.stop()
        self._resolve_pending_permission(resolution)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._event_targets_chat(event):
            event.stop()
            self.query_one("#chat", RichLog).scroll_up(animate=False, immediate=True)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._event_targets_chat(event):
            event.stop()
            self.query_one("#chat", RichLog).scroll_down(animate=False, immediate=True)

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
        if button_id is not None and button_id.startswith("artifact-"):
            event.stop()
            self._handle_artifact_button(button_id)
            return
        if button_id is not None and button_id.startswith("change-"):
            event.stop()
            self._handle_change_button(button_id)
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

        if self._pending_key_setup is not None:
            await self._handle_pending_key_input(text)
            return

        if self._pending_permission is not None and not text.startswith("/"):
            self._handle_permission_input(text)
            return

        parsed: ParsedTUIInput | None = None
        if text.startswith("/"):
            command_name = text.split(maxsplit=1)[0].lower()
            if command_name not in SLASH_COMMAND_NAMES:
                parsed = _parse_tui_image_input(text, self.config.general.working_directory)
                if not parsed.attachments:
                    await self._handle_command(text)
                    return
            else:
                await self._handle_command(text)
                return

        if parsed is None:
            parsed = _parse_tui_image_input(text, self.config.general.working_directory)

        if self._active_task is not None and not self._active_task.done():
            self._append_system("A response is already streaming. Use /cancel to stop it.")
            return

        for warning in parsed.warnings:
            self._append_system(warning)
        attachments = tuple((*self._pending_attachments, *parsed.attachments))
        self._pending_attachments.clear()
        self._update_shell_chrome()
        user_message = parsed.message.strip() if attachments else text
        if attachments and not user_message:
            user_message = TUI_IMAGE_ATTACHMENT_PROMPT
        if attachments and _canonical_tui_provider(self.config.general.default_provider) == "codex":
            self._append_system("Codex CLI provider currently receives text only; switch to Anthropic, OpenAI, OpenRouter, or Ollama for image inputs.")

        self._append_user(user_message)
        for attachment in attachments:
            self._append_attachment(attachment)
        self._archive_session_event_later(
            "user_message",
            {"content": user_message, "attachments": [_attachment_metadata(attachment) for attachment in attachments]},
        )
        if self.daemon_client is not None:
            assistant_index = self._append_assistant("")
            self._active_task = asyncio.create_task(self._stream_daemon_response(user_message, assistant_index, attachments=attachments))
            return

        run = await self._start_run("chat", user_message)
        await self._record_run_event(
            "user_message",
            {"content": user_message, "attachments": [_attachment_metadata(attachment) for attachment in attachments]},
        )
        if self.agent is None:
            self._append_system(self.provider_error or "No provider is available.")
            await self._record_run_event("error", {"message": self.provider_error or "No provider is available."})
            await self._finish_active_run("failed", summary=self.provider_error or "No provider is available.")
            return

        assistant_index = self._append_assistant("")
        self._append_system(f"Run {run.run_id} started.")
        self._active_task = asyncio.create_task(self._stream_agent_response(user_message, assistant_index, attachments=attachments))

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/exit":
            self._cancel_active_generation(cancel_daemon_run=False)
            self.exit()
            return
        if command in {"/clear", "/new", "/restart"}:
            self._clear_transcript()
            return
        if command in {"/cancel", "/stop"}:
            if argument:
                await self._cancel_run_command(argument)
            else:
                self._cancel_active_generation()
            return
        if command == "/btw":
            self._handle_steering_note("btw", argument)
            return
        if command == "/steer":
            self._handle_steering_note("steer", argument)
            return
        if command == "/attach":
            self._handle_attach_command(argument)
            return
        if command == "/paste-image":
            self._handle_clipboard_image_command()
            return
        if command == "/help":
            self._append_system(self._help_text())
            return
        if command == "/status":
            self._append_system(await self._status_report_text())
            return
        if command == "/update":
            await self._handle_update_command(argument)
            return
        if command == "/cost":
            self._append_system(self._cost_text())
            return
        if command == "/usage":
            await self._handle_usage_command(argument)
            return
        if command == "/model":
            self._set_model(argument)
            return
        if command == "/models":
            self._append_system(_model_help_text(self.config))
            return
        if command == "/fallback":
            await self._handle_fallback_command(argument)
            return
        if command == "/theme":
            self._handle_theme_command(argument)
            return
        if command == "/provider":
            self._set_provider(argument)
            return
        if command == "/setup":
            await self._handle_setup_command(argument)
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
        if command == "/artifacts":
            await self._handle_artifacts_command(argument)
            return
        if command == "/review":
            self._handle_review_command(argument)
            return
        if command == "/approvals":
            await self._handle_approvals_command(argument)
            return
        if command == "/changes":
            await self._handle_changes_command(argument)
            return
        if command == "/skills":
            await self._handle_skills_command(argument)
            return
        if command == "/soul":
            self._handle_soul_command(argument)
            return
        if command == "/schedule":
            await self._handle_schedule_command(argument)
            return
        if command == "/heartbeat":
            await self._handle_heartbeat_command(argument)
            return
        if command == "/memory":
            await self._handle_memory_command(argument)
            return
        if command == "/workspace":
            await self._handle_workspace_command(argument)
            return
        if command == "/daemon":
            self._append_system(await self._daemon_status_text())
            return
        if command == "/telegram":
            self._append_system(self._telegram_status())
            return
        if command == "/petdex":
            await self._handle_petdex_command(argument)
            return
        if command == "/tools":
            self._handle_tools_command(argument)
            return

        self._append_system(f"Unknown command: {command}")

    async def _handle_update_command(self, argument: str) -> None:
        normalized = argument.strip().lower()
        if normalized not in {"", "--dry-run"}:
            self._append_system("Usage: /update [--dry-run]")
            return
        dry_run = normalized == "--dry-run"
        self._append_system("Checking origin/main for Libre Claw updates...")
        try:
            result = await asyncio.to_thread(
                update_checkout,
                libre_claw_checkout_path(),
                dry_run=dry_run,
            )
        except (UpdateError, OSError) as exc:
            self._append_system(f"Update failed: {exc}")
            return
        self._append_system(
            update_result_text(
                result,
                apply_command="/update",
                restart_hint="Exit and restart Libre Claw to load the updated code.",
            )
        )

    async def _handle_petdex_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=2)
        action = parts[0].lower() if parts else "status"
        if action in {"", "status"}:
            self._append_system(self.petdex_client.status_text())
            return
        if action == "state":
            if len(parts) < 2:
                self._append_system("Usage: /petdex state <idle|ready|running|working|success|error> [message]")
                return
            message = parts[2] if len(parts) > 2 else ""
            result = await self.petdex_client.send_state(parts[1], message=message, details={"surface": "tui", "manual": True})
            if result.ok:
                self._append_system(f"Petdex state sent: {parts[1].strip().lower()}")
                return
            prefix = "Petdex update skipped" if result.skipped else "Petdex update failed"
            self._append_system(f"{prefix}: {result.message}")
            return
        self._append_system("Usage: /petdex status|state <state> [message]")

    def _handle_attach_command(self, argument: str) -> None:
        normalized = argument.strip().lower()
        if normalized in {"", "list", "status"}:
            if not self._pending_attachments:
                self._append_system(
                    "No pending image attachments. Use `/attach <image-path>`, "
                    "`/attach paste`, or paste an image path in a message."
                )
                return
            lines = ["Pending image attachments for the next message:"]
            lines.extend(f"- {attachment.filename or attachment.path or attachment.media_type}" for attachment in self._pending_attachments)
            self._append_system("\n".join(lines))
            return
        if normalized in {"clear", "reset"}:
            count = len(self._pending_attachments)
            self._pending_attachments.clear()
            self._update_shell_chrome()
            self._append_system(f"Cleared {count} pending image attachment{'s' if count != 1 else ''}.")
            return
        if normalized in {"paste", "clipboard", "clip"}:
            self._handle_clipboard_image_command()
            return

        parsed = _parse_tui_image_input(argument, self.config.general.working_directory)
        for warning in parsed.warnings:
            self._append_system(warning)
        if not parsed.attachments:
            self._append_system("No image attachments found. Use a PNG, JPEG, WebP, or GIF path.")
            return
        self._pending_attachments.extend(parsed.attachments)
        self._update_shell_chrome()
        for attachment in parsed.attachments:
            self._append_attachment(attachment, pending=True)
        suffix = " They will be sent with your next message."
        self._append_system(f"Attached {len(parsed.attachments)} image{'s' if len(parsed.attachments) != 1 else ''}.{suffix}")
        if parsed.message:
            self._append_system(f"Ignored non-image text after /attach: {parsed.message}")

    def _handle_clipboard_image_command(self) -> None:
        attachment, warning = _load_tui_clipboard_image(TUI_CLIPBOARD_IMAGE_DIR)
        if attachment is None:
            self._append_system(warning or "Clipboard does not contain an attachable image.")
            return
        self._pending_attachments.append(attachment)
        self._update_shell_chrome()
        self._append_attachment(attachment, pending=True)
        self._append_system("Attached clipboard image. It will be sent with your next message.")

    def _handle_steering_note(self, kind: Literal["btw", "steer"], argument: str) -> None:
        note = argument.strip()
        if not note:
            self._append_system(f"Usage: /{kind} <note>")
            return
        label = "Side note" if kind == "btw" else "Steering instruction"
        self.session.summary = _append_session_note(self.session.summary, f"{label}: {note}")
        self._archive_session_event_later("steering_note", {"kind": kind, "content": note})
        self._append_system(f"{label} saved for future turns.")

    async def _handle_palette_input(self, query: str) -> None:
        matches = self._palette_matches(query)
        if not matches:
            self._append_system(f"No command palette match for: {query}")
            self._close_palette()
            return

        index = _bounded_menu_index(self._palette_selected_index, len(matches))
        slash = matches[index].usage.split()[0]
        self._close_palette()
        await self._handle_command(slash)

    def action_interrupt(self) -> None:
        if self.palette_open:
            self._close_palette()
            return
        if self._change_review_visible:
            self._hide_change_review()
            return
        if self._artifact_visible:
            self._hide_artifact_panel()
            return
        self._cancel_active_generation()

    def action_quit_app(self) -> None:
        if self._copy_selected_text_to_clipboard():
            return
        self._cancel_active_generation(quiet=True, cancel_daemon_run=False)
        self.exit()

    def action_toggle_sidebar(self) -> None:
        self.sidebar_visible = not self.sidebar_visible
        self._sync_sidebar_visibility()
        state = "shown" if self.sidebar_visible else "hidden"
        self._append_system(f"File tree {state}.")

    def action_toggle_change_review(self) -> None:
        if self._change_review_visible:
            self._hide_change_review()
            return
        self._show_change_review()

    def action_command_palette(self) -> None:
        self.palette_open = not self.palette_open
        self._update_palette(self.query_one("#input", Input).value)
        self._update_slash_suggestions("")
        input_widget = self.query_one("#input", Input)
        input_widget.placeholder = self._input_placeholder()
        input_widget.focus()

    def action_accept_suggestion(self) -> None:
        if self.palette_open:
            self._accept_selected_palette_command(self.query_one("#input", Input))
            return
        self._accept_selected_suggestion(self.query_one("#input", Input))

    def action_toggle_release_notes(self) -> None:
        self.startup_expanded = not self.startup_expanded
        self._refresh_startup_entry()

    def action_scroll_chat_up(self) -> None:
        self.query_one("#chat", RichLog).scroll_page_up(animate=False, immediate=True)

    def action_scroll_chat_down(self) -> None:
        self.query_one("#chat", RichLog).scroll_page_down(animate=False, immediate=True)

    def action_scroll_chat_top(self) -> None:
        self.query_one("#chat", RichLog).scroll_home(animate=False, immediate=True)

    def action_scroll_chat_bottom(self) -> None:
        self.query_one("#chat", RichLog).scroll_end(animate=False, immediate=True)

    def _scroll_chat_with_arrow_key(self, event: events.Key) -> bool:
        if self.palette_open or self._slash_suggestions or self._pending_permission is not None:
            return False
        if event.key not in {"up", "down"}:
            return False
        input_widget = self.query_one("#input", Input)
        if input_widget.value:
            return False
        chat = self.query_one("#chat", RichLog)
        if event.key == "up":
            chat.scroll_up(animate=False, immediate=True)
        else:
            chat.scroll_down(animate=False, immediate=True)
        return True

    def _event_targets_chat(self, event: events.MouseEvent) -> bool:
        widget = getattr(event, "widget", None)
        return widget is self.query_one("#chat", RichLog)

    def action_copy_last_response(self) -> None:
        if self._copy_selected_text_to_clipboard():
            return
        if not self._last_assistant_response:
            self._append_system("No assistant response to copy.")
            return
        self.copy_to_clipboard(self._last_assistant_response)
        self._append_system("Copied last assistant response to clipboard.")

    def _copy_selected_text_to_clipboard(self) -> bool:
        selected_text = self.screen.get_selected_text()
        if not selected_text:
            return False
        self.copy_to_clipboard(selected_text)
        self._append_system("Copied selected text to clipboard.")
        return True

    async def _stream_agent_response(
        self,
        user_message: str,
        assistant_index: int,
        *,
        attachments: Sequence[UserAttachment] = (),
    ) -> None:
        if self.agent is None:
            return

        stream_buffer = StreamRenderBuffer(
            interval=STREAM_RENDER_INTERVAL,
            max_buffered_chars=STREAM_RENDER_MAX_BUFFERED_CHARS,
        )
        run_state = "done"
        run_summary = ""

        try:
            async for event in self.agent.run(user_message, attachments=attachments):
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

    async def _stream_daemon_response(
        self,
        user_message: str,
        assistant_index: int,
        *,
        attachments: Sequence[UserAttachment] = (),
    ) -> None:
        if self.daemon_client is None:
            return

        try:
            started = await self.daemon_client.start_run(
                user_message,
                kind="chat",
                provider=_canonical_tui_provider(self.config.general.default_provider),
                model=_effective_model(self.config),
                surface="tui:daemon",
                session=session_to_payload(self.session),
                attachments=[attachment.as_payload() for attachment in attachments],
            )
            run = _object_payload(started.get("run"))
            run_id = str(run.get("run_id", ""))
            if not run_id:
                raise RuntimeError("Daemon did not return a run id.")
            self._active_run_id = run_id
            self._daemon_poll_after = 0
            self._append_system(f"Daemon run {run_id} started.")
            await self._poll_daemon_run(run_id, assistant_index)
        except asyncio.CancelledError:
            self._append_system("Daemon polling stopped. The daemon run can continue in the background.")
        except Exception as exc:
            if assistant_index < len(self.transcript) and not self.transcript[assistant_index].content:
                self.transcript.pop(assistant_index)
                self._render_transcript()
            self._append_system(f"Daemon run failed: {exc}")
        finally:
            self._pending_permission = None
            self._pending_daemon_permission_run_id = None
            self._hide_permission_prompt()
            self._active_task = None
            self.query_one("#input", Input).focus()

    async def _poll_daemon_run(self, run_id: str, assistant_index: int) -> None:
        if self.daemon_client is None:
            return

        while True:
            payload = await self.daemon_client.get_events(run_id, after=self._daemon_poll_after)
            events = payload.get("events", [])
            if isinstance(events, list):
                for raw_event in events:
                    if not isinstance(raw_event, dict):
                        continue
                    self._daemon_poll_after = max(self._daemon_poll_after, int(raw_event.get("event_id", 0) or 0))
                    self._handle_daemon_event(run_id, raw_event, assistant_index)

            detail = await self.daemon_client.get_run(run_id)
            run = _object_payload(detail.get("run"))
            state = str(run.get("state", ""))
            if state in {"done", "failed", "cancelled"}:
                self._active_run_id = None
                await self._refresh_artifact_panel_for_run_id(run_id)
                return
            await asyncio.sleep(max(0.1, self.config.daemon.poll_interval))

    def _handle_daemon_event(self, run_id: str, raw_event: dict[str, Any], assistant_index: int) -> None:
        data = _object_payload(raw_event.get("data"))
        event_type = str(raw_event.get("type", ""))
        if event_type in {"run_started", "user_message"}:
            return
        if event_type == "assistant_delta":
            text = str(data.get("text", ""))
            self._append_to_entry(assistant_index, text)
            self._last_assistant_response = self.transcript[assistant_index].content
            return
        if event_type == "tool_call":
            call = ToolCall(
                id=str(data.get("id", "")),
                name=str(data.get("name", "tool")),
                arguments=_object_payload(data.get("arguments")),
            )
            self._append_tool_call(call)
            self._archive_session_event_later("tool_call", {"run_id": run_id, "name": call.name, "arguments": dict(call.arguments)})
            return
        if event_type == "permission_request":
            call = ToolCall(
                id=str(data.get("tool_call_id", "")),
                name=str(data.get("name", "tool")),
                arguments=_object_payload(data.get("arguments")),
            )
            future: asyncio.Future[PermissionResolution] = asyncio.get_running_loop().create_future()
            self._pending_permission = AgentPermissionRequest(call=call, future=future)
            self._pending_daemon_permission_run_id = run_id
            self._show_permission_prompt(self._pending_permission)
            self._archive_session_event_later("permission_request", {"run_id": run_id, "name": call.name, "arguments": dict(call.arguments)})
            return
        if event_type == "tool_result":
            call = ToolCall(
                id=str(data.get("tool_call_id", "")),
                name=str(data.get("name", "tool")),
                arguments=_object_payload(data.get("arguments")),
            )
            result = ToolResult(
                content=str(data.get("content", "")) if not data.get("is_error") else "",
                error=str(data.get("content", "")) if data.get("is_error") else None,
                metadata=_object_payload(data.get("metadata")),
            )
            self._append_tool_result(call, result)
            self._archive_session_event_later(
                "tool_result",
                {"run_id": run_id, "name": call.name, "is_error": result.is_error, "content": result.as_text()},
            )
            return
        if event_type == "usage":
            usage = _usage_from_payload(data)
            self.usage = combine_usage(self.usage, usage) or self.usage
            self._update_status()
            return
        if event_type == "error":
            self._append_system(str(data.get("message", "Daemon run error.")))
            self._archive_session_event_later("error", {"run_id": run_id, "message": str(data.get("message", "Daemon run error."))})
            return
        if event_type == "provider_fallback":
            self._append_system(
                f"Provider fallback engaged: {data.get('provider', 'fallback')}\n"
                f"Reason: {data.get('reason', '')}"
            )
            return
        if event_type == "run_finished":
            self._append_system(f"Daemon run {run_id} finished: {data.get('state', 'done')}")
            if assistant_index < len(self.transcript):
                assistant_text = self.transcript[assistant_index].content
                if assistant_text:
                    self._archive_session_event_later("assistant_message", {"run_id": run_id, "content": assistant_text})
                    self._schedule_memory_extraction(run_id, "", assistant_text)
            self._archive_session_event_later("run_finished", {"run_id": run_id, "state": str(data.get("state", "done"))})

    async def _refresh_artifact_panel_for_run_id(self, run_id: str) -> None:
        if not self._artifact_visible or self._artifact_run_id != run_id:
            return
        run = await self.run_store.load_run(run_id)
        if run is not None:
            await self._refresh_artifact_panel(run)

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
                        self._record_run_event_later(
                            "usage",
                            _usage_payload(
                                event.usage,
                                provider=_canonical_tui_provider(self.config.general.default_provider),
                                model=_effective_model(self.config),
                                surface="tui:goal:judge",
                            ),
                        )
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
            self._petdex_state_later(
                "command" if event.call.name == "bash" else "working",
                message=f"Calling {event.call.name}",
                details=petdex_tool_details(event.call.name, event.call.arguments),
            )
            self._archive_session_event_later(
                "tool_call",
                {"run_id": self._active_run_id or "", "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            self._record_run_event_later(
                "tool_call",
                {"id": event.call.id, "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            return True, False

        if isinstance(event, AgentPermissionRequest):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._pending_permission = event
            self._show_permission_prompt(event)
            self._petdex_state_later(
                "waving",
                message=f"Approval needed: {event.call.name}",
                details=petdex_tool_details(event.call.name, event.call.arguments),
            )
            self._archive_session_event_later(
                "permission_request",
                {"run_id": self._active_run_id or "", "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            self._record_run_event_later(
                "permission_request",
                {"tool_call_id": event.call.id, "name": event.call.name, "arguments": dict(event.call.arguments)},
            )
            self._set_run_state_later("blocked")
            return True, False

        if isinstance(event, AgentToolResult):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._append_tool_result(event.call, event.result)
            self._petdex_state_later(
                "error" if event.result.is_error else "success",
                message=f"{event.call.name} {'failed' if event.result.is_error else 'finished'}",
                details={"tool": event.call.name, "is_error": event.result.is_error},
            )
            self._archive_session_event_later(
                "tool_result",
                {
                    "run_id": self._active_run_id or "",
                    "name": event.call.name,
                    "is_error": event.result.is_error,
                    "content": event.result.as_text(),
                },
            )
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
                self._record_run_event_later(
                    "usage",
                    _usage_payload(
                        event.usage,
                        provider=_canonical_tui_provider(self.config.general.default_provider),
                        model=_effective_model(self.config),
                        surface=self._current_user_surface(),
                    ),
                )
                self._update_status()
            return True, False

        if isinstance(event, AgentError):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._petdex_state_later("error", message=event.message, details={"surface": self._current_user_surface()})
            if assistant_index < len(self.transcript) and not self.transcript[assistant_index].content:
                self.transcript.pop(assistant_index)
                self._render_transcript()
            self._append_system(event.message)
            self._archive_session_event_later("error", {"run_id": self._active_run_id or "", "message": event.message})
            self._record_run_event_later("error", {"message": event.message})
            return True, stop_on_error

        if isinstance(event, AgentFallback):
            self._flush_stream_buffer(assistant_index, stream_buffer)
            self._petdex_state_later(
                "thinking",
                message=f"Fallback: {event.provider_label}",
                details={"provider": event.provider_label, "reason": event.reason},
            )
            self._append_system(f"Provider fallback engaged: {event.provider_label}\nReason: {event.reason}")
            self._archive_session_event_later(
                "provider_fallback",
                {"run_id": self._active_run_id or "", "provider": event.provider_label, "reason": event.reason},
            )
            self._record_run_event_later(
                "provider_fallback",
                {"provider": event.provider_label, "reason": event.reason},
            )
            return True, False

        return False, False

    def _flush_stream_buffer(self, assistant_index: int, stream_buffer: StreamRenderBuffer) -> None:
        text = stream_buffer.flush(time.monotonic())
        if not text or assistant_index >= len(self.transcript):
            return
        self._append_to_entry(assistant_index, text)
        self._last_assistant_response = self.transcript[assistant_index].content
        self._active_run_summary = self.transcript[assistant_index].content
        self._record_run_event_later("assistant_delta", {"text": text})

    def _current_user_surface(self) -> str:
        return "tui:goal" if self._goal_description is not None else "tui:chat"

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
        self._update_shell_chrome()
        await self.run_store.append_event(
            run.run_id,
            "run_started",
            {
                "kind": kind,
                "provider": run.provider,
                "model": run.model,
                "working_directory": run.working_directory,
                "surface": f"tui:{kind}",
            },
        )
        self._petdex_state_later(
            "running",
            message=petdex_message_preview(title),
            details={"surface": f"tui:{kind}", "run_id": run.run_id, "provider": run.provider, "model": run.model},
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

    def _petdex_state_later(
        self,
        state: str,
        *,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.config.petdex.notify_tui:
            return
        self._track_run_background_task(self.petdex_client.send_state(state, message=message, details=details))

    def _update_petdex_panel(self) -> None:
        try:
            panel = self.query_one("#petdex-panel", Static)
        except Exception:
            return

        renderable = _petdex_panel_renderable(
            enabled=self.config.petdex.enabled,
            accent=self._theme.accent,
            light=self._theme.is_light,
        )
        if renderable is None:
            panel.add_class("hidden")
            return
        panel.remove_class("hidden")
        panel.update(renderable)

    def _archive_session_event_later(self, event_type: str, data: dict[str, Any]) -> None:
        if not self.config.memory.archive_sessions:
            return
        task = asyncio.create_task(self.memory_store.append_session_event(self.session_archive_id, event_type, data))
        self._memory_background_tasks.add(task)
        task.add_done_callback(self._memory_background_tasks.discard)

    def _track_memory_background_task(self, awaitable: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(awaitable)
        self._memory_background_tasks.add(task)
        task.add_done_callback(self._memory_background_tasks.discard)

    def _schedule_memory_extraction(self, run_id: str, user_message: str, assistant_text: str) -> None:
        if not self._memory_enabled() or not assistant_text.strip():
            return
        self._track_memory_background_task(self._extract_run_memory(run_id, user_message, assistant_text))

    async def _extract_run_memory(self, run_id: str, user_message: str, assistant_text: str) -> None:
        if not self._memory_enabled():
            return
        prompt_text = user_message.strip()
        if not prompt_text:
            run = await self.run_store.load_run(run_id)
            prompt_text = run.title if run is not None else ""
        source_root = self.config.general.working_directory
        if self.config.memory.auto_summarize:
            summary = _memory_summary_text(prompt_text, assistant_text)
            if summary:
                try:
                    await self.memory_store.add_memory_item(
                        kind="summary",
                        scope="session",
                        text=summary,
                        source_type="run",
                        source_id=f"{run_id}:summary",
                        project_root=source_root,
                    )
                except Exception:
                    pass
        if not self.config.memory.auto_extract:
            return
        try:
            provider = create_provider(self.config)
            existing = [item.text for item in await self.memory_store.search_memory_items(prompt_text, project_root=source_root, limit=8)]
            extracted = await extract_memories_with_provider(
                provider,
                user_message=prompt_text,
                assistant_text=assistant_text,
                existing_memories=existing,
            )
            for index, memory in enumerate(extracted):
                await self.memory_store.add_memory_item(
                    kind=memory.kind,
                    scope=memory.scope,
                    text=memory.text,
                    source_type="run",
                    source_id=f"{run_id}:memory:{index}",
                    project_root=source_root if memory.scope == "project" else "",
                )
        except Exception:
            return

    async def _relevant_memory_texts(self, user_message: str) -> list[str]:
        if not self._memory_enabled() or not self.config.memory.inject_relevant:
            return []
        query = "\n".join(part for part in (user_message, self.session.summary or "") if part)
        items = await self.memory_store.search_memory_items(
            query,
            project_root=self.config.general.working_directory,
            limit=max(1, self.config.memory.max_injected_items),
        )
        return _memory_texts_with_budget(items, self.config.memory.max_injected_tokens)

    def _memory_enabled(self) -> bool:
        return self.memory_enabled and self.config.memory.enabled

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
        verification, diff, browser = await _collect_run_artifacts(working_directory, state, events)
        summary_text = summary or self._active_run_summary
        await self.run_store.finish_run(
            run_id,
            cast(RunState, state),
            plan=run_plan_text(events),
            summary=summary_text,
            verification=verification,
            diff=diff,
            browser=browser,
        )
        await self.run_store.append_event(run_id, "run_finished", {"state": state})
        if self.config.petdex.notify_tui:
            await self.petdex_client.send_state(
                _petdex_final_state(state),
                message=f"TUI run {state}",
                details={"surface": "tui", "run_id": run_id},
            )
        if summary_text:
            self._archive_session_event_later("assistant_message", {"run_id": run_id, "content": summary_text})
        self._archive_session_event_later("run_finished", {"run_id": run_id, "state": state})
        if state == "done" and summary_text:
            self._schedule_memory_extraction(run_id, run.title if run is not None else "", summary_text)
        self._active_run_id = None
        self._active_run_summary = ""
        self._update_shell_chrome()

    def _cancel_active_generation(self, quiet: bool = False, *, cancel_daemon_run: bool = True) -> None:
        if self._pending_permission is not None and not self._pending_permission.future.done():
            self._pending_permission.future.set_result("deny")
            self._pending_permission = None
            self._pending_daemon_permission_run_id = None
            self._hide_permission_prompt()
        if self._active_task is None or self._active_task.done():
            if not quiet:
                self._append_system("No active generation to cancel.")
            return
        if cancel_daemon_run and self.daemon_client is not None and self._active_run_id is not None:
            self._track_run_background_task(self.daemon_client.cancel_run(self._active_run_id))
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

    async def _handle_pending_key_input(self, text: str) -> None:
        pending = self._pending_key_setup
        if pending is None:
            return

        if text.strip().lower() in {"/cancel", "cancel"}:
            self._cancel_key_setup()
            self._append_system("Provider key setup cancelled.")
            return

        store = ApiKeyStore.from_config(self.config.auth)
        try:
            location = await asyncio.to_thread(store.set_api_key, pending.provider, text)
        except KeyStorageError as exc:
            self._append_system(f"Could not store {pending.provider} API key: {exc}")
            self._end_key_setup()
            return

        provider = pending.provider
        self._end_key_setup()
        self._rebuild_agent()
        self._update_status()
        suffix = " Provider is ready." if self.provider_error is None else f" {self.provider_error}"
        self._append_system(f"Stored {provider} API key in {location.replace('_', ' ')}.{suffix}")

    def _begin_key_setup(self, provider: str) -> None:
        self._pending_key_setup = PendingProviderKeySetup(provider=provider)
        input_widget = self.query_one("#input", Input)
        input_widget.password = True
        input_widget.placeholder = f"Paste {provider} API key. It will not be shown. Type /cancel to abort."
        input_widget.focus()

    def _end_key_setup(self) -> None:
        self._pending_key_setup = None
        input_widget = self.query_one("#input", Input)
        input_widget.password = False
        input_widget.placeholder = self._input_placeholder()

    def _cancel_key_setup(self) -> None:
        self._end_key_setup()

    def _show_permission_prompt(self, request: AgentPermissionRequest) -> None:
        panel = self.query_one("#permission-panel", Vertical)
        title = self.query_one("#permission-title", Static)
        preview = self.query_one("#permission-preview", RichLog)
        warning = self.query_one("#permission-warning", Static)
        allow_once = self.query_one("#permission-allow-once", Button)
        always_tool = self.query_one("#permission-always-tool", Button)
        always_call = self.query_one("#permission-always-call", Button)

        danger = self._dangerous_permission_warning(request.call)
        title.update(f"Review required · {request.call.name}")
        preview.clear()
        preview.write(self._permission_preview(request.call))
        warning.update(
            f"Warning: {danger}\nDangerous commands require one-time approval."
            if danger is not None
            else "Choose once, always for this tool, or always for this exact call."
        )
        always_tool.disabled = danger is not None
        always_call.disabled = danger is not None
        panel.remove_class("hidden")
        self.query_one("#input", Input).placeholder = self._input_placeholder()
        self._update_shell_chrome()
        allow_once.focus()

    def _hide_permission_prompt(self) -> None:
        panel = self.query_one("#permission-panel", Vertical)
        panel.add_class("hidden")
        self.query_one("#permission-title", Static).update("")
        self.query_one("#permission-preview", RichLog).clear()
        self.query_one("#permission-warning", Static).update("")
        for button_id in ("#permission-always-tool", "#permission-always-call"):
            self.query_one(button_id, Button).disabled = False
        self.query_one("#input", Input).placeholder = self._input_placeholder()
        self._update_shell_chrome()

    def _permission_preview(self, call: ToolCall) -> RenderableType:
        arguments = call.arguments
        path = self._display_tool_path(arguments.get("path"))
        if call.name == "edit_file":
            before = str(arguments.get("old_text", ""))
            after = str(arguments.get("new_text", ""))
            diff = self._diff_text(before, after, path or "file")
            return Group(
                Text(f"EDIT  {path or 'file'}", style=f"bold {self._theme.accent}"),
                _lobster_syntax(diff or "No textual change.", "diff", light=self._theme.is_light, word_wrap=False),
            )
        if call.name == "write_file":
            content = str(arguments.get("content", ""))
            return Group(
                Text(f"WRITE  {path or 'file'} · {len(content)} characters", style=f"bold {self._theme.accent}"),
                _lobster_syntax(content or "<empty file>", _lexer_for_path(path), light=self._theme.is_light, word_wrap=False),
            )
        if call.name == "bash":
            command = str(arguments.get("command", ""))
            return Group(
                Text("SHELL", style=f"bold {self._theme.accent}"),
                _lobster_syntax(command, "bash", light=self._theme.is_light, word_wrap=False),
            )
        return Group(
            Text(call.name.upper(), style=f"bold {self._theme.accent}"),
            _lobster_syntax(self._format_arguments(arguments), "json", light=self._theme.is_light, word_wrap=False),
        )

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
        daemon_run_id = self._pending_daemon_permission_run_id
        self._pending_permission = None
        self._pending_daemon_permission_run_id = None
        self._hide_permission_prompt()
        if daemon_run_id is not None and self.daemon_client is not None:
            self._track_run_background_task(
                self.daemon_client.resolve_permission(daemon_run_id, request.call.id, resolution)
            )
            self._append_system(f"Daemon permission response sent for {request.call.name}: {_permission_label(resolution)}")
            return
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
        self._hide_change_review()
        self._startup_entry_index = None
        self._append_startup_entry()
        self._append_system("Transcript cleared.")

    def _handle_theme_command(self, argument: str) -> None:
        selected, persist_global = _strip_global_flag(argument)
        selected = selected.strip().lower()
        if selected in {"", "status", "list", "help"}:
            self._append_system(_theme_help_text(self.config.general.theme))
            return

        theme_id = _resolve_theme_id(selected)
        if theme_id is None:
            self._append_system(f"Unknown theme: {selected}\n\n{_theme_help_text(self.config.general.theme)}")
            return

        self._set_tui_theme(theme_id)
        persisted_path: Path | None = None
        if persist_global:
            try:
                persisted_path = set_global_theme(theme_id, config_path=global_config_path(self.config))
                self._global_model_config_path = persisted_path
                self._global_model_config_mtime_ns = _path_mtime_ns(persisted_path)
            except ConfigError as exc:
                self._append_system(f"Theme set for this session, but global config was not updated: {exc}")
                return

        suffix = f"\nSaved as global default in {persisted_path}." if persisted_path is not None else ""
        self._append_system(f"Theme set to {THEME_PALETTES[theme_id].label}.{suffix}")

    async def _handle_fallback_command(self, argument: str) -> None:
        try:
            tokens = shlex.split(argument)
        except ValueError as exc:
            self._append_system(f"Could not parse fallback command: {exc}")
            return

        action = tokens.pop(0).lower() if tokens else "list"
        persist_global = "--global" in tokens
        tokens = [token for token in tokens if token != "--global"]

        if action in {"", "list", "status", "help"}:
            self._append_system(_fallback_help_text(self.config))
            return

        routes = list(self.config.fallback.routes)
        recheck_after_attempts = self.config.fallback.recheck_after_attempts

        if action == "set":
            if len(tokens) < 2:
                self._append_system("Usage: /fallback set 1|2|3 <provider>:<model> [--key-env ENV] [--global]")
                return
            slot = _parse_fallback_slot(tokens.pop(0))
            if slot is None:
                self._append_system("Fallback slot must be 1, 2, or 3.")
                return
            if slot > len(routes) + 1:
                self._append_system(f"Set fallback {len(routes) + 1} before setting fallback {slot}.")
                return
            parsed = _parse_model_argument(tokens.pop(0), self.config.general.default_provider)
            if parsed is None:
                self._append_system(
                    "Fallback route must look like moonshot:kimi-k3, "
                    "openrouter:openrouter/auto, or ollama:kimi-k2.6:cloud."
                )
                return
            api_key_env, parse_error = _parse_fallback_key_env(tokens)
            if parse_error is not None:
                self._append_system(parse_error)
                return
            provider, model = parsed
            route = FallbackRouteConfig(provider=provider, model=model, api_key_env=api_key_env or "")
            if slot <= len(routes):
                routes[slot - 1] = route
            else:
                routes.append(route)
        elif action == "clear":
            if not tokens or tokens[0].lower() in {"all", "*"}:
                routes = []
            else:
                slot = _parse_fallback_slot(tokens[0])
                if slot is None:
                    self._append_system("Usage: /fallback clear [1|2|3|all] [--global]")
                    return
                if slot > len(routes):
                    self._append_system(f"Fallback {slot} is already empty.")
                    return
                routes.pop(slot - 1)
        elif action in {"recheck", "retry-primary", "primary"}:
            if not tokens or not tokens[0].isdigit():
                self._append_system("Usage: /fallback recheck <provider-calls> [--global]")
                return
            recheck_after_attempts = max(1, int(tokens[0]))
        else:
            self._append_system(_fallback_help_text(self.config))
            return

        fallback = FallbackConfig(
            enabled=bool(routes),
            routes=tuple(routes[:3]),
            recheck_after_attempts=recheck_after_attempts,
        )
        self.config = replace(self.config, fallback=fallback)
        persisted_path: Path | None = None
        if persist_global:
            try:
                persisted_path = set_global_fallback_config(fallback, config_path=global_config_path(self.config))
                self._global_model_config_path = persisted_path
                self._global_model_config_mtime_ns = _path_mtime_ns(persisted_path)
            except ConfigError as exc:
                self._append_system(f"Fallback updated for this session, but global config was not updated: {exc}")

        daemon_note = await self._sync_daemon_fallback_after_selection(fallback, persist_global=persist_global)
        self._rebuild_agent()
        self._update_status()
        suffix = f"\nSaved in {persisted_path}." if persisted_path is not None else ""
        if daemon_note:
            suffix += f"\n{daemon_note}"
        self._append_system(_fallback_status_text(self.config) + suffix)

    def _set_tui_theme(self, theme_id: str) -> None:
        for known_theme in THEME_PALETTES:
            self.remove_class(known_theme)
        self.remove_class("light")

        self._theme = tui_theme_palette(theme_id)
        self.config = replace(self.config, general=replace(self.config.general, theme=self._theme.theme_id))
        self.add_class(self._theme.theme_id)
        if self._theme.is_light:
            self.add_class("light")
        self._apply_tui_theme()
        self._render_transcript()
        self._update_status()

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
        self.config = _replace_model_selection(self.config, provider, selected_model)
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
        if provider == "moonshot":
            self.config = apply_moonshot_model_limits(self.config)
        self._sync_daemon_model_after_selection(provider, selected_model, persist_global=persist_global)
        if persist_global and self.daemon_client is None:
            self._track_run_background_task(self._update_local_scheduled_models(provider, selected_model))
        if self.daemon_client is None and provider == "openrouter":
            self._track_run_background_task(self._refresh_openrouter_model_limits())
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

    def _sync_daemon_model_after_selection(self, provider: str, model: str, *, persist_global: bool = False) -> None:
        if self.daemon_client is None:
            return
        self._track_run_background_task(self._update_daemon_model_runtime(provider, model, persist_global=persist_global))

    async def _update_daemon_model_runtime(self, provider: str, model: str, *, persist_global: bool = False) -> None:
        if self.daemon_client is None:
            return
        try:
            payload = await self.daemon_client.update_model(provider, model, persist_global=persist_global)
        except Exception as exc:
            self._append_system(f"Daemon model was not updated: {exc}")
            return
        if persist_global:
            automations_updated = int(payload.get("automations_updated") or 0)
            if automations_updated:
                self._append_system(f"Updated {automations_updated} scheduled automation(s) to {provider}:{model}.")
        self._apply_daemon_model_payload(payload, announce_model_change=False)

    async def _sync_daemon_fallback_after_selection(
        self,
        fallback: FallbackConfig,
        *,
        persist_global: bool = False,
    ) -> str:
        if self.daemon_client is None:
            return ""
        try:
            await self.daemon_client.update_fallback(fallback, persist_global=persist_global)
        except Exception as exc:
            return f"Daemon fallback chain was not updated: {exc}"
        return "Daemon fallback chain updated for new daemon-backed runs."

    async def _refresh_openrouter_model_limits(self) -> None:
        provider = _canonical_tui_provider(self.config.general.default_provider)
        if provider == "moonshot":
            updated = apply_moonshot_model_limits(self.config)
            if not _runtime_model_metadata_changed(self.config, updated):
                return
            self.config = updated
            self._rebuild_agent()
            self._update_status()
            return
        if provider != "openrouter":
            return
        try:
            limits = await detect_openrouter_model_limits(self.config, model=self.config.general.default_model)
        except Exception as exc:
            self._append_system(f"OpenRouter model metadata unavailable; using configured context window. ({exc})")
            return
        updated = apply_openrouter_model_limits(self.config, limits, model=self.config.general.default_model)
        if not _runtime_model_metadata_changed(self.config, updated):
            return
        self.config = updated
        self._rebuild_agent()
        self._update_status()

    async def _update_local_scheduled_models(self, provider: str, model: str) -> None:
        try:
            automations_updated = await self.automation_store.update_global_model(provider, model)
        except AutomationError as exc:
            self._append_system(f"Scheduled automations were not updated: {exc}")
            return
        if automations_updated:
            self._append_system(f"Updated {automations_updated} scheduled automation(s) to {provider}:{model}.")

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
        if provider == "moonshot":
            self.config = apply_moonshot_model_limits(self.config)
        self._rebuild_agent()
        self._update_status()
        if self.provider_error:
            self._append_system(self.provider_error)
        else:
            self._append_system(f"Provider set to {provider}.")

    async def _handle_setup_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=2)
        action = parts[0].lower() if parts else "status"
        value = parts[1].strip() if len(parts) > 1 else ""
        rest = parts[2].strip() if len(parts) > 2 else ""

        if action in {"status", "check"}:
            self._append_system(await self._setup_status_text())
            return

        if action in {"help", ""}:
            self._append_system(_setup_help_text())
            return

        if action == "provider":
            if not value:
                self._append_system(_provider_help_text(self.config))
                return
            self._set_provider(value)
            return

        if action == "model":
            self._set_model(" ".join(part for part in (value, rest) if part))
            return

        if action == "key":
            provider = _canonical_tui_provider(value)
            if provider not in {"anthropic", "openai", "openrouter", "moonshot", "ollama"}:
                self._append_system(
                    "Usage: /setup key anthropic|openai|openrouter|moonshot|ollama"
                )
                return
            self._append_system(f"Ready for {provider} API key. Paste it into the input box; it will be hidden.")
            self._begin_key_setup(provider)
            return

        if action == "codex":
            await self._handle_codex_command("login")
            return

        if action == "openrouter":
            self._set_model("openrouter:qwen/qwen3.7-max --global")
            self._append_system("Next: run `/setup key openrouter` if the OpenRouter key is not stored yet.")
            return

        if action == "moonshot":
            self._set_model("moonshot:kimi-k3 --global")
            self._append_system(
                "Next: run `/setup key moonshot` if the Moonshot Platform key is not stored yet."
            )
            return

        if action == "ollama-cloud":
            self._set_model("ollama:kimi-k2.6:cloud --global")
            self._append_system(
                "Next: run `/setup key ollama` and configure [providers.ollama].base_url = "
                '"https://ollama.com". Exact Cloud API names can be checked with '
                "`curl https://ollama.com/api/tags -H 'Authorization: Bearer $OLLAMA_API_KEY'`."
            )
            return

        self._append_system(_setup_help_text())

    async def _setup_status_text(self) -> str:
        store = ApiKeyStore.from_config(self.config.auth)
        providers = [
            (name, _provider_api_key_env(provider_config))
            for name, provider_config in self.config.providers.items()
            if name in {"anthropic", "openai", "openrouter", "moonshot", "ollama"}
        ]
        try:
            statuses = await asyncio.to_thread(store.key_status, providers)
        except KeyStorageError as exc:
            statuses = {"error": str(exc)}
        codex = await codex_status()
        lines = [
            "Libre Claw setup status:",
            f"- Provider: {_canonical_tui_provider(self.config.general.default_provider)}",
            f"- Model: {_effective_model(self.config)}",
            f"- Working directory: {self.config.general.working_directory}",
            "- Keys:",
        ]
        lines.extend(f"  - {name}: {source}" for name, source in sorted(statuses.items()))
        lines.append(f"  - codex: {'logged_in' if codex.logged_in else 'missing'}")
        lines.extend(
            [
                "",
                "Next steps:",
                "- /setup provider openrouter",
                "- /setup key openrouter",
                "- /model openrouter:qwen/qwen3.7-max --global",
                "- /setup moonshot",
                "- /setup codex",
            ]
        )
        return "\n".join(lines)

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
        self._startup_entry_index = None
        self._append_startup_entry()
        self._update_status()
        self._rebuild_agent()
        self._append_system(f"Session loaded from {name}.")

    async def _handle_memory_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        value = parts[1].strip() if len(parts) > 1 else ""

        if action == "status":
            status = await self.memory_store.memory_status()
            self._append_system(
                "Memory status:\n"
                f"enabled: {self._memory_enabled()}\n"
                f"active items: {status['active']}\n"
                f"disabled items: {status['disabled']}\n"
                f"session archives: {status['session_archives']}"
            )
            return

        if action == "on":
            self.memory_enabled = True
            self._rebuild_agent()
            self._append_system("Persistent memory enabled for this TUI session.")
            return

        if action == "off":
            self.memory_enabled = False
            self._rebuild_agent()
            self._append_system("Persistent memory disabled for this TUI session.")
            return

        if action == "list":
            items = await self.memory_store.list_memory_items(limit=50)
            if not items:
                self._append_system("No active memories stored.")
                return
            self._append_system(_memory_items_text(items))
            return

        if action == "search":
            if not value:
                self._append_system("Usage: /memory search <query>")
                return
            items = await self.memory_store.search_memory_items(
                value,
                project_root=self.config.general.working_directory,
                limit=20,
            )
            self._append_system(_memory_items_text(items) if items else "No matching memories.")
            return

        if action == "add":
            if not value:
                self._append_system("Usage: /memory add <memory>")
                return
            fact = await self.memory_store.add_memory_item(
                text=value,
                kind="fact",
                scope="global",
                source_type="manual",
                project_root=self.config.general.working_directory,
            )
            await self._refresh_memory_facts()
            self._append_system(f"Added memory {fact.id}.")
            return

        if action == "forget":
            if not value.isdigit():
                self._append_system("Usage: /memory forget <id>")
                return
            removed = await self.memory_store.forget_memory_item(int(value))
            await self._refresh_memory_facts()
            self._append_system("Memory forgotten." if removed else f"No active memory with id {value}.")
            return

        if action == "summarize":
            summary = summarize_session_for_memory(self.session)
            if not summary:
                self._append_system("No session content to summarize into memory.")
                return
            item = await self.memory_store.add_memory_item(
                kind="summary",
                scope="session",
                text=summary,
                source_type="session",
                source_id=f"{self.session_archive_id}:summary",
                project_root=self.config.general.working_directory,
            )
            self._append_system(f"Session summary saved as memory {item.id}.")
            return

        if action == "import-runs":
            count = await self._import_run_memories()
            self._append_system(f"Imported {count} run summary memory item(s).")
            return

        self._append_system("Usage: /memory status|on|off|list|search <query>|add <text>|forget <id>|summarize|import-runs")

    async def _handle_workspace_command(self, argument: str) -> None:
        parts = shlex.split(argument) if argument.strip() else []
        action = parts[0].lower() if parts else "status"
        if action == "status":
            self._append_system(workspace_status_text(self.config.general.working_directory))
            return
        if action == "init":
            overwrite = "--overwrite" in parts
            target_text = next((part for part in parts[1:] if not part.startswith("-")), "")
            target = Path(target_text).expanduser() if target_text else default_claw_workspace_path()
            try:
                result = initialize_claw_workspace(
                    source_root=self.config.general.working_directory,
                    target=target,
                    set_default=True,
                    config_path=global_config_path(self.config),
                    overwrite=overwrite,
                )
            except ConfigError as exc:
                self._append_system(str(exc))
                return
            self._set_working_directory(result.path)
            self._append_system(workspace_result_text(result))
            return
        if action == "use" and len(parts) >= 2:
            path = Path(parts[1]).expanduser().resolve()
            if not path.is_dir():
                self._append_system(f"Workspace path does not exist: {path}")
                return
            try:
                config_path = set_global_working_directory(path, config_path=global_config_path(self.config))
            except ConfigError as exc:
                self._append_system(str(exc))
                return
            self._set_working_directory(path)
            self._append_system(f"Libre Claw workspace set to {path}.\nSaved in {config_path}.")
            return
        self._append_system("Usage: /workspace status|init [path] [--overwrite]|use <path>")

    async def _import_run_memories(self) -> int:
        count = 0
        runs = await self.run_store.list_runs(limit=200)
        for run in runs:
            summary_path = run.path / "summary.md"
            if not summary_path.exists():
                continue
            summary = await asyncio.to_thread(summary_path.read_text, encoding="utf-8")
            summary = redact_secrets(summary).strip()
            if not summary:
                continue
            await self.memory_store.add_memory_item(
                kind="summary",
                scope="project",
                text=_memory_summary_text(run.title, summary),
                source_type="run",
                source_id=f"{run.run_id}:summary",
                project_root=run.working_directory or self.config.general.working_directory,
            )
            count += 1
        return count

    async def _handle_skills_command(self, argument: str) -> None:
        try:
            parsed = _parse_skills_command(argument)
        except SkillError as exc:
            self._append_system(str(exc))
            return

        action = parsed["action"]
        if action == "list":
            skills = await self.skill_store.list_skills()
            self._append_system(_skills_list_text(skills))
            return

        if action == "sync":
            try:
                statuses = await self.skill_store.sync_external_sources(force=True)
            except SkillError as exc:
                self._append_system(str(exc))
                return
            self._append_system("External skill sources synced:\n" + "\n".join(f"- {status}" for status in statuses))
            return

        if action == "show":
            skills = await self.skill_store.list_skills()
            name = str(parsed.get("name", ""))
            scope = parsed.get("scope")
            matches = [
                skill
                for skill in skills
                if skill.name == name and (scope is None or skill.scope == scope)
            ]
            if not matches:
                self._append_system(f"No skill named {name}.")
                return
            if len(matches) > 1:
                self._append_system(
                    f"Skill name exists in multiple scopes; use `/skills show --bundled {name}`, "
                    f"`/skills show --external {name}`, `/skills show --user {name}`, "
                    f"or `/skills show --project {name}`."
                )
                return
            self._append_system(matches[0].prompt_text)
            return

        if action == "add":
            try:
                skill = await self.skill_store.add_skill(
                    str(parsed["name"]),
                    str(parsed.get("content", "")),
                    scope=cast(SkillScope, parsed["scope"]),
                )
            except SkillError as exc:
                self._append_system(str(exc))
                return
            self._append_system(f"Added {skill.scope} skill {skill.name}: {skill.path}")
            return

        if action == "edit":
            try:
                skill = await self.skill_store.edit_skill(
                    str(parsed["name"]),
                    str(parsed.get("content", "")),
                    scope=cast(SkillScope | None, parsed.get("scope")),
                )
            except SkillError as exc:
                self._append_system(str(exc))
                return
            self._append_system(f"Updated {skill.scope} skill {skill.name}: {skill.path}")
            return

        if action == "delete":
            try:
                removed = await self.skill_store.delete_skill(
                    str(parsed["name"]),
                    scope=cast(SkillScope | None, parsed.get("scope")),
                )
            except SkillError as exc:
                self._append_system(str(exc))
                return
            self._append_system("Skill deleted." if removed else f"No skill named {parsed['name']}.")
            return

        self._append_system(_skills_help_text())

    def _handle_soul_command(self, argument: str) -> None:
        tokens = argument.split()
        action = tokens[0].lower() if tokens else "status"
        if action in {"status", "paths"}:
            self._append_system(self.soul_store.status_text())
            return
        if action == "show":
            self._append_system(self.soul_store.combined_text())
            return
        if action == "reload":
            self._rebuild_agent()
            count = len(self.soul_store.load())
            self._append_system(f"Reloaded {count} soul file{'s' if count != 1 else ''}.")
            return
        if action == "init":
            scope = tokens[1] if len(tokens) > 1 else "--user"
            try:
                path = self.soul_store.ensure_template(scope)
            except SoulError as exc:
                self._append_system(str(exc))
                return
            self._rebuild_agent()
            self._append_system(f"Soul template ready: {path}")
            return
        self._append_system("Usage: /soul status|show|reload|init --user|--project|--root")

    async def _handle_schedule_command(self, argument: str) -> None:
        try:
            parsed = _parse_schedule_command(argument)
        except AutomationError as exc:
            self._append_system(str(exc))
            return

        action = str(parsed["action"])
        if action == "list":
            try:
                payloads = await self._list_schedule_payloads()
            except Exception as exc:
                self._append_system(f"Could not list schedules: {exc}")
                return
            self._append_system(_automation_list_text(payloads))
            return

        if action == "examples":
            self._append_system(_schedule_examples_text())
            return

        if action == "add":
            route = cast(AutomationRoute, parsed.get("route", "report"))
            payload = {
                "name": str(parsed["name"]),
                "prompt": str(parsed["prompt"]),
                "schedule": str(parsed["schedule"]),
                "route": route,
                "provider": _canonical_tui_provider(self.config.general.default_provider),
                "model": _effective_model(self.config),
            }
            try:
                created = await self._create_schedule_payload(payload)
            except Exception as exc:
                self._append_system(f"Could not create schedule: {exc}")
                return
            self._append_system("Scheduled:\n" + _automation_line(_object_payload(created.get("automation", created))))
            return

        automation_id = str(parsed.get("automation_id", ""))
        if action in {"pause", "resume", "delete"}:
            try:
                result = await self._mutate_schedule_payload(action, automation_id)
            except Exception as exc:
                self._append_system(f"Could not {action} schedule: {exc}")
                return
            if action == "delete":
                self._append_system(f"Deleted schedule {automation_id}.")
            else:
                self._append_system("Updated schedule:\n" + _automation_line(_object_payload(result.get("automation", result))))
            return

        self._append_system(_schedule_help_text())

    async def _handle_heartbeat_command(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        action = parts[0].lower() if parts else "status"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in {"status", ""}:
            active = self._heartbeat_task is not None and not self._heartbeat_task.done()
            self._append_system(
                "Heartbeat status:\n"
                f"- Active in this TUI: {active}\n"
                f"- Interval: every {self._heartbeat_interval_minutes} minutes\n"
                f"- Config route: {self.config.heartbeat.route}\n"
                f"- Config enabled: {self.config.heartbeat.enabled}\n"
                "- Use `/heartbeat once`, `/heartbeat start every 30 minutes`, or `/heartbeat stop`."
            )
            return

        if action in {"once", "run", "now"}:
            await self._run_tui_heartbeat_once()
            return

        if action in {"start", "on", "every"}:
            interval_text = rest if action != "every" else argument
            try:
                minutes = parse_heartbeat_interval(interval_text, self.config.heartbeat.interval_minutes)
            except HeartbeatError as exc:
                self._append_system(str(exc))
                return
            self._start_tui_heartbeat(minutes)
            self._append_system(f"Heartbeat started in this TUI: every {minutes} minutes.")
            return

        if action in {"stop", "off", "pause"}:
            if self._heartbeat_task is not None and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._heartbeat_task = None
            self._append_system("Heartbeat stopped for this TUI session.")
            return

        self._append_system("Usage: /heartbeat status|once|start [every 30 minutes|1h]|stop")

    def _start_tui_heartbeat(self, interval_minutes: int) -> None:
        self._heartbeat_interval_minutes = max(1, interval_minutes)
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._tui_heartbeat_loop(), name="libre-claw-tui-heartbeat")

    async def _tui_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval_minutes * 60)
                await self._run_tui_heartbeat_once()
        except asyncio.CancelledError:
            raise

    async def _run_tui_heartbeat_once(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._append_system("Heartbeat skipped: an agent response is already active.")
            return
        if self._pending_permission is not None:
            self._append_system("Heartbeat skipped: a permission prompt is waiting.")
            return
        prompt = heartbeat_prompt(self.config, surface="tui")
        self._append_system("Heartbeat check started.")
        await self.handle_user_input(prompt)

    async def _list_schedule_payloads(self) -> list[dict[str, Any]]:
        if self.daemon_client is not None:
            payload = await self.daemon_client.list_automations()
            automations = payload.get("automations", [])
            return [dict(item) for item in automations if isinstance(item, dict)]
        automations = await self.automation_store.list()
        return [_automation_record_payload(record) for record in automations]

    async def _create_schedule_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.daemon_client is not None:
            return await self.daemon_client.create_automation(**payload)
        record = await self.automation_store.create(
            name=str(payload["name"]),
            prompt=str(payload["prompt"]),
            schedule=str(payload["schedule"]),
            route=cast(AutomationRoute, payload.get("route", "report")),
            provider=str(payload.get("provider", "")),
            model=str(payload.get("model", "")),
            working_directory=self.config.general.working_directory,
            metadata={"created_by": "tui"},
        )
        return {"automation": _automation_record_payload(record)}

    async def _mutate_schedule_payload(self, action: str, automation_id: str) -> dict[str, Any]:
        if self.daemon_client is not None:
            if action == "pause":
                return await self.daemon_client.pause_automation(automation_id)
            if action == "resume":
                return await self.daemon_client.resume_automation(automation_id)
            if action == "delete":
                return await self.daemon_client.delete_automation(automation_id)
        if action == "pause":
            record = await self.automation_store.update_status(automation_id, "paused")
            if record is None:
                raise AutomationError("Unknown automation.")
            return {"automation": _automation_record_payload(record)}
        if action == "resume":
            record = await self.automation_store.update_status(automation_id, "active")
            if record is None:
                raise AutomationError("Unknown automation.")
            return {"automation": _automation_record_payload(record)}
        if action == "delete":
            if not await self.automation_store.delete(automation_id):
                raise AutomationError("Unknown automation.")
            return {"deleted": True}
        raise AutomationError(_schedule_help_text())

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
                "[goal].judge_provider must be 'current', 'anthropic', 'openai', "
                "'openrouter', 'moonshot', 'ollama', or 'codex'."
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
        await self._show_artifact_panel(run, "summary")

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
        last_seen = _read_last_seen_event_id(run)
        changes = run_changes_text(run, events, last_seen)
        self.transcript = _transcript_from_run_events(events)
        self._startup_entry_index = None
        self._append_startup_entry()
        self._tool_entry_by_call_id.clear()
        self._active_run_id = run.run_id if run.state in {"running", "blocked"} else None
        self._append_system(changes)
        _write_last_seen_event_id(run, _max_event_id(events))
        await self._show_artifact_panel(run, "summary")
        if self.daemon_client is not None and run.state in {"running", "blocked"}:
            self._daemon_poll_after = _max_event_id(events)
            assistant_index = _last_assistant_index(self.transcript)
            if assistant_index is None:
                assistant_index = self._append_assistant("")
            self._active_task = asyncio.create_task(self._poll_daemon_run(run.run_id, assistant_index))
            self._append_system(f"Polling active daemon run {run.run_id}.")
        self._append_system(f"Loaded run {run.run_id} ({run.state}) from {run.path}.")

    async def _handle_artifacts_command(self, argument: str) -> None:
        tab, run_query = _parse_artifact_command(argument)
        run_id = await self._resolve_optional_run_id(run_query)
        if run_id is None:
            self._append_system("Usage: /artifacts [plan|summary|verify|diff|browser] [run-id]")
            return
        run = await self.run_store.load_run(run_id)
        if run is None:
            self._append_system(f"No durable run found for: {run_id}")
            return
        await self._show_artifact_panel(run, tab)

    async def _handle_approvals_command(self, argument: str) -> None:
        del argument
        lines: list[str] = []
        if self._pending_permission is not None:
            call = self._pending_permission.call
            lines.append(
                f"active local prompt: {call.name} {call.id} {self._format_arguments(call.arguments)}"
            )

        for run in await self.run_store.list_runs(limit=100):
            if run.state != "blocked":
                continue
            for item in pending_approvals(run, await self.run_store.load_events(run.run_id)):
                lines.append(
                    f"{item.run_id} {item.tool_call_id} {item.tool_name} "
                    f"{json.dumps(item.arguments, sort_keys=True, default=str)}"
                )

        if not lines:
            self._append_system("No blocked approvals.")
            return
        self._append_system("Blocked approval inbox:\n" + "\n".join(lines))

    async def _handle_changes_command(self, argument: str) -> None:
        run_id = await self._resolve_optional_run_id(argument)
        if run_id is None:
            self._append_system("Usage: /changes [run-id]")
            return
        run = await self.run_store.load_run(run_id)
        if run is None:
            self._append_system(f"No durable run found for: {run_id}")
            return
        events = await self.run_store.load_events(run.run_id)
        last_seen = _read_last_seen_event_id(run)
        self._append_system(run_changes_text(run, events, last_seen))
        _write_last_seen_event_id(run, _max_event_id(events))

    async def _show_artifact_panel(self, run: RunRecord, tab: ArtifactTab) -> None:
        self._hide_change_review()
        self._artifact_run_id = run.run_id
        self._artifact_tab = tab
        self._artifact_visible = True
        panel = self.query_one("#artifact-panel", Vertical)
        panel.remove_class("hidden")
        await self._refresh_artifact_panel(run)

    async def _refresh_artifact_panel(self, run: RunRecord | None = None) -> None:
        run_id = self._artifact_run_id
        if not self._artifact_visible or run_id is None:
            return
        run = run or await self.run_store.load_run(run_id)
        if run is None:
            self._hide_artifact_panel()
            return

        title = self.query_one("#artifact-title", Static)
        content = self.query_one("#artifact-content", RichLog)
        text = await asyncio.to_thread(_read_artifact_text, run, self._artifact_tab)
        title.update(f"{run.run_id} [{run.state}] {self._artifact_tab}")
        content.clear()
        if self._artifact_tab == "diff":
            content.wrap = False
            content.write(_lobster_syntax(text or "No diff artifact.", "diff", light=self._theme.is_light))
        else:
            content.wrap = True
            content.write(_lobster_markdown(text or f"No {self._artifact_tab} artifact.", light=self._theme.is_light))

    def _handle_artifact_button(self, button_id: str) -> None:
        if button_id == "artifact-close":
            self._hide_artifact_panel()
            return
        tab = button_id.removeprefix("artifact-")
        if tab not in {"plan", "summary", "verify", "diff", "browser"}:
            return
        self._artifact_tab = cast(ArtifactTab, tab)
        if self._artifact_run_id is not None:
            asyncio.create_task(self._refresh_artifact_panel())

    def _hide_artifact_panel(self) -> None:
        self._artifact_visible = False
        self.query_one("#artifact-panel", Vertical).add_class("hidden")
        self.query_one("#artifact-title", Static).update("")
        self.query_one("#artifact-content", RichLog).clear()

    def _handle_review_command(self, argument: str) -> None:
        action = argument.strip().lower() or "latest"
        if action in {"latest", "open", "show"}:
            self._show_change_review()
            return
        if action in {"previous", "prev", "back"}:
            self._move_change_review(-1)
            return
        if action in {"next", "forward"}:
            self._move_change_review(1)
            return
        if action in {"close", "hide"}:
            self._hide_change_review()
            return
        self._append_system("Usage: /review [latest|previous|next|close]")

    def _handle_change_button(self, button_id: str) -> None:
        if button_id == "change-close":
            self._hide_change_review()
            return
        if button_id == "change-previous":
            self._move_change_review(-1)
            return
        if button_id == "change-next":
            self._move_change_review(1)

    def _change_entries(self) -> list[tuple[int, TranscriptEntry]]:
        return [
            (index, entry)
            for index, entry in enumerate(self.transcript)
            if entry.role == "tool" and (entry.metadata or {}).get("syntax") == "diff"
        ]

    def _show_change_review(self, transcript_index: int | None = None) -> None:
        changes = self._change_entries()
        if not changes:
            self._append_system("No file edits are available to review yet.")
            return

        self._hide_artifact_panel()
        if transcript_index is None:
            self._change_review_cursor = len(changes) - 1
        else:
            matching = next((position for position, item in enumerate(changes) if item[0] == transcript_index), None)
            self._change_review_cursor = matching if matching is not None else len(changes) - 1
        self._change_review_visible = True
        self.add_class("reviewing-changes")
        self.query_one("#change-panel", Vertical).remove_class("hidden")
        self._refresh_change_review()

    def _move_change_review(self, delta: int) -> None:
        changes = self._change_entries()
        if not changes:
            self._show_change_review()
            return
        if not self._change_review_visible:
            self._change_review_cursor = len(changes) - 1
            self._change_review_visible = True
            self.add_class("reviewing-changes")
            self.query_one("#change-panel", Vertical).remove_class("hidden")
        self._change_review_cursor = (self._change_review_cursor + delta) % len(changes)
        self._refresh_change_review()

    def _refresh_change_review(self) -> None:
        if not self._change_review_visible:
            return
        changes = self._change_entries()
        if not changes:
            self._hide_change_review()
            return

        self._change_review_cursor = max(0, min(self._change_review_cursor, len(changes) - 1))
        transcript_index, entry = changes[self._change_review_cursor]
        del transcript_index
        metadata = entry.metadata or {}
        title = entry.title or "File edit"
        position = f"{self._change_review_cursor + 1}/{len(changes)}"
        self.query_one("#change-title", Static).update(f"CHANGE {position} · {title}")
        content = self.query_one("#change-content", RichLog)
        content.clear()
        content.write(_lobster_syntax(entry.content or "No diff available.", "diff", light=self._theme.is_light, word_wrap=False))
        self.query_one("#change-previous", Button).disabled = len(changes) < 2
        self.query_one("#change-next", Button).disabled = len(changes) < 2
        self._update_shell_chrome()

    def _hide_change_review(self) -> None:
        self._change_review_visible = False
        self.remove_class("reviewing-changes")
        self.query_one("#change-panel", Vertical).add_class("hidden")
        self.query_one("#change-title", Static).update("")
        self.query_one("#change-content", RichLog).clear()
        self._update_shell_chrome()

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
        plan_path = run.path / "plan.md"
        diff_path = run.path / "diff.patch"
        browser_path = run.path / "browser.md"
        plan = await asyncio.to_thread(plan_path.read_text, encoding="utf-8") if plan_path.exists() else ""
        summary = await asyncio.to_thread(summary_path.read_text, encoding="utf-8") if summary_path.exists() else ""
        diff = await asyncio.to_thread(diff_path.read_text, encoding="utf-8") if diff_path.exists() else ""
        browser = await asyncio.to_thread(browser_path.read_text, encoding="utf-8") if browser_path.exists() else ""
        await self.run_store.append_event(run.run_id, "cancelled", {"reason": "Cancelled by user command."})
        await self.run_store.finish_run(
            run.run_id,
            "cancelled",
            plan=plan,
            summary=summary,
            verification="Run cancelled by user command.\n",
            diff=diff,
            browser=browser,
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

    async def _resolve_optional_run_id(self, value: str) -> str | None:
        if value.strip():
            return await self._resolve_run_id(value)
        if self._active_run_id is not None:
            return self._active_run_id
        runs = await self.run_store.list_runs(limit=1)
        return runs[0].run_id if runs else None

    def _handle_tools_command(self, argument: str) -> None:
        parts = argument.split()
        if not parts:
            self._append_system("Usage: /tools list|expand|collapse|toggle <index>")
            return

        action = parts[0].lower()
        if action == "list":
            self._append_system(self._tools_list_text())
            return

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

        self._append_system("Usage: /tools list|expand|collapse|toggle <index>")

    def _tools_list_text(self) -> str:
        try:
            registry = create_builtin_registry(self.config, memory_store=self.memory_store)
        except Exception as exc:
            return f"Could not load tools: {exc}"
        lines = ["Available tools:"]
        for schema in registry.schemas():
            name = str(schema.get("name", "tool"))
            description = str(schema.get("description", ""))
            marker = " (MCP)" if name.startswith("mcp__") else ""
            lines.append(f"- {name}{marker}: {description}")
        return "\n".join(lines)

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

        self._set_working_directory(parent)
        self._append_system(f"Explorer root and agent working directory set to {parent}.")

    def _set_working_directory(self, path: Path) -> None:
        self.config = _replace_general(self.config, working_directory=path)
        self.skill_store = SkillStore(self.config.general.working_directory, skills_config=self.config.skills)
        self.soul_store = SoulStore(self.config.general.working_directory)
        self.query_one("#file-tree", DirectoryTree).path = self.config.general.working_directory
        self.query_one("#sidebar-root", Static).update(self._sidebar_root_text())
        self._rebuild_agent()
        self._update_status()
        self._update_shell_chrome()

    def _append_user(self, text: str) -> int:
        return self._append_entry("user", text)

    def _append_assistant(self, text: str) -> int:
        return self._append_entry("assistant", text)

    def _append_system(self, text: str) -> int:
        return self._append_entry("system", text)

    def _append_permission(self, text: str) -> int:
        return self._append_entry("permission", text)

    def _append_attachment(self, attachment: UserAttachment, *, pending: bool = False) -> int:
        title = attachment.filename or Path(attachment.path).name or "image"
        state = "pending image" if pending else "image"
        return self._append_entry(
            "attachment",
            _attachment_summary(attachment),
            title=f"{state}: {title}",
            metadata={"attachment": attachment.as_payload(), "pending": pending},
        )

    def _append_tool_call(self, call: ToolCall) -> int:
        path = self._display_tool_path(call.arguments.get("path"))
        title = f"{call.name} pending"
        if path and call.name in {"edit_file", "write_file", "read_file"}:
            title = f"{call.name} · {path} · pending"
        index = self._append_entry(
            "tool",
            self._format_arguments(call.arguments),
            title=title,
            collapsed=True,
            metadata={"tool": call.name, "status": "pending", "call": call},
        )
        self._tool_entry_by_call_id[call.id] = index
        return index

    def _append_tool_result(self, call: ToolCall, result: ToolResult) -> int:
        metadata = dict(result.metadata)
        metadata.update({"tool": call.name, "status": "error" if result.is_error else "result", "call": call})
        content = result.as_text()
        collapsed = True
        title = f"{call.name} {'error' if result.is_error else 'result'}"
        changed = bool(result.metadata.get("changed"))
        if call.name in {"edit_file", "write_file"} and changed and "before" in result.metadata and "after" in result.metadata:
            content = self._diff_text(
                str(result.metadata.get("before", "")),
                str(result.metadata.get("after", "")),
                str(result.metadata.get("path", call.arguments.get("path", "file"))),
            )
            metadata["syntax"] = "diff"
            stats = _file_change_stats(content)
            path = self._display_tool_path(result.metadata.get("path", call.arguments.get("path"))) or "file"
            verb = "CREATE" if result.metadata.get("created") else "EDIT"
            title = f"{verb} {path} · {stats.compact}"
            metadata["change_stats"] = {
                "additions": stats.additions,
                "deletions": stats.deletions,
                "hunks": stats.hunks,
            }
            metadata["change_summary"] = f"{stats.compact} · Ctrl+E review"
            collapsed = len(content.splitlines()) > 80
            for transcript_entry in self.transcript:
                if transcript_entry.role == "tool" and (transcript_entry.metadata or {}).get("syntax") == "diff":
                    transcript_entry.collapsed = True
        index = self._tool_entry_by_call_id.pop(call.id, None)
        if index is None or index >= len(self.transcript):
            created_index = self._append_entry("tool", content, title=title, collapsed=collapsed, metadata=metadata)
            if metadata.get("syntax") == "diff" and self._change_review_visible:
                self._change_review_cursor = len(self._change_entries()) - 1
                self._refresh_change_review()
            return created_index

        entry = self.transcript[index]
        entry.content = content
        entry.title = title
        entry.collapsed = collapsed
        entry.metadata = metadata
        self._render_transcript()
        if metadata.get("syntax") == "diff" and self._change_review_visible:
            self._change_review_cursor = len(self._change_entries()) - 1
            self._refresh_change_review()
        self._update_shell_chrome()
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
        scroll_y, stick_to_end, selection = self._capture_chat_view_state(chat)
        chat.clear()
        self._chat_entry_spans.clear()
        for index, entry in enumerate(self.transcript):
            start_line = len(chat.lines)
            chat.write(self._format_entry(entry, index), scroll_end=False)
            self._chat_entry_spans[index] = (start_line, len(chat.lines))
        self._restore_chat_view_state(chat, scroll_y, stick_to_end, selection)

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

        scroll_y, stick_to_end, selection = self._capture_chat_view_state(chat)
        del chat.lines[start_line:end_line]
        if hasattr(chat, "_line_cache"):
            chat._line_cache.clear()
        chat.write(self._format_entry(self.transcript[index], index), scroll_end=False)
        self._chat_entry_spans[index] = (start_line, len(chat.lines))
        self._restore_chat_view_state(chat, scroll_y, stick_to_end, selection)
        return True

    def _capture_chat_view_state(self, chat: RichLog) -> tuple[float, bool, Selection | None]:
        selection = self.screen.selections.get(chat)
        stick_to_end = selection is None and (not chat.lines or chat.is_vertical_scroll_end)
        return chat.scroll_y, stick_to_end, selection

    def _restore_chat_view_state(self, chat: RichLog, scroll_y: float, stick_to_end: bool, selection: Selection | None) -> None:
        if stick_to_end:
            chat.scroll_end(animate=False, immediate=True)
        else:
            chat.scroll_to(y=min(scroll_y, chat.max_scroll_y), animate=False, immediate=True)
        if selection is not None:
            self.screen.selections[chat] = selection

    def _format_entry(self, entry: TranscriptEntry, index: int = 0) -> RenderableType:
        if entry.role == "startup":
            return _startup_renderable(self.startup_expanded, accent=self._theme.accent, light=self._theme.is_light)
        if entry.role == "user":
            return Text.assemble(("User: ", f"bold {self._theme.accent}"), entry.content)
        if entry.role == "assistant":
            if not entry.content:
                return Text("Libre Claw: streaming...", style=f"bold {self._theme.accent} dim")
            return Group(
                Text("Libre Claw:", style=f"bold {self._theme.accent}"),
                _lobster_markdown(entry.content, light=self._theme.is_light),
            )
        if entry.role == "tool":
            title = entry.title or "Tool"
            metadata = entry.metadata or {}
            status = str(metadata.get("status", ""))
            style = _tool_style(status)
            if entry.collapsed:
                label = "Change" if metadata.get("syntax") == "diff" else "Tool"
                return Text.assemble(
                    (f"{label} {self._tool_display_index(index)}: ", f"bold {style}"),
                    (title, f"bold {style}"),
                    (" - ", "dim"),
                    (_tool_preview(entry), "dim"),
                )
            if metadata.get("syntax") == "diff":
                return Group(
                    Text(
                        f"Change {self._tool_display_index(index)}: {title} · Ctrl+E focused review",
                        style=f"bold {style}",
                    ),
                    _lobster_syntax(entry.content, "diff", light=self._theme.is_light, word_wrap=False),
                )
            return Text.assemble(
                (f"Tool {self._tool_display_index(index)}: {title}\n", f"bold {style}"),
                _compact_tool_output(entry.content, expanded=True),
            )
        if entry.role == "permission":
            return Text.assemble(("Permission: ", "bold yellow"), entry.content)
        if entry.role == "attachment":
            return _attachment_renderable(entry, accent=self._theme.accent)
        if entry.role == "file":
            title = entry.title or "File"
            return Group(
                Text(f"File: {title}", style=f"bold {self._theme.accent}"),
                _lobster_syntax(entry.content, "text", light=self._theme.is_light),
            )
        return Text("System: " + entry.content, style="dim")

    def _tool_display_index(self, transcript_index: int) -> int:
        return sum(1 for entry in self.transcript[: transcript_index + 1] if entry.role == "tool") - 1

    def _format_arguments(self, arguments: object) -> str:
        try:
            return json.dumps(arguments, sort_keys=True)
        except TypeError:
            return str(arguments)

    def _display_tool_path(self, value: object) -> str:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            return ""
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            return str(path)
        try:
            return str(path.resolve().relative_to(self.config.general.working_directory.resolve()))
        except ValueError:
            return str(path)

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
        if self.daemon_client is not None:
            return
        try:
            provider = create_provider(self.config)
            fallbacks = create_fallback_providers(self.config)
        except ProviderConfigurationError as exc:
            self.provider_error = str(exc)
            return
        try:
            tool_registry = create_builtin_registry(self.config, memory_store=self.memory_store)
        except Exception as exc:
            self.provider_error = str(exc)
            return

        self.agent = Agent(
            session=self.session,
            provider=provider,
            tool_registry=tool_registry,
            permission_manager=PermissionManager(self.config.permissions),
            system_prompt=self.config.agent.system_prompt,
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            context_window_tokens=self.config.agent.context_window_tokens,
            compact_keep_last=self.config.agent.compact_keep_last,
            provider_retry_attempts=self.config.agent.provider_retry_attempts,
            provider_retry_initial_delay=self.config.agent.provider_retry_initial_delay,
            memory_facts=self.memory_facts,
            system_prompt_extra=self.config.agent.system_prompt_extra,
            skill_provider=self.skill_store.relevant_skill_texts,
            soul_provider=self.soul_store.soul_texts,
            memory_provider=self._relevant_memory_texts,
            fallback_providers=tuple((fallback.label, fallback.provider) for fallback in fallbacks),
            fallback_recheck_after_attempts=self.config.fallback.recheck_after_attempts,
        )

    async def _initialize_memory(self) -> None:
        await self.memory_store.initialize()
        await self._refresh_memory_facts()
        self._archive_session_event_later(
            "session_started",
            {
                "surface": "tui",
                "working_directory": str(self.config.general.working_directory),
                "provider": self.config.general.default_provider,
                "model": _effective_model(self.config),
            },
        )
        self._rebuild_agent()

    async def _refresh_memory_facts(self) -> None:
        self.memory_facts = await self.memory_store.list_always_injected_memories()
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
                elif block_type == "image":
                    attachment = UserAttachment(
                        media_type=str(block.get("media_type", "image/*")),
                        data=str(block.get("data", "")),
                        filename=str(block.get("filename", "")),
                        path=str(block.get("path", "")),
                    )
                    tool_parts.append(
                        TranscriptEntry(
                            role="attachment",
                            content=_attachment_summary(attachment),
                            title=(
                                "image: "
                                f"{attachment.filename or Path(attachment.path).name or attachment.media_type}"
                            ),
                            metadata={"attachment": attachment.as_payload()},
                        )
                    )

            if text_parts:
                entries.append(TranscriptEntry(role=message.role, content="\n".join(text_parts)))
            entries.extend(tool_parts)
        return entries

    def _sync_sidebar_visibility(self) -> None:
        self.query_one("#sidebar", Vertical).display = self.sidebar_visible
        self.query_one("#sidebar-rail", Vertical).display = not self.sidebar_visible

    def _append_startup_entry(self) -> None:
        if self.transcript and self.transcript[0].role == "startup":
            self._startup_entry_index = 0
            return
        self.transcript = [entry for entry in self.transcript if entry.role != "startup"]
        self.transcript.insert(0, TranscriptEntry(role="startup", content=""))
        self._startup_entry_index = 0
        self._render_transcript()

    def _refresh_startup_entry(self) -> None:
        if self._startup_entry_index is None or self._startup_entry_index >= len(self.transcript):
            self._append_startup_entry()
            return
        self._render_transcript()

    def _sidebar_root_text(self) -> str:
        return f"cwd: {self.config.general.working_directory}"

    def _update_shell_chrome(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#workspace-bar", Static).update(self._workspace_bar_text())
        self.query_one("#composer-meta", Static).update(self._composer_meta_text())

    def _workspace_bar_text(self) -> str:
        root = self.config.general.working_directory
        workspace = root.name or str(root)
        mode = "daemon" if self.daemon_client is not None else "direct"
        run = _short_run_id(self._active_run_id) if self._active_run_id else "idle"
        change_count = len(self._change_entries())
        change_label = f"{change_count} edit{'s' if change_count != 1 else ''}"
        return f"WORKSPACE  {workspace}   ·   {mode}   ·   RUN  {run}   ·   {change_label}   ·   Ctrl+E review"

    def _composer_meta_text(self) -> str:
        if self._pending_permission is not None:
            return "APPROVAL  Review the exact operation above · y allow · n deny · a always tool · ! always exact"
        if self._goal_description is not None:
            return f"GOAL  turn {self._goal_turn}/{self._goal_max_turns} · /steer adjusts course · Esc interrupts"
        if self._active_task is not None and not self._active_task.done():
            return "RUNNING  Streaming output · /btw adds context · Esc interrupts"
        if self._pending_attachments:
            count = len(self._pending_attachments)
            return f"MESSAGE  {count} image{'s' if count != 1 else ''} attached · Enter sends · /attach clear removes"
        return "MESSAGE  Enter sends · / opens commands · paste an image path to attach"

    def _update_palette(self, query: str = "", *, reset_selection: bool = True) -> None:
        palette = self.query_one("#palette", Static)
        if not self.palette_open:
            palette.add_class("hidden")
            palette.update("")
            self._palette_selected_index = 0
            return
        palette.remove_class("hidden")
        matches = self._palette_matches(query)
        if reset_selection:
            self._palette_selected_index = 0
        self._palette_selected_index = _bounded_menu_index(self._palette_selected_index, len(matches))
        palette.update(self._palette_text(query))

    def _close_palette(self) -> None:
        self.palette_open = False
        self._palette_selected_index = 0
        self._update_palette()
        self.query_one("#input", Input).placeholder = self._input_placeholder()

    def _palette_matches(self, query: str) -> list[SlashCommand]:
        normalized = query.lower().strip()
        if not normalized:
            return list(SLASH_COMMANDS)
        name_matches = [command for command in SLASH_COMMANDS if normalized in command.name.lower()]
        description_matches = [
            command
            for command in SLASH_COMMANDS
            if command not in name_matches and normalized in command.description.lower()
        ]
        return [*name_matches, *description_matches]

    def _palette_text(self, query: str) -> str:
        matches = self._palette_matches(query)
        lines = ["Command palette - Up/Down select, Enter run, Tab fill"]
        lines.extend(
            _menu_line(command, selected=index == self._palette_selected_index, usage_width=26)
            for index, command in enumerate(matches)
        )
        return "\n".join(lines)

    def _help_text(self) -> str:
        command_lines = "\n".join(f"{command.usage} - {command.description}" for command in SLASH_COMMANDS)
        return (
            f"{command_lines}\n"
            "Ctrl+C exits. Ctrl+R toggles release notes. Esc or /cancel interrupts. "
            "PageUp/PageDown scroll the transcript. Ctrl+Home/Ctrl+End jump top/bottom. "
            "Menus support Up/Down, Enter, and Tab. "
            "Permission prompts support buttons plus y, n, a, ! shortcuts."
        )

    def _update_slash_suggestions(self, text: str, *, reset_selection: bool = True) -> None:
        self._slash_suggestions = self._slash_suggestion_matches(text)
        if reset_selection:
            self._slash_suggestion_index = 0
        self._slash_suggestion_index = _bounded_menu_index(self._slash_suggestion_index, len(self._slash_suggestions))
        suggestions = self.query_one("#suggestions", Static)
        if not self._slash_suggestions:
            suggestions.add_class("hidden")
            suggestions.update("")
            self._slash_suggestion_index = 0
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
        if lowered.startswith("/update "):
            query = lowered.removeprefix("/update ").strip()
            suggestion = SlashCommand(
                "/update --dry-run",
                "/update --dry-run",
                "Check origin/main without changing files",
            )
            return [suggestion] if not query or "--dry-run".startswith(query) else []

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

        if lowered.startswith("/fallback "):
            query = lowered.removeprefix("/fallback ").strip()
            suggestions = _fallback_suggestion_commands(self.config)
            if not query:
                return suggestions[:6]
            return [
                suggestion
                for suggestion in suggestions
                if query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/theme "):
            query = lowered.removeprefix("/theme ").strip()
            suggestions = _theme_suggestion_commands()
            if not query:
                return suggestions[:6]
            return [
                suggestion
                for suggestion in suggestions
                if query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/setup "):
            query = lowered.removeprefix("/setup ").strip()
            suggestions = [
                SlashCommand("/setup status", "/setup status", "Show provider and key readiness"),
                SlashCommand("/setup provider openrouter", "/setup provider openrouter", "Switch to OpenRouter"),
                SlashCommand("/setup key openrouter", "/setup key openrouter", "Store OpenRouter key inside the TUI"),
                SlashCommand("/setup moonshot", "/setup moonshot", "Configure Moonshot AI with Kimi K3"),
                SlashCommand("/setup key moonshot", "/setup key moonshot", "Store Moonshot Platform key inside the TUI"),
                SlashCommand("/setup key anthropic", "/setup key anthropic", "Store Anthropic key inside the TUI"),
                SlashCommand("/setup key openai", "/setup key openai", "Store OpenAI key inside the TUI"),
                SlashCommand("/setup key ollama", "/setup key ollama", "Store Ollama Cloud key inside the TUI"),
                SlashCommand("/setup codex", "/setup codex", "Start Codex/ChatGPT login"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
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

        if lowered.startswith("/attach "):
            query = lowered.removeprefix("/attach ").strip()
            suggestions = [
                SlashCommand("/attach paste", "/attach paste", "Attach image from the OS clipboard"),
                SlashCommand("/attach list", "/attach list", "List pending image attachments"),
                SlashCommand("/attach clear", "/attach clear", "Clear pending image attachments"),
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

        if lowered.startswith("/artifacts "):
            query = lowered.removeprefix("/artifacts ").strip()
            suggestions = [
                SlashCommand("/artifacts plan", "/artifacts plan [id]", "Show run plan"),
                SlashCommand("/artifacts summary", "/artifacts summary [id]", "Show run summary"),
                SlashCommand("/artifacts verify", "/artifacts verify [id]", "Show verification notes"),
                SlashCommand("/artifacts diff", "/artifacts diff [id]", "Show captured diff"),
                SlashCommand("/artifacts browser", "/artifacts browser [id]", "Show browser screenshots/downloads"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/review "):
            query = lowered.removeprefix("/review ").strip()
            suggestions = [
                SlashCommand("/review latest", "/review latest", "Open the newest file edit"),
                SlashCommand("/review previous", "/review previous", "Review the previous file edit"),
                SlashCommand("/review next", "/review next", "Review the next file edit"),
                SlashCommand("/review close", "/review close", "Close the change review drawer"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/tools "):
            query = lowered.removeprefix("/tools ").strip()
            suggestions = [
                SlashCommand("/tools list", "/tools list", "List exposed tools including MCP"),
                SlashCommand("/tools expand", "/tools expand", "Expand all tool cards"),
                SlashCommand("/tools collapse", "/tools collapse", "Collapse all tool cards"),
                SlashCommand("/tools toggle ", "/tools toggle <index>", "Toggle one tool card"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/petdex "):
            query = lowered.removeprefix("/petdex ").strip()
            suggestions = [
                SlashCommand("/petdex status", "/petdex status", "Show Petdex endpoint and token status"),
                SlashCommand("/petdex state ready", "/petdex state ready", "Send ready state"),
                SlashCommand("/petdex state running", "/petdex state running", "Send running state"),
                SlashCommand("/petdex state working", "/petdex state working", "Send working state"),
                SlashCommand("/petdex state success", "/petdex state success", "Send success state"),
                SlashCommand("/petdex state error", "/petdex state error", "Send error state"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]

        if lowered.startswith("/skills "):
            query = lowered.removeprefix("/skills ").strip()
            suggestions = [
                SlashCommand("/skills list", "/skills list", "List user and project skills"),
                SlashCommand("/skills sync", "/skills sync", "Refresh external skill catalogues"),
                SlashCommand("/skills show ", "/skills show <name>", "Show one skill"),
                SlashCommand("/skills add --user ", "/skills add --user <name> <markdown>", "Add a global user skill"),
                SlashCommand("/skills add --project ", "/skills add --project <name> <markdown>", "Add a project skill"),
                SlashCommand("/skills edit ", "/skills edit <name> <markdown>", "Replace a skill body"),
                SlashCommand("/skills delete ", "/skills delete <name>", "Delete a skill"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        if lowered.startswith("/soul "):
            query = lowered.removeprefix("/soul ").strip()
            suggestions = [
                SlashCommand("/soul status", "/soul status", "Show loaded SOUL.md files"),
                SlashCommand("/soul show", "/soul show", "Show active persona text"),
                SlashCommand("/soul init --user", "/soul init --user", "Create ~/.libre-claw/SOUL.md"),
                SlashCommand("/soul init --project", "/soul init --project", "Create .libre-claw/SOUL.md"),
                SlashCommand("/soul init --root", "/soul init --root", "Create ./SOUL.md"),
                SlashCommand("/soul reload", "/soul reload", "Reload persona files"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        if lowered.startswith("/schedule "):
            query = lowered.removeprefix("/schedule ").strip()
            suggestions = [
                SlashCommand("/schedule list", "/schedule list", "List recurring runs"),
                SlashCommand("/schedule examples", "/schedule examples", "Show ready-made schedules"),
                SlashCommand(
                    "/schedule add daily 09:00 | Daily repo health check | ",
                    "/schedule add <schedule> | <name> | <prompt>",
                    "Create a recurring run",
                ),
                SlashCommand("/schedule pause ", "/schedule pause <id>", "Pause a schedule"),
                SlashCommand("/schedule resume ", "/schedule resume <id>", "Resume a schedule"),
                SlashCommand("/schedule delete ", "/schedule delete <id>", "Delete a schedule"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        if lowered.startswith("/heartbeat "):
            query = lowered.removeprefix("/heartbeat ").strip()
            suggestions = [
                SlashCommand("/heartbeat status", "/heartbeat status", "Show heartbeat state"),
                SlashCommand("/heartbeat once", "/heartbeat once", "Run one checklist now"),
                SlashCommand("/heartbeat start every 30 minutes", "/heartbeat start [interval]", "Start TUI check-ins"),
                SlashCommand("/heartbeat start 1h", "/heartbeat start 1h", "Start hourly TUI check-ins"),
                SlashCommand("/heartbeat stop", "/heartbeat stop", "Stop TUI check-ins"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        if lowered.startswith("/memory "):
            query = lowered.removeprefix("/memory ").strip()
            suggestions = [
                SlashCommand("/memory status", "/memory status", "Show memory status"),
                SlashCommand("/memory list", "/memory list", "List active memories"),
                SlashCommand("/memory search ", "/memory search <query>", "Search memory"),
                SlashCommand("/memory add ", "/memory add <text>", "Add a memory"),
                SlashCommand("/memory forget ", "/memory forget <id>", "Disable one memory"),
                SlashCommand("/memory summarize", "/memory summarize", "Save current session summary"),
                SlashCommand("/memory import-runs", "/memory import-runs", "Import run summaries"),
                SlashCommand("/memory on", "/memory on", "Enable memory for this session"),
                SlashCommand("/memory off", "/memory off", "Disable memory for this session"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        if lowered.startswith("/workspace "):
            query = lowered.removeprefix("/workspace ").strip()
            suggestions = [
                SlashCommand("/workspace status", "/workspace status", "Show workspace paths"),
                SlashCommand("/workspace init", "/workspace init [path]", "Create and use the dedicated workspace"),
                SlashCommand(
                    "/workspace init --overwrite",
                    "/workspace init [path] --overwrite",
                    "Refresh workspace Markdown templates",
                ),
                SlashCommand("/workspace use ", "/workspace use <path>", "Use an existing workspace directory"),
            ]
            return [
                suggestion
                for suggestion in suggestions
                if not query or query in suggestion.name.lower() or query in suggestion.description.lower()
            ][:6]
        return []

    def _slash_suggestion_text(self, suggestions: list[SlashCommand]) -> str:
        return "\n".join(
            _menu_line(command, selected=index == self._slash_suggestion_index, usage_width=30)
            for index, command in enumerate(suggestions)
        )

    def _should_complete_on_submit(self, text: str) -> bool:
        if not self._slash_suggestions:
            return False
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return False
        if " " in stripped:
            return stripped.lower() not in {command.name.lower() for command in self._slash_suggestions}
        return all(stripped.lower() != command.name for command in SLASH_COMMANDS)

    def _accept_selected_suggestion(self, input_widget: Input) -> None:
        if not self._slash_suggestions:
            return
        command = self._slash_suggestions[_bounded_menu_index(self._slash_suggestion_index, len(self._slash_suggestions))]
        input_widget.value = self._completion_text(command)
        input_widget.cursor_position = len(input_widget.value)
        self._update_slash_suggestions(input_widget.value)

    def _accept_first_suggestion(self, input_widget: Input) -> None:
        self._slash_suggestion_index = 0
        self._accept_selected_suggestion(input_widget)

    def _accept_selected_palette_command(self, input_widget: Input) -> None:
        if not self.palette_open:
            return
        matches = self._palette_matches(input_widget.value)
        if not matches:
            return
        command = matches[_bounded_menu_index(self._palette_selected_index, len(matches))]
        input_widget.value = self._completion_text(command)
        input_widget.cursor_position = len(input_widget.value)
        self._close_palette()
        self._update_slash_suggestions(input_widget.value)

    def _move_menu_selection(self, delta: int) -> bool:
        input_widget = self.query_one("#input", Input)
        if self.palette_open:
            matches = self._palette_matches(input_widget.value)
            if not matches:
                return False
            self._palette_selected_index = (self._palette_selected_index + delta) % len(matches)
            self._update_palette(input_widget.value, reset_selection=False)
            return True
        if self._slash_suggestions:
            self._slash_suggestion_index = (self._slash_suggestion_index + delta) % len(self._slash_suggestions)
            self._update_slash_suggestions(input_widget.value, reset_selection=False)
            return True
        return False

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

    async def _status_report_text(self) -> str:
        if self.daemon_client is not None:
            await self._sync_daemon_model_if_changed()
        meter = self._context_meter()
        provider = _canonical_tui_provider(self.config.general.default_provider)
        model = _effective_model(self.config)
        active = "running" if self._active_task is not None and not self._active_task.done() else "idle"
        if self._goal_description is not None and active == "running":
            active = f"goal {self._goal_turn}/{self._goal_max_turns}"
        lines = [
            "Libre Claw status",
            "",
            "Model",
            f"- Provider: `{provider}`",
            f"- Model: `{model}`",
            "",
            "Context",
            f"- Window: {_format_token_count(meter.context_window_tokens)} tokens",
            f"- Used: ~{_format_token_count(meter.estimated_tokens)} estimated tokens",
            f"- Fill: `{_context_bar(meter)}` {meter.display_percent}",
            "",
            "Usage",
            f"- Tokens: {_format_token_count(self.usage.total_tokens)} total ({self.usage.input_tokens} input, {self.usage.output_tokens} output)",
            f"- Cost: {_format_usage_cost(self.usage)}",
            "",
            "Run",
            f"- State: {active}",
        ]
        if self._active_run_id:
            lines.append(f"- Active run: `{self._active_run_id}`")
        if self.daemon_client is not None:
            lines.extend(["", await self._daemon_status_text()])
        return "\n".join(lines)

    async def _daemon_status_text(self) -> str:
        if self.daemon_client is None:
            return (
                "Daemon\n"
                "- Mode: disabled for this TUI session\n"
                f"- URL: {daemon_base_url(self.config)}\n"
                "- Start it with `libre-claw start -d` or launch the TUI with daemon mode enabled."
            )
        try:
            health = await self.daemon_client.health()
        except Exception as exc:
            return f"Daemon\n- URL: {daemon_base_url(self.config)}\n- State: unreachable\n- Error: {exc}"
        return "\n".join(
            [
                "Daemon",
                f"- URL: {daemon_base_url(self.config)}",
                f"- State: {'ok' if health.get('ok') else 'not ok'}",
                f"- Active runs: {health.get('active_runs', 0)}",
                f"- Telegram bridge: {health.get('telegram_bridge', 'unknown')}",
            ]
        )

    async def _handle_usage_command(self, argument: str) -> None:
        normalized = argument.strip().lower()
        if normalized in {"", "help"}:
            self._append_system(_usage_help_text())
            return
        if normalized in {"openrouter attribution", "attribution", "verify", "openrouter verify"}:
            self._append_system(openrouter_attribution_text())
            return
        if normalized in {"openrouter presets", "presets", "models", "openrouter models"}:
            self._append_system(openrouter_model_presets_text())
            return
        if normalized not in {"openrouter", "all"}:
            self._append_system(_usage_help_text())
            return

        provider = None if normalized == "all" else "openrouter"
        if self.daemon_client is not None:
            try:
                payload = await self.daemon_client.usage(provider=provider or "", limit=250)
                text = str(payload.get("text", "")).strip()
                if text:
                    self._append_system(text)
                    return
            except Exception:
                pass

        records = await load_usage_records(self.run_store, provider=provider, limit=250)
        self._append_system(usage_report_text(records, provider=provider or "all"))

    def _context_meter(self) -> ContextMeter:
        extra_texts = tuple(
            text
            for text in (
                self.config.agent.system_prompt,
                self.config.agent.system_prompt_extra,
                *self.soul_store.soul_texts(),
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
        activity = f"{elapsed}s | {active}" if active != "idle" else "idle"
        return (
            f"Libre Claw v{__version__} | {provider}:{model} | {_format_usage_cost(self.usage)} | "
            f"{_token_status_text(self.usage, meter)} | ctx {_context_status_text(meter)} | {activity}"
        )

    def _update_status(self) -> None:
        self._sync_global_model_if_changed()
        self._sync_daemon_model_later()
        self._update_shell_chrome()
        if self.config.tui.show_status_bar:
            status = self._status_text()
            if status != self._last_status_text:
                self.query_one("#status", Static).update(status)
                self._last_status_text = status

    def _sync_daemon_model_later(self) -> None:
        if self.daemon_client is None:
            return
        if self._daemon_model_sync_task is not None and not self._daemon_model_sync_task.done():
            return
        self._daemon_model_sync_task = asyncio.create_task(self._sync_daemon_model_if_changed())

    async def _sync_daemon_model_if_changed(self) -> None:
        if self.daemon_client is None:
            return
        try:
            payload = await self.daemon_client.current_model()
        except Exception:
            return

        provider = _canonical_tui_provider(str(payload.get("provider", "")).strip())
        model = str(payload.get("model", "")).strip()
        if not provider or not model:
            return

        self._apply_daemon_model_payload(payload, announce_model_change=True)

    def _apply_daemon_model_payload(self, payload: Mapping[str, Any], *, announce_model_change: bool) -> None:
        provider = _canonical_tui_provider(str(payload.get("provider", "")).strip())
        model = str(payload.get("model", "")).strip()
        if not provider or not model:
            return

        updated = _config_with_daemon_model_payload(self.config, payload)
        model_changed = (
            provider != _canonical_tui_provider(self.config.general.default_provider)
            or model != self.config.general.default_model
        )
        if not model_changed and not _runtime_model_metadata_changed(self.config, updated):
            return

        self.config = updated
        self._rebuild_agent()
        if announce_model_change and model_changed:
            self._append_system(f"Daemon model changed to {provider}:{model}; TUI session updated.")
        if self.config.tui.show_status_bar:
            self.query_one("#status", Static).update(self._status_text())

    def _sync_global_model_if_changed(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            return
        path = global_config_path(self.config)
        mtime = _path_mtime_ns(path)
        if mtime is None:
            self._global_model_config_path = path
            self._global_model_config_mtime_ns = None
            return
        if path == self._global_model_config_path and mtime == self._global_model_config_mtime_ns:
            return

        self._global_model_config_path = path
        self._global_model_config_mtime_ns = mtime
        try:
            updated = load_config(path)
        except ConfigError:
            return

        provider = _canonical_tui_provider(updated.general.default_provider)
        model = updated.general.default_model
        theme = normalize_theme(updated.general.theme)
        model_changed = (
            provider != _canonical_tui_provider(self.config.general.default_provider)
            or model != self.config.general.default_model
        )
        theme_changed = theme != normalize_theme(self.config.general.theme)
        if not model_changed and not theme_changed:
            return

        if model_changed:
            self.config = _replace_model_selection(self.config, provider, model, providers=updated.providers)
            self._rebuild_agent()
            self._append_system(f"Global model changed to {provider}:{model}; TUI session updated.")
        if theme_changed:
            self._set_tui_theme(theme)
            self._append_system(f"Global theme changed to {THEME_PALETTES[theme].label}; TUI session updated.")

    def _input_placeholder(self) -> str:
        if self.palette_open:
            return "Command palette query..."
        if self._pending_key_setup is not None:
            return f"Paste {self._pending_key_setup.provider} API key. It is hidden. Type /cancel to abort."
        if self._pending_permission is not None:
            return "Permission prompt active: click a choice or press y/n/a/!"
        if self._goal_description is not None:
            return "Goal mode active... (/goal status, /goal stop)"
        return "Type a message... (/help, PageUp/PageDown scroll, Ctrl+R release, Ctrl+C exit)"


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
        fallback=config.fallback,
        heartbeat=config.heartbeat,
        memory=config.memory,
        daemon=config.daemon,
        automations=config.automations,
        browser=config.browser,
        petdex=config.petdex,
        mcp=config.mcp,
        skills=config.skills,
        providers=config.providers,
        source_paths=config.source_paths,
    )


def _petdex_final_state(state: str) -> str:
    if state == "done":
        return "success"
    if state == "cancelled":
        return "failed"
    if state == "blocked":
        return "waving"
    return "error" if state == "failed" else "idle"


def _replace_model_selection(
    config: LibreClawConfig,
    provider: str,
    model: str,
    *,
    providers: Mapping[str, Mapping[str, Any]] | None = None,
) -> LibreClawConfig:
    clean_provider = _canonical_tui_provider(provider)
    clean_model = model.strip()
    next_providers: dict[str, Mapping[str, Any]] = {
        name: dict(value) if isinstance(value, Mapping) else value
        for name, value in (providers or config.providers).items()
    }
    provider_config = next_providers.get(clean_provider)
    if isinstance(provider_config, Mapping):
        updated_provider = dict(provider_config)
        updated_provider["default_model"] = clean_model
        next_providers[clean_provider] = updated_provider
    updated = _replace_general(config, default_provider=clean_provider, default_model=clean_model)
    return replace(updated, providers=next_providers)


def _config_with_daemon_model_payload(config: LibreClawConfig, payload: Mapping[str, Any]) -> LibreClawConfig:
    provider = _canonical_tui_provider(str(payload.get("provider") or config.general.default_provider))
    model = str(payload.get("model") or config.general.default_model).strip()
    updated = _replace_model_selection(config, provider, model)
    if provider not in {"openrouter", "moonshot"}:
        return updated

    context_window = _positive_int(payload.get("detected_context_window_tokens")) or _positive_int(
        payload.get("context_window_tokens")
    )
    if context_window is not None:
        updated = replace(updated, agent=replace(updated.agent, context_window_tokens=context_window))

    providers: dict[str, Mapping[str, Any]] = {
        name: dict(value) if isinstance(value, Mapping) else value for name, value in updated.providers.items()
    }
    provider_config = dict(providers.get(provider, {}))
    for key in (
        "detected_context_window_tokens",
        "detected_max_completion_tokens",
        "detected_context_source",
        "detected_context_model",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            provider_config[key] = value
    providers[provider] = provider_config
    return replace(updated, providers=providers)


def _runtime_model_metadata_changed(before: LibreClawConfig, after: LibreClawConfig) -> bool:
    if before.agent.context_window_tokens != after.agent.context_window_tokens:
        return True
    return _provider_metadata_tuple(before) != _provider_metadata_tuple(after)


def _provider_metadata_tuple(config: LibreClawConfig) -> tuple[object, ...]:
    provider = _canonical_tui_provider(config.general.default_provider)
    provider_config = config.providers.get(provider, {})
    if not isinstance(provider_config, Mapping):
        return ()
    return tuple(
        provider_config.get(key)
        for key in (
            "detected_context_window_tokens",
            "detected_max_completion_tokens",
            "detected_context_source",
            "detected_context_model",
        )
    )


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        integer = int(value)
        return integer if integer > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        integer = int(value)
        return integer if integer > 0 else None
    return None


def _path_mtime_ns(path: Path) -> int | None:
    try:
        return path.expanduser().stat().st_mtime_ns
    except OSError:
        return None


def _append_session_note(summary: str | None, note: str, *, limit: int = 4000) -> str:
    lines = [line for line in (summary or "").splitlines() if line.strip()]
    lines.append("User steering: " + note.strip())
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[-limit:].lstrip()


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


def _parse_fallback_slot(value: str) -> int | None:
    if not value.isdigit():
        return None
    slot = int(value)
    if 1 <= slot <= 3:
        return slot
    return None


def _parse_fallback_key_env(tokens: Sequence[str]) -> tuple[str, str | None]:
    api_key_env = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--key-env", "--api-key-env"}:
            if index + 1 >= len(tokens):
                return "", f"{token} requires an environment variable name."
            api_key_env = tokens[index + 1].strip()
            index += 2
            continue
        return "", f"Unknown fallback option: {token}"
    return api_key_env, None


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
    if provider == "anthropic":
        lines.append("- curl https://api.anthropic.com/v1/models ...  # live Claude API model catalog")
    if provider == "ollama":
        lines.append(
            "- curl https://ollama.com/api/tags -H 'Authorization: Bearer $OLLAMA_API_KEY'  # live Cloud names"
        )
    if provider == "codex":
        lines.append("- codex debug models  # live Codex CLI model catalog")
    lines.append("")
    lines.append("Suggested models:")
    for suggestion in _model_suggestion_commands(config):
        lines.append(f"- {suggestion.name} - {suggestion.description}")
    return "\n".join(lines)


def _fallback_status_text(config: LibreClawConfig) -> str:
    lines = [
        "Provider fallback chain",
        f"Enabled: {config.fallback.enabled and bool(config.fallback.routes)}",
        f"Primary: {_canonical_tui_provider(config.general.default_provider)}:{_effective_model(config)}",
        f"Recheck primary after: {config.fallback.recheck_after_attempts} fallback provider call(s)",
    ]
    if not config.fallback.routes:
        lines.append("Fallback slots: none")
        return "\n".join(lines)
    lines.append("Fallback slots:")
    for index, route in enumerate(config.fallback.routes[:3], start=1):
        suffix = f" via {route.api_key_env}" if route.api_key_env else ""
        lines.append(f"{index}. {_canonical_tui_provider(route.provider)}:{route.model}{suffix}")
    return "\n".join(lines)


def _fallback_help_text(config: LibreClawConfig) -> str:
    lines = [
        _fallback_status_text(config),
        "",
        "Commands:",
        "/fallback set 1 openrouter:openrouter/auto --global",
        "/fallback set 2 ollama:kimi-k2.6:cloud --key-env OLLAMA_BACKUP_API_KEY --global",
        "/fallback set 3 anthropic:claude-sonnet-5 --global",
        "/fallback clear 2 --global",
        "/fallback clear all --global",
        "/fallback recheck 3 --global",
        "",
        "Libre Claw tries fallback slots in order only when the active provider fails before producing text or tool calls.",
    ]
    return "\n".join(lines)


def _provider_help_text(config: LibreClawConfig) -> str:
    provider = _canonical_tui_provider(config.general.default_provider)
    lines = [
        f"Current provider: {provider}",
        (
            "Use `/provider anthropic|openai|openrouter|moonshot|ollama|codex`, "
            + "or use `/model <provider>:<name>` to switch both."
        ),
        "For Codex/ChatGPT auth, run `/codex login` then `/provider codex`.",
    ]
    return "\n".join(lines)


def _setup_help_text() -> str:
    return "\n".join(
        [
            "Libre Claw first-run setup:",
            "/setup status",
            "/setup provider anthropic|openai|openrouter|moonshot|ollama|codex",
            "/setup key anthropic|openai|openrouter|moonshot|ollama",
            "/setup model <provider>:<model> [--global]",
            "/setup openrouter",
            "/setup moonshot",
            "/setup ollama-cloud",
            "/setup codex",
            "",
            "API keys entered through `/setup key` are hidden and stored through the same keyring/encrypted-file path as `libre-claw auth set-key`.",
        ]
    )


def _setup_first_run_hint() -> str:
    return (
        "First-run setup is available inside the TUI. Try `/setup status`, "
        "`/setup provider openrouter`, or `/setup key openrouter`."
    )


def _provider_api_key_env(provider_config: object) -> str | None:
    if isinstance(provider_config, dict):
        value = provider_config.get("api_key_env")
        if isinstance(value, str):
            return value
    return None


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


def _parse_skills_command(argument: str) -> dict[str, object]:
    tokens = shlex.split(argument)
    if not tokens:
        return {"action": "list"}
    action = tokens.pop(0).lower()
    scope: SkillScope | None = None
    if tokens and tokens[0] in {"--bundled", "--external", "--user", "--project"}:
        raw_scope = tokens.pop(0)
        scope = raw_scope.removeprefix("--")  # type: ignore[assignment]

    if action in {"list", "ls"}:
        return {"action": "list"}
    if action in {"sync", "refresh"}:
        return {"action": "sync"}
    if action == "show":
        if not tokens:
            raise SkillError("Usage: /skills show [--bundled|--external|--user|--project] <name>")
        return {"action": "show", "scope": scope, "name": tokens[0]}
    if action == "add":
        if scope in {"bundled", "external"}:
            raise SkillError("Bundled and external skills are read-only; add a user or project override.")
        if not tokens:
            raise SkillError("Usage: /skills add [--user|--project] <name> [markdown]")
        name = tokens.pop(0)
        return {"action": "add", "scope": scope or "user", "name": name, "content": " ".join(tokens)}
    if action == "edit":
        if scope in {"bundled", "external"}:
            raise SkillError("Bundled and external skills are read-only; edit a user or project override.")
        if len(tokens) < 2:
            raise SkillError("Usage: /skills edit [--user|--project] <name> <markdown>")
        name = tokens.pop(0)
        return {"action": "edit", "scope": scope, "name": name, "content": " ".join(tokens)}
    if action in {"delete", "del", "rm"}:
        if scope in {"bundled", "external"}:
            raise SkillError("Bundled and external skills are read-only.")
        if not tokens:
            raise SkillError("Usage: /skills delete [--user|--project] <name>")
        return {"action": "delete", "scope": scope, "name": tokens[0]}
    raise SkillError(_skills_help_text())


def _skills_help_text() -> str:
    return "\n".join(
        [
            "Usage:",
            "/skills list",
            "/skills sync",
            "/skills show [--bundled|--external|--user|--project] <name>",
            "/skills add [--user|--project] <name> [markdown]",
            "/skills edit [--user|--project] <name> <markdown>",
            "/skills delete [--user|--project] <name>",
            "",
            "Bundled and external skills are read-only. Add a user or project skill with the same workflow to customize behavior.",
        ]
    )


def _skills_list_text(skills: list[Skill]) -> str:
    if not skills:
        return "No skills found. Add one with `/skills add <name> <markdown>`."
    return "\n".join(
        f"{skill.scope}:{skill.name} - {skill.title} ({skill.path})"
        for skill in skills
    )


def _parse_schedule_command(argument: str) -> dict[str, object]:
    stripped = argument.strip()
    if not stripped:
        return {"action": "list"}
    parts = stripped.split(maxsplit=1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action in {"list", "ls"}:
        return {"action": "list"}
    if action == "examples":
        return {"action": "examples"}
    if action in {"pause", "resume", "delete", "del", "rm"}:
        if not rest:
            raise AutomationError(f"Usage: /schedule {action} <id>")
        return {"action": "delete" if action in {"del", "rm"} else action, "automation_id": rest.split()[0]}
    if action != "add":
        raise AutomationError(_schedule_help_text())

    fields = [field.strip() for field in rest.split("|", maxsplit=2)]
    if len(fields) != 3 or not all(fields):
        raise AutomationError("Usage: /schedule add [--route report|tui|telegram] <schedule> | <name> | <prompt>")

    left_tokens = shlex.split(fields[0])
    route: AutomationRoute = "report"
    cleaned_tokens: list[str] = []
    index = 0
    while index < len(left_tokens):
        token = left_tokens[index]
        if token == "--route":
            if index + 1 >= len(left_tokens):
                raise AutomationError("--route requires report, tui, or telegram.")
            route_text = left_tokens[index + 1].lower()
            if route_text not in {"report", "tui", "telegram"}:
                raise AutomationError("--route must be report, tui, or telegram.")
            route = cast(AutomationRoute, route_text)
            index += 2
            continue
        cleaned_tokens.append(token)
        index += 1

    schedule = " ".join(cleaned_tokens).strip()
    if not schedule:
        raise AutomationError("Schedule is required.")
    return {
        "action": "add",
        "route": route,
        "schedule": schedule,
        "name": fields[1],
        "prompt": fields[2],
    }


def _schedule_help_text() -> str:
    return "\n".join(
        [
            "Usage:",
            "/schedule list",
            "/schedule examples",
            "/schedule add [--route report|tui|telegram] <schedule> | <name> | <prompt>",
            "/schedule pause <id>",
            "/schedule resume <id>",
            "/schedule delete <id>",
            "",
            "Schedules: daily HH:MM, weekly mon HH:MM, every N minutes, hourly, or five-field cron.",
        ]
    )


def _usage_help_text() -> str:
    return "\n".join(
        [
            "Usage:",
            "/usage openrouter",
            "/usage openrouter attribution",
            "/usage openrouter presets",
            "",
            "Shows persistent provider usage from durable runs: tokens, cost, model, run, and user surface.",
        ]
    )


def _schedule_examples_text() -> str:
    lines = ["Schedule examples:"]
    for name, schedule, prompt in automation_examples():
        lines.append(f"/schedule add {schedule} | {name} | {prompt}")
    return "\n".join(lines)


def _automation_list_text(automations: list[dict[str, Any]]) -> str:
    if not automations:
        return "No schedules yet. Try `/schedule examples`."
    return "Schedules:\n" + "\n".join(_automation_line(record) for record in automations)


def _automation_line(record: dict[str, Any]) -> str:
    last_run = record.get("last_run_id") or "never"
    report = record.get("report_path")
    suffix = f" report={report}" if report else ""
    return (
        f"{record.get('automation_id', '')} [{record.get('status', 'unknown')}] "
        f"{record.get('schedule', '')} -> {record.get('route', 'report')} "
        f"next={record.get('next_run_at', 'unknown')} last={last_run} "
        f"{record.get('name', 'Untitled')}{suffix}"
    ).strip()


def _automation_record_payload(record: AutomationRecord) -> dict[str, Any]:
    return {
        "automation_id": record.automation_id,
        "name": record.name,
        "prompt": record.prompt,
        "schedule": record.schedule,
        "route": record.route,
        "status": record.status,
        "provider": record.provider,
        "model": record.model,
        "working_directory": record.working_directory,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "next_run_at": record.next_run_at,
        "last_run_at": record.last_run_at,
        "last_run_id": record.last_run_id,
        "telegram_chat_id": record.telegram_chat_id,
        "report_path": record.report_path,
        "metadata": record.metadata,
        "path": str(record.path),
    }


def _usage_payload(usage: Usage, *, provider: str = "", model: str = "", surface: str = "") -> dict[str, Any]:
    payload = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost": usage.cost,
    }
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    if surface:
        payload["surface"] = surface
    return payload


def _usage_from_payload(payload: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(payload.get("input_tokens", 0) or 0),
        output_tokens=int(payload.get("output_tokens", 0) or 0),
        cached_tokens=int(payload.get("cached_tokens", 0) or 0),
        reasoning_tokens=int(payload.get("reasoning_tokens", 0) or 0),
        cost=payload.get("cost") if isinstance(payload.get("cost"), int | float) else None,
    )


def _object_payload(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def _collect_run_artifacts(
    working_directory: Path,
    state: str,
    events: list[RunEvent],
) -> tuple[str, str, str]:
    browser = browser_artifact_text(events)
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
        return verification, "", browser

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
    return verification, diff, browser


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
    for name in RUN_ARTIFACT_NAMES:
        path = run.path / name
        try:
            size = path.stat().st_size
        except OSError:
            parts.append(f"{name}=missing")
        else:
            parts.append(f"{name}={size}B")
    return ", ".join(parts)


def _parse_artifact_command(argument: str) -> tuple[ArtifactTab, str]:
    tokens = argument.split()
    if not tokens:
        return "summary", ""
    first = tokens[0].lower()
    aliases: dict[str, ArtifactTab] = {
        "plan": "plan",
        "summary": "summary",
        "verify": "verify",
        "verification": "verify",
        "diff": "diff",
        "browser": "browser",
        "screenshots": "browser",
    }
    if first in aliases:
        return aliases[first], " ".join(tokens[1:])
    return "summary", argument.strip()


def _read_artifact_text(run: RunRecord, tab: ArtifactTab) -> str:
    name = {
        "plan": "plan.md",
        "summary": "summary.md",
        "verify": "verification.md",
        "diff": "diff.patch",
        "browser": "browser.md",
    }[tab]
    path = run.path / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _max_event_id(events: list[RunEvent]) -> int:
    return max((event.event_id for event in events), default=0)


def _read_last_seen_event_id(run: RunRecord) -> int:
    path = run.path / "last_seen.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    try:
        return int(payload.get("event_id", 0))
    except (TypeError, ValueError):
        return 0


def _write_last_seen_event_id(run: RunRecord, event_id: int) -> None:
    path = run.path / "last_seen.json"
    path.write_text(json.dumps({"event_id": event_id}, sort_keys=True) + "\n", encoding="utf-8")


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
            result_metadata = event.data.get("metadata", {})
            if not isinstance(result_metadata, dict):
                result_metadata = {}
            arguments = event.data.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            name = str(event.data.get("name", "tool"))
            is_error = bool(event.data.get("is_error"))
            title = _tool_timeline_title(
                name,
                is_error=bool(event.data.get("is_error")),
                metadata=result_metadata,
            )
            content = str(event.data.get("content", ""))
            collapsed = True
            entry_metadata: dict[str, Any] = {
                "status": "error" if is_error else "result",
                "tool": name,
                "metadata": result_metadata,
                "arguments": arguments,
            }
            if (
                name in {"edit_file", "write_file"}
                and not is_error
                and bool(result_metadata.get("changed"))
                and "before" in result_metadata
                and "after" in result_metadata
            ):
                path = str(result_metadata.get("path", arguments.get("path", "file")))
                content = "\n".join(
                    difflib.unified_diff(
                        str(result_metadata.get("before", "")).splitlines(),
                        str(result_metadata.get("after", "")).splitlines(),
                        fromfile=f"{path} before",
                        tofile=f"{path} after",
                        lineterm="",
                    )
                )
                stats = _file_change_stats(content)
                verb = "CREATE" if result_metadata.get("created") else "EDIT"
                title = f"{verb} {Path(path).name or path} · {stats.compact}"
                entry_metadata.update(
                    {
                        "syntax": "diff",
                        "change_summary": f"{stats.compact} · Ctrl+E review",
                        "change_stats": {
                            "additions": stats.additions,
                            "deletions": stats.deletions,
                            "hunks": stats.hunks,
                        },
                    }
                )
                collapsed = len(content.splitlines()) > 80
            entry = TranscriptEntry(
                role="tool",
                content=content,
                title=title,
                collapsed=collapsed,
                metadata=entry_metadata,
            )
            if index is None or index >= len(entries):
                entries.append(entry)
            else:
                entries[index] = entry
            continue
        if event.type in {"error", "cancelled", "goal_judge", "goal_complete", "goal_stopped"}:
            entries.append(TranscriptEntry(role="system", content=_run_event_summary(event)))

    diff_entries = [entry for entry in entries if entry.role == "tool" and (entry.metadata or {}).get("syntax") == "diff"]
    for entry in diff_entries[:-1]:
        entry.collapsed = True
    return entries


def _last_assistant_index(entries: list[TranscriptEntry]) -> int | None:
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].role == "assistant":
            return index
    return None


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


def _fallback_suggestion_commands(config: LibreClawConfig) -> list[SlashCommand]:
    presets = _model_suggestion_commands(config)
    route_examples = [
        SlashCommand("/fallback list", "/fallback list", "Show fallback slots"),
        SlashCommand("/fallback clear all --global", "/fallback clear all --global", "Disable all fallback slots globally"),
        SlashCommand("/fallback recheck 3 --global", "/fallback recheck 3 --global", "Retry primary after 3 fallback provider calls"),
    ]
    for slot, suggestion in enumerate(presets[:3], start=1):
        model = suggestion.name.removeprefix("/model ").strip()
        route_examples.append(
            SlashCommand(
                f"/fallback set {slot} {model} --global",
                f"/fallback set {slot} {model} --global",
                f"Use {suggestion.description} as fallback {slot}",
            )
        )
    return route_examples


def _bounded_menu_index(index: int, size: int) -> int:
    if size <= 0:
        return 0
    return max(0, min(index, size - 1))


def _rich_log_selection_text(lines: Sequence[Any]) -> str:
    return "\n".join(str(getattr(line, "text", line)).rstrip() for line in lines)


def _menu_line(command: SlashCommand, *, selected: bool, usage_width: int) -> str:
    marker = ">" if selected else " "
    return f"{marker} {command.usage:<{usage_width}} {command.description}"


def _theme_suggestion_commands() -> list[SlashCommand]:
    return [
        SlashCommand(
            name=f"/theme {theme_id}",
            usage=f"/theme {theme_id} [--global]",
            description=palette.label,
        )
        for theme_id, palette in THEME_PALETTES.items()
    ]


def _resolve_theme_id(value: str) -> str | None:
    normalized = value.strip().lower()
    theme_id = THEME_ALIASES.get(normalized, normalized)
    if theme_id in THEME_PALETTES:
        return theme_id
    return None


def _theme_help_text(current_theme: str) -> str:
    current_theme_id = normalize_theme(current_theme)
    lines = [
        "Libre Claw themes",
        f"Current: {THEME_PALETTES[current_theme_id].label} (`{current_theme_id}`)",
        "",
        "Use `/theme <name>` to switch this TUI session.",
        "Use `/theme <name> --global` to save the TUI and dashboard default.",
        "",
        "Available themes:",
    ]
    lines.extend(f"- `{theme_id}` - {palette.label}" for theme_id, palette in THEME_PALETTES.items())
    lines.extend(
        [
            "",
            "Aliases: `dark`, `default`, and `libre-default` = `lobster`; "
            "`clear` and `lobster-clear` = `lobster-light`; `light` = `github-light`.",
        ]
    )
    return "\n".join(lines)


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


def _short_run_id(run_id: str, max_length: int = 18) -> str:
    if len(run_id) <= max_length:
        return run_id
    return f"{run_id[:8]}…{run_id[-6:]}"


def _file_change_stats(diff: str) -> FileChangeStats:
    additions = 0
    deletions = 0
    hunks = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return FileChangeStats(additions=additions, deletions=deletions, hunks=hunks)


def _lexer_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".css": "css",
        ".html": "html",
        ".js": "javascript",
        ".json": "json",
        ".md": "markdown",
        ".py": "python",
        ".sh": "bash",
        ".toml": "toml",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix, "text")


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
        return "#3b82f6"
    return "#FF5C5C"


def _tool_timeline_title(name: str, *, is_error: bool, metadata: dict[str, Any]) -> str:
    status = "error" if is_error else "result"
    bits = [name, status]
    exit_code = metadata.get("exit_code")
    duration_ms = metadata.get("duration_ms")
    if exit_code is not None:
        bits.append(f"exit={exit_code}")
    if duration_ms is not None:
        bits.append(f"{duration_ms}ms")
    return " ".join(str(bit) for bit in bits)


def _tool_preview(entry: TranscriptEntry, max_length: int = 120) -> str:
    metadata = entry.metadata or {}
    if metadata.get("syntax") == "diff":
        preview = str(metadata.get("change_summary") or "diff ready · Ctrl+E review")
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


def _startup_renderable(expanded: bool, accent: str = ASSISTANT_ACCENT, *, light: bool = False) -> RenderableType:
    banner = Text(STARTUP_ASCII.strip(), style=accent)
    if not expanded:
        return Group(
            banner,
            Text(PROJECT_LINKS, style="dim"),
            Text(
                f"Libre Claw v{__version__} - release notes collapsed. Press Ctrl+R to expand.",
                style="dim",
            ),
            Text(PROJECT_NOTICE, style="dim"),
        )
    return Group(
        banner,
        Text(PROJECT_LINKS, style="dim"),
        Text(f"Libre Claw v{__version__}", style=f"bold {accent}"),
        Text(PROJECT_NOTICE, style="dim"),
        _lobster_markdown(latest_release_notes(), light=light),
        Text("Press Ctrl+R to collapse. Type /help for commands.", style="dim"),
    )


def _startup_message() -> str:
    return f"{STARTUP_ASCII.strip()}\n\n{PROJECT_LINKS}\n\n{latest_release_notes()}\n\nType /help for commands."


def _parse_tui_image_input(text: str, working_directory: Path) -> ParsedTUIInput:
    """Extract image paths/data URLs from a TUI input line."""
    path_matches = _find_tui_image_path_matches(text, working_directory)
    path_spans: list[tuple[int, int]] = []
    path_attachments: list[UserAttachment] = []
    warnings: list[str] = []

    for start, end, image_path in path_matches:
        path_spans.append((start, end))
        attachment, warning = _load_tui_image_attachment(image_path)
        if attachment is None:
            warnings.append(warning or f"Could not attach image: {image_path}")
            continue
        path_attachments.append(attachment)

    if path_spans:
        text = _remove_tui_text_spans(text, path_spans)

    tokens = _split_tui_input_tokens(text)
    if not tokens:
        return ParsedTUIInput(attachments=tuple(path_attachments), warnings=tuple(warnings))

    message_tokens: list[str] = []
    attachments: list[UserAttachment] = list(path_attachments)

    for token in tokens:
        data_attachment, data_warning = _attachment_from_data_url(token)
        if data_attachment is not None:
            attachments.append(data_attachment)
            continue
        if data_warning is not None:
            warnings.append(data_warning)
            continue

        image_path = _resolve_tui_image_path(token, working_directory)
        if image_path is None:
            if _looks_like_tui_image_reference(token):
                warnings.append(f"Image path not found or unsupported: {token}")
            else:
                message_tokens.append(token)
            continue

        attachment, warning = _load_tui_image_attachment(image_path)
        if attachment is None:
            warnings.append(warning or f"Could not attach image: {image_path}")
            continue
        attachments.append(attachment)

    return ParsedTUIInput(
        message=" ".join(message_tokens).strip(),
        attachments=tuple(attachments),
        warnings=tuple(warnings),
    )


def _find_tui_image_path_matches(text: str, working_directory: Path) -> list[tuple[int, int, Path]]:
    lowered = text.lower()
    matches: list[tuple[int, int, Path]] = []
    consumed_until = 0

    for start in _tui_path_candidate_starts(text):
        if start < consumed_until:
            continue

        best: tuple[int, int, Path] | None = None
        for extension in TUI_IMAGE_EXTENSIONS:
            search_from = start
            while True:
                extension_index = lowered.find(extension, search_from)
                if extension_index == -1:
                    break
                end = extension_index + len(extension)
                candidate = text[start:end].strip("'\"")
                image_path = _resolve_tui_image_path(candidate, working_directory)
                if image_path is not None:
                    best = (start, end, image_path)
                search_from = extension_index + 1

        if best is not None:
            matches.append(best)
            consumed_until = best[1]

    return matches


def _tui_path_candidate_starts(text: str) -> tuple[int, ...]:
    starts: list[int] = []
    index = 0
    while index < len(text):
        previous_is_boundary = index == 0 or text[index - 1].isspace() or text[index - 1] in {"(", "[", "{"}
        if previous_is_boundary and text.startswith("file://", index):
            starts.append(index)
            index += len("file://")
            continue
        if previous_is_boundary and text[index] == "/":
            starts.append(index)
        if previous_is_boundary and text[index] == "~" and index + 1 < len(text) and text[index + 1] == "/":
            starts.append(index)
        index += 1
    return tuple(starts)


def _remove_tui_text_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return " ".join("".join(parts).split())


def _split_tui_input_tokens(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _attachment_from_data_url(token: str) -> tuple[UserAttachment | None, str | None]:
    if not token.startswith("data:image/"):
        return None, None
    try:
        header, encoded = token.split(",", 1)
    except ValueError:
        return None, "Image data URL is missing the base64 payload."
    if ";base64" not in header:
        return None, "Only base64 image data URLs are supported."
    media_type = header.removeprefix("data:").split(";", 1)[0]
    if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return None, f"Unsupported image media type: {media_type}"
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return None, "Image data URL is not valid base64."
    if len(raw) > TUI_IMAGE_ATTACHMENT_MAX_BYTES:
        return None, (
            f"Image attachment is too large: {_format_bytes(len(raw))} > "
            f"{_format_bytes(TUI_IMAGE_ATTACHMENT_MAX_BYTES)}"
        )
    extension = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(media_type, "img")
    return UserAttachment(
        media_type=media_type,
        data=base64.b64encode(raw).decode("ascii"),
        filename=f"pasted-image.{extension}",
    ), None


def _resolve_tui_image_path(token: str, working_directory: Path) -> Path | None:
    candidate = token.strip()
    if not candidate:
        return None
    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        candidate = unquote(parsed.path)
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = working_directory / path
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if not resolved.is_file():
        return None
    if resolved.suffix.lower() not in TUI_IMAGE_EXTENSIONS:
        return None
    return resolved


def _looks_like_tui_image_reference(token: str) -> bool:
    if token.startswith("file://") or token.startswith("data:image/"):
        return True
    try:
        suffix = Path(token).suffix.lower()
    except Exception:
        return False
    return suffix in TUI_IMAGE_EXTENSIONS


def _load_tui_image_attachment(path: Path) -> tuple[UserAttachment | None, str | None]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"Could not inspect image {path}: {exc}"
    if size > TUI_IMAGE_ATTACHMENT_MAX_BYTES:
        return None, (
            f"Image attachment is too large: {_format_bytes(size)} > "
            f"{_format_bytes(TUI_IMAGE_ATTACHMENT_MAX_BYTES)}"
        )
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return None, f"Unsupported image media type for {path}: {media_type or 'unknown'}"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"Could not read image {path}: {exc}"
    return UserAttachment(
        media_type=media_type,
        data=base64.b64encode(raw).decode("ascii"),
        filename=path.name,
        path=str(path),
    ), None


def _load_tui_clipboard_image(target_dir: Path) -> tuple[UserAttachment | None, str | None]:
    try:
        from PIL import Image, ImageGrab
    except Exception as exc:
        return None, f"Clipboard image support requires Pillow/ImageGrab: {exc}"

    try:
        clipboard = ImageGrab.grabclipboard()
    except Exception as exc:
        return None, f"Could not read an image from the OS clipboard: {exc}"

    if clipboard is None:
        return None, (
            "Clipboard does not contain an image. Copy an image, drag an image path "
            "into the terminal, or use `/attach <image-path>`."
        )

    if isinstance(clipboard, Image.Image):
        return _save_clipboard_image(clipboard, target_dir)

    if isinstance(clipboard, list):
        for item in clipboard:
            path = Path(str(item)).expanduser()
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved.is_file() and resolved.suffix.lower() in TUI_IMAGE_EXTENSIONS:
                return _load_tui_image_attachment(resolved)
        return None, "Clipboard contains files, but none are supported images."

    return None, "Clipboard content is not an image."


def _save_clipboard_image(image: Any, target_dir: Path) -> tuple[UserAttachment | None, str | None]:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"clipboard-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
    try:
        if getattr(image, "mode", "") not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        image.save(path, format="PNG")
    except Exception as exc:
        return None, f"Could not save clipboard image: {exc}"
    return _load_tui_image_attachment(path)


def _attachment_metadata(attachment: UserAttachment) -> dict[str, str]:
    payload: dict[str, str] = {"media_type": attachment.media_type}
    if attachment.filename:
        payload["filename"] = attachment.filename
    if attachment.path:
        payload["path"] = attachment.path
    return payload


def _attachment_summary(attachment: UserAttachment) -> str:
    lines = [
        f"type: {attachment.media_type}",
        f"size: {_format_bytes(_attachment_byte_count(attachment))}",
    ]
    if attachment.path:
        lines.append(f"path: {attachment.path}")
    return "\n".join(lines)


def _attachment_renderable(entry: TranscriptEntry, *, accent: str) -> RenderableType:
    metadata = entry.metadata or {}
    attachment = _attachment_from_metadata(metadata.get("attachment"))
    title = entry.title or "image"
    header = Text(title, style=f"bold {accent}")
    summary = Text(entry.content, style="dim")
    if attachment is None:
        return Group(header, summary)

    preview = _attachment_preview_renderable(attachment)
    if preview is None:
        hint = Text("Preview unavailable here; attachment will still be sent to the model.", style="dim")
        return Group(header, summary, hint)
    return Group(header, preview, summary)


def _attachment_from_metadata(value: object) -> UserAttachment | None:
    if not isinstance(value, dict):
        return None
    media_type = value.get("media_type")
    data = value.get("data")
    if not isinstance(media_type, str) or not isinstance(data, str):
        return None
    filename = value.get("filename")
    path = value.get("path")
    return UserAttachment(
        media_type=media_type,
        data=data,
        filename=filename if isinstance(filename, str) else "",
        path=path if isinstance(path, str) else "",
    )


def _attachment_preview_renderable(attachment: UserAttachment) -> RenderableType | None:
    if not attachment.path:
        return None
    return _image_preview_renderable(Path(attachment.path), max_size=(56, 32))


def _petdex_panel_renderable(*, enabled: bool, accent: str, light: bool) -> RenderableType | None:
    if not enabled:
        return None
    snapshot = _petdex_runtime_snapshot()
    if snapshot is None:
        return None

    header = Text.assemble(
        ("🦞 ", ""),
        ("Petdex", f"bold {accent}"),
        (f" · {snapshot.display_name}", "bold"),
        (f" · {snapshot.state}", "dim"),
    )
    preview = _petdex_sprite_renderable(snapshot.spritesheet_path, snapshot.state, light=light)
    bubble = Text(snapshot.bubble_text, style="dim") if snapshot.bubble_text else Text("Companion ready", style="dim")
    if preview is None:
        return Group(header, bubble)
    return Group(header, preview, bubble)


def _petdex_runtime_snapshot(home: Path | None = None) -> PetdexTUISnapshot | None:
    root = home or Path.home() / ".petdex"
    runtime = root / "runtime"
    active_payload = _read_json_file(root / "active.json")
    active_slug = str(active_payload.get("slug") or active_payload.get("id") or "").strip()
    pet_dir = root / "pets" / active_slug if active_slug else None
    pet_payload = _read_json_file(pet_dir / "pet.json") if pet_dir is not None else {}
    display_name = str(pet_payload.get("displayName") or pet_payload.get("name") or active_slug or "Petdex")
    sprite_name = str(pet_payload.get("spritesheetPath") or "spritesheet.webp")

    spritesheet_path = runtime / "webview" / "spritesheet.webp"
    if not spritesheet_path.is_file() and pet_dir is not None:
        spritesheet_path = pet_dir / sprite_name
    if not spritesheet_path.is_file():
        return None

    state_payload = _read_json_file(runtime / "state.json")
    bubble_payload = _read_json_file(runtime / "bubble.json")
    state = str(state_payload.get("state") or "idle").strip() or "idle"
    bubble_text = str(bubble_payload.get("text") or "").strip()
    return PetdexTUISnapshot(
        display_name=display_name,
        state=state,
        bubble_text=bubble_text,
        spritesheet_path=spritesheet_path,
    )


def _petdex_sprite_renderable(path: Path, state: str, *, light: bool) -> RenderableType | None:
    background = "#fdf6e3" if light else "#111827"
    return _image_preview_renderable(
        path,
        max_size=(22, 14),
        frame_selector=lambda width, height: _petdex_frame_rect(width, height, state),
        background=background,
    )


def _petdex_frame_rect(width: int, height: int, state: str) -> tuple[int, int, int, int] | None:
    columns = 8
    rows = 9
    if width < columns or height < rows or width % columns != 0 or height % rows != 0:
        return None
    frame_width = width // columns
    frame_height = height // rows
    row = _petdex_state_row(state, rows)
    column = _petdex_state_column(state, columns)
    left = column * frame_width
    top = row * frame_height
    return left, top, left + frame_width, top + frame_height


def _petdex_state_row(state: str, rows: int) -> int:
    row_by_state = {
        "idle": 0,
        "waiting": 0,
        "review": 0,
        "running": 1,
        "running-left": 1,
        "running-right": 1,
        "jumping": 2,
        "waving": 3,
        "failed": 4,
    }
    return min(rows - 1, row_by_state.get(state.strip().lower(), 1))


def _petdex_state_column(state: str, columns: int) -> int:
    if state.strip().lower() in {"idle", "waiting", "review"}:
        return 0
    return int(time.monotonic() * 4) % columns


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _image_preview_renderable(
    path: Path,
    *,
    max_size: tuple[int, int],
    frame_selector: Any | None = None,
    background: str = "#111827",
) -> RenderableType | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as image:
            if frame_selector is not None:
                frame = frame_selector(*image.size)
                if frame is not None:
                    image = image.crop(frame)
            image = _image_over_background(image, background)
            image.thumbnail(max_size, _nearest_resampling(Image))
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            text = Text()
            for y in range(0, height, 2):
                for x in range(width):
                    top = image.getpixel((x, y))
                    bottom = image.getpixel((x, min(y + 1, height - 1)))
                    text.append(
                        "▀",
                        style=(
                            f"#{top[0]:02x}{top[1]:02x}{top[2]:02x} "
                            f"on #{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}"
                        ),
                    )
                text.append("\n")
            return text
    except Exception:
        return None


def _image_over_background(image: Any, background: str) -> Any:
    if image.mode == "RGBA" or "transparency" in image.info:
        from PIL import Image

        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, background)
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")
    return image.convert("RGB")


def _nearest_resampling(image_module: Any) -> Any:
    resampling = getattr(image_module, "Resampling", None)
    if resampling is not None:
        return resampling.NEAREST
    return image_module.NEAREST


def _attachment_byte_count(attachment: UserAttachment) -> int:
    try:
        return len(base64.b64decode(attachment.data, validate=True))
    except Exception:
        return 0


def _format_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value} B"


def _permission_label(resolution: PermissionResolution) -> str:
    labels = {
        "allow_once": "approved once",
        "deny": "denied",
        "always_allow_tool": "always allowed for this tool",
        "always_allow_call": "always allowed for this exact command",
    }
    return labels[resolution]


def _memory_items_text(items: list[MemoryItem]) -> str:
    return "\n".join(f"{item.id}: [{item.kind}/{item.scope}] {item.text}" for item in items)


def _memory_texts_with_budget(items: list[MemoryItem], max_tokens: int) -> list[str]:
    budget = max(1, max_tokens) * 4
    selected: list[str] = []
    used = 0
    for item in items:
        text = f"[{item.kind}/{item.scope}] {item.text}"
        cost = len(text)
        if selected and used + cost > budget:
            break
        selected.append(text[:budget])
        used += cost
    return selected


def _memory_summary_text(user_message: str, assistant_text: str) -> str:
    parts = []
    if user_message.strip():
        parts.append("User asked: " + user_message.strip()[:500])
    if assistant_text.strip():
        parts.append("Libre Claw response: " + assistant_text.strip()[:1500])
    return redact_secrets("\n".join(parts).strip())
