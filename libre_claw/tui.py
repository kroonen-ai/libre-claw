"""Terminal User Interface for Libre Claw.

Rich-based TUI with slash commands, streaming output, and a polished experience.
"""

import re
import os
import select
import shutil
import sys
import subprocess
import termios
import tty
import time
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .agent import Agent, AgentMode
from .backends import Message
from .config import Config, DEFAULT_CODEX_MODELS

# Custom theme
THEME = Theme({
    "user": "bold cyan",
    "assistant": "green",
    "system": "dim yellow",
    "error": "bold red",
    "command": "bold magenta",
    "info": "dim",
    "accent": "bold blue",
})


class TUI:
    """Rich-based terminal user interface for Libre Claw."""

    COMMANDS = {
        "help": "Show available commands",
        "clear": "Clear conversation history",
        "info": "Show session information",
        "memory": "Search long-term memory (usage: /memory <query>)",
        "heartbeat": "Trigger a manual heartbeat tick",
        "proactive": "Show/start/stop proactive loop (usage: /proactive [start|stop|status])",
        "mode": "Show or switch mode (usage: /mode [direct|heartbeat])",
        "backend": "Show or switch backend (usage: /backend [claude_code|codex_cli|openai_codex|anthropic|openai|ollama])",
        "login": "Bind OpenAI via Codex OAuth (usage: /login openai)",
        "model": "Show/set model for current backend (usage: /model [model-id])",
        "models": "List available models for current backend",
        "context": "Show loaded workspace context files + context window usage",
        "compact": "Compact conversation history (keeps recent turns)",
        "approval": "Edit approval mode (usage: /approval [ask|always|never|status])",
        "verbose": "Show detailed auto-apply logs (usage: /verbose [on|off|status])",
        "quiet": "Silence auto-apply logs (usage: /quiet [on|off|status])",
        "daily": "Append to today's daily note (usage: /daily <text>)",
        "files": "List workspace files",
        "read": "Read a workspace file (usage: /read <filename>)",
        "cost": "Show token usage and cost estimate",
        "quit": "Exit Libre Claw",
    }

    CONTEXT_WINDOW_FALLBACK_TOKENS = 32768
    CONTEXT_WARNING_RATIO = 0.75
    CONTEXT_CRITICAL_RATIO = 0.9
    AUTO_APPLY_MAX_ATTEMPTS = 3
    PASTE_SUMMARY_MIN_LINES = 3
    PASTE_SUMMARY_MIN_CHARS = 150
    COMPOSER_HINT = " Composer · Enter=send · Shift+Enter/Ctrl+J=new line · Ctrl+U=clear "
    COMPOSER_MIN_WIDTH = 56
    COMPOSER_MAX_WIDTH = 120
    CONTEXT_HINT_MODEL_WINDOWS = [
        ("gpt-5", 200000),
        ("gpt-4.1", 200000),
        ("gpt-4o", 128000),
        ("gpt-4", 128000),
        ("gpt-3.5", 16384),
        ("claude-3", 200000),
        ("claude", 200000),
        ("llama3", 32768),
        ("llama-3", 32768),
        ("llama2", 4096),
        ("gemma", 8192),
        ("mistral", 32768),
        ("qwen", 32768),
        ("mixtral", 32768),
        ("deepseek", 64000),
        ("codex", 200000),
        ("o1", 200000),
    ]

    GIT_STATUS_LABELS = {
        "??": "untracked",
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "U": "merge_conflict",
    }

    def __init__(self, agent: Agent, config: Optional[Config] = None):
        self.agent = agent
        self.config = config or Config()
        self.console = Console(theme=THEME)
        self._running = False
        self._start_time = datetime.now()
        self._message_count = 0
        self._approval_mode = "ask"  # ask | always | never
        self._auto_apply_verbose = bool(self.config.heartbeat.auto_apply_verbose)
        self._stdin_buffer = b""
        self._last_edit_proposal: Optional[tuple[str, str]] = None

    def _workspace_config_path(self) -> Path:
        return self.agent.workspace.path / "config.yaml"

    def _save_user_config(self) -> None:
        target = self._workspace_config_path()
        self.config.save(target)

    def _render_banner(self) -> None:
        """Render the startup banner."""
        info = self.agent.get_session_info()
        logo_left = [
            "                   ",
            "█░░░ █ █▄▄▄ █▄▄▄ █▀▀▄",
            "█░░░ █ █▀▀▄ █▀▀▀ █▄▄▀",
            "█▄▄█ █ █▄▄█ █▄▄▄ █ ▀▄",
        ]
        logo_right = [
            "                 ▄     ",
            " █▀▀ █░░░ █▀▀█ █░░░█  ",
            " █░░ █░░░ █▄▄█ ▀█▄█▀  ",
            " ▀▀▀ ▀▀▀▀ ▀░░▀ ░▀░▀   ",
        ]

        logo = Text()
        for left, right in zip(logo_left, logo_right):
            logo.append(left, style="dim")
            logo.append(" ")
            logo.append(right, style="bold cyan")
            logo.append("\n")

        status = Text()
        status.append("● ", style="green")
        status.append(f"Backend: {info['backend']}", style="info")
        status.append("  │  ", style="dim")
        status.append(f"Mode: {info['mode']}", style="info")
        status.append("  │  ", style="dim")
        status.append(self._format_context_usage(), style="info")

        workspace = Text()
        workspace.append("Workspace: ", style="dim")
        workspace.append(str(info["workspace"]), style="info")

        hint = Text()
        hint.append("Type ", style="dim")
        hint.append("/help", style="command")
        hint.append(" for commands, ", style="dim")
        hint.append("/quit", style="command")
        hint.append(" to exit", style="dim")

        body = Text()
        body.append_text(logo)
        body.append("\n")
        body.append_text(status)
        body.append("\n")
        body.append_text(workspace)
        body.append("\n")
        body.append_text(hint)

        self.console.print(
            Panel(
                body,
                border_style="cyan",
                box=box.DOUBLE,
                padding=(1, 2),
                title="[bold cyan]LIBRE CLAW[/bold cyan] [dim]v0.1.0[/dim]",
                subtitle="[dim]Agentic terminal workspace[/dim]",
            )
        )
        self.console.print()

        # Check backend availability
        if hasattr(self.agent.backend, 'check_available'):
            if not self.agent.backend.check_available():
                self.console.print(
                    f"  [error]⚠ Backend '{info['backend']}' is not available![/error]"
                )
                self.console.print(
                    "  [dim]Check that the backend is installed and running.[/dim]"
                )
                self.console.print()

    def _composer_width(self) -> int:
        cols = shutil.get_terminal_size((100, 24)).columns
        return max(self.COMPOSER_MIN_WIDTH, min(cols - 2, self.COMPOSER_MAX_WIDTH))

    def _composer_top_border(self) -> str:
        width = self._composer_width()
        hint = self.COMPOSER_HINT
        if len(hint) > width:
            hint = hint[: max(0, width - 1)] + ("…" if width > 0 else "")
        fill = max(0, width - len(hint))
        return f"╭{hint}{'─' * fill}╮"

    def _composer_bottom_border(self) -> str:
        return f"╰{'─' * self._composer_width()}╯"

    def _handle_command(self, command: str) -> bool:
        """Handle a slash command. Returns True if should continue running."""
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            table = Table(title="Commands", border_style="blue", show_header=False, padding=(0, 2))
            table.add_column("Command", style="command", min_width=12)
            table.add_column("Description")
            for name, desc in self.COMMANDS.items():
                table.add_row(f"/{name}", desc)
            self.console.print(table)

        elif cmd == "clear":
            self.agent.backend.clear_history()
            self._message_count = 0
            self.console.print("  [system]Conversation cleared[/system]")

        elif cmd == "info":
            info = self.agent.get_session_info()
            table = Table(title="Session Info", border_style="blue", show_header=False)
            table.add_column("Key", style="bold")
            table.add_column("Value")
            for k, v in info.items():
                table.add_row(k, str(v))
            table.add_row("messages", str(self._message_count))
            table.add_row("uptime", self._format_uptime())
            self.console.print(table)

        elif cmd == "memory":
            query = args or Prompt.ask("  [cyan]Search query[/cyan]")
            if not query:
                return True
            with self.console.status("Searching memory..."):
                results = self.agent.search_memory(query)
            if results:
                for i, r in enumerate(results, 1):
                    doc = r.get("document", "")[:120]
                    dist = r.get("distance", 0)
                    self.console.print(f"  [dim]{i}.[/dim] ({dist:.3f}) {doc}")
            else:
                self.console.print("  [system]No results found[/system]")

        elif cmd == "heartbeat":
            with self.console.status("Running heartbeat..."):
                response = self.agent.heartbeat_tick()
            self.console.print(Panel(
                Markdown(response),
                title="Heartbeat",
                border_style="yellow",
            ))

        elif cmd == "mode":
            if args:
                mode_str = args.lower().strip()
                if mode_str in ("direct", "heartbeat"):
                    self.agent._set_mode(
                        AgentMode.DIRECT if mode_str == "direct" else AgentMode.HEARTBEAT
                    )
                    self.console.print(f"  [system]Mode set to: {mode_str}[/system]")
                else:
                    self.console.print("  [error]Invalid mode. Use: direct or heartbeat[/error]")
            else:
                self.console.print(f"  Current mode: [bold]{self.agent.state.mode.value}[/bold]")

        elif cmd == "proactive":
            action = (args or "status").strip().lower()
            if action == "start":
                self.agent.start_proactive()
                self.console.print("  [system]Proactive loop started[/system]")
            elif action == "stop":
                self.agent.stop_proactive()
                self.console.print("  [system]Proactive loop stopped[/system]")
            else:
                status = "running" if self.agent.proactive_running else "stopped"
                self.console.print(f"  Proactive loop: [bold]{status}[/bold]")

        elif cmd == "backend":
            if args:
                backend = args.lower().strip()
                allowed = {"claude_code", "codex_cli", "openai_codex", "anthropic", "openai", "ollama"}
                if backend not in allowed:
                    self.console.print("  [error]Invalid backend. Use: claude_code, codex_cli, openai_codex, anthropic, openai, ollama[/error]")
                else:
                    try:
                        self.config.backend.type = backend
                        self._save_user_config()
                        self.agent.switch_backend(backend)
                        self.console.print(f"  [system]Backend switched to: {backend}[/system]")
                    except Exception as e:
                        self.console.print(f"  [error]Failed to switch backend: {e}[/error]")
            else:
                self.console.print(f"  Current backend: [bold]{self.agent.backend.name}[/bold]")

        elif cmd == "login":
            provider = (args or "").strip().lower()
            if provider != "openai":
                self.console.print("  [error]Usage: /login openai[/error]")
            else:
                # OpenClaw-like behavior: OAuth login means Codex backend, no token paste flow.
                try:
                    codex_bin = self.config.backend.codex_path or "codex"
                    status = subprocess.run([codex_bin, "login", "status"], capture_output=True, text=True, timeout=10)
                    if status.returncode != 0:
                        self.console.print("  [error]Codex OAuth not active. Run: codex login[/error]")
                        return True

                    self.config.backend.type = "openai_codex"
                    self.config.backend.openai_codex_base_url = "https://chatgpt.com/backend-api"
                    self._save_user_config()
                    self.agent.switch_backend("openai_codex")
                    self.console.print("  [system]Codex OAuth active. Backend set to: openai_codex[/system]")
                except Exception as e:
                    self.console.print(f"  [error]Codex login check failed: {e}[/error]")
        elif cmd == "model":
            backend = self.config.backend.type
            if not args:
                if backend == "openai":
                    current = self.config.backend.openai_model
                    self.console.print(f"  Current model: [bold]{current}[/bold] (openai)")
                    if hasattr(self.agent.backend, "list_models"):
                        models = self.agent.backend.list_models()
                        if models:
                            shown = models[:30]
                            for i, m in enumerate(shown, 1):
                                self.console.print(f"  [dim]{i:>2}.[/dim] {m}")
                            pick = Prompt.ask("  Pick model number (or Enter to keep current)", default="").strip()
                            if pick:
                                try:
                                    idx = int(pick)
                                    if 1 <= idx <= len(shown):
                                        chosen = shown[idx - 1]
                                        self.config.backend.openai_model = chosen
                                        self._save_user_config()
                                        self.agent.switch_backend(backend)
                                        self.console.print(f"  [system]Saved model '{chosen}' for backend {backend}[/system]")
                                    else:
                                        self.console.print("  [error]Invalid model number[/error]")
                                except ValueError:
                                    self.console.print("  [error]Please enter a number[/error]")
                        else:
                            self.console.print("  [system]Could not fetch model list; use /model <model-id>[/system]")
                elif backend == "anthropic":
                    self.console.print(f"  Current model: [bold]{self.config.backend.anthropic_model}[/bold] (anthropic)")
                elif backend == "ollama":
                    self.console.print(f"  Current model: [bold]{self.config.backend.ollama_model}[/bold] (ollama)")
                elif backend == "codex_cli":
                    model = self.config.backend.codex_model or "(codex default)"
                    self.console.print(f"  Current model: [bold]{model}[/bold] (codex_cli)")
                    self.console.print("  [dim]Set explicit override with: /model <model-id>[/dim]")
                elif backend == "openai_codex":
                    model = self.config.backend.openai_codex_model
                    self.console.print(f"  Current model: [bold]{model}[/bold] (openai_codex)")
                    for i, m in enumerate(DEFAULT_CODEX_MODELS, 1):
                        self.console.print(f"  [dim]{i:>2}.[/dim] {m}")
                    pick = Prompt.ask("  Pick model number (or Enter to keep current)", default="").strip()
                    if pick:
                        try:
                            idx = int(pick)
                            if 1 <= idx <= len(DEFAULT_CODEX_MODELS):
                                chosen = DEFAULT_CODEX_MODELS[idx - 1]
                                self.config.backend.openai_codex_model = chosen
                                self._save_user_config()
                                self.agent.switch_backend(backend)
                                self.console.print(f"  [system]Saved model '{chosen}' for backend {backend}[/system]")
                            else:
                                self.console.print("  [error]Invalid model number[/error]")
                        except ValueError:
                            self.console.print("  [error]Please enter a number[/error]")
                else:
                    self.console.print(f"  Backend [bold]{backend}[/bold] does not expose a model selector")
            else:
                model = args.strip()
                if backend == "openai":
                    self.config.backend.openai_model = model
                elif backend == "anthropic":
                    self.config.backend.anthropic_model = model
                elif backend == "ollama":
                    self.config.backend.ollama_model = model
                elif backend == "codex_cli":
                    self.config.backend.codex_model = model
                elif backend == "openai_codex":
                    self.config.backend.openai_codex_model = model
                else:
                    self.console.print(f"  [error]Cannot set model for backend: {backend}[/error]")
                    return True

                self._save_user_config()
                try:
                    self.agent.switch_backend(backend)
                except Exception:
                    pass
                self.console.print(f"  [system]Saved model '{model}' for backend {backend}[/system]")

        elif cmd == "models":
            backend = self.config.backend.type
            if backend in {"codex_cli", "openai_codex"}:
                for m in DEFAULT_CODEX_MODELS:
                    self.console.print(f"  [dim]•[/dim] {m}")
            elif hasattr(self.agent.backend, "list_models"):
                models = self.agent.backend.list_models()
                if models:
                    for m in models[:100]:
                        self.console.print(f"  [dim]•[/dim] {m}")
                else:
                    self.console.print(f"  [system]No model list available for {backend} right now[/system]")
            else:
                self.console.print(f"  [system]Backend {backend} does not support listing models[/system]")

        elif cmd == "context":
            ctx = self.agent.workspace.get_context(self.agent.state.mode.value)
            if ctx:
                for filename in ctx:
                    size = len(ctx[filename])
                    self.console.print(f"  [dim]●[/dim] {filename} ({size:,} chars)")
            else:
                self.console.print("  [system]No context files loaded[/system]")
            self.console.print(f"  [system]{self._format_context_usage()}[/system]")
            usage = self._parse_context_usage()
            if usage["level"] in {"warn", "critical"}:
                self.console.print("  [error]Context window is getting tight. Consider compacting history soon.[/error]")

        elif cmd == "compact":
            history = self.agent.backend.get_history()
            keep = 12
            if len(history) <= keep:
                self.console.print("  [system]Nothing to compact[/system]")
            else:
                dropped = len(history) - keep
                kept = history[-keep:]
                summary_text = self._build_compaction_summary(history[:-keep])
                summary_entry = Message(
                    role="assistant",
                    content=f"[Conversation summary]\n{summary_text}",
                )
                self.agent.workspace.write(
                    "CONVERSATION_SUMMARY.md",
                    summary_text,
                )
                self.agent.backend._conversation_history = [summary_entry] + kept
                self.console.print(
                    f"  [system]Compacted history: dropped {dropped} messages, kept {keep} + continuity summary[/system]"
                )
                self.console.print("  [system]Added continuity summary at start of retained history.[/system]")

        elif cmd == "approval":
            mode = (args or "status").strip().lower()
            if mode in {"ask", "always", "never"}:
                self._approval_mode = mode
                self.console.print(f"  [system]Approval mode set to: {mode}[/system]")
            else:
                self.console.print(f"  Current approval mode: [bold]{self._approval_mode}[/bold]")
                self.console.print("  [dim]Modes: ask (default), always (danger), never[/dim]")

        elif cmd == "verbose":
            mode = (args or "status").strip().lower()
            if mode in {"on", "true", "1", "yes"}:
                self._auto_apply_verbose = True
                self.config.heartbeat.auto_apply_verbose = True
                self._save_user_config()
                self.console.print("  [system]Auto-apply verbose logging: [bold]on[/bold][/system]")
            elif mode in {"off", "false", "0", "no"}:
                self._auto_apply_verbose = False
                self.config.heartbeat.auto_apply_verbose = False
                self._save_user_config()
                self.console.print("  [system]Auto-apply verbose logging: [bold]off[/bold][/system]")
            else:
                current = "on" if self._auto_apply_verbose else "off"
                self.console.print(f"  Auto-apply verbose logging: [bold]{current}[/bold]")
                self.console.print("  [dim]Use: /verbose on | /verbose off[/dim]")

        elif cmd == "quiet":
            mode = (args or "status").strip().lower()
            if mode in {"on", "true", "1", "yes"}:
                self._auto_apply_verbose = False
                self.config.heartbeat.auto_apply_verbose = False
                self._save_user_config()
                self.console.print("  [system]Auto-apply verbose logging: [bold]off[/bold] (quiet mode enabled)[/system]")
            elif mode in {"off", "false", "0", "no"}:
                self._auto_apply_verbose = True
                self.config.heartbeat.auto_apply_verbose = True
                self._save_user_config()
                self.console.print("  [system]Auto-apply verbose logging: [bold]on[/bold] (quiet mode disabled)[/system]")
            else:
                current = "off" if not self._auto_apply_verbose else "on"
                self.console.print(f"  Quiet mode: [bold]{current}[/bold] for auto-apply logs")
                self.console.print("  [dim]Use: /quiet on | /quiet off[/dim]")

        elif cmd == "daily":
            if args:
                self.agent.workspace.write_daily_note(f"- {args}")
                self.console.print("  [system]Added to daily note[/system]")
            else:
                self.console.print("  [error]Usage: /daily <text>[/error]")

        elif cmd == "files":
            files = self.agent.workspace.list_files("**/*.md")
            if files:
                for f in files:
                    rel = f.relative_to(self.agent.workspace.path)
                    self.console.print(f"  [dim]●[/dim] {rel}")
            else:
                self.console.print("  [system]No files found[/system]")

        elif cmd == "read":
            if args:
                content = self.agent.workspace.read(args.strip())
                if content:
                    self.console.print(Panel(
                        Markdown(content),
                        title=args.strip(),
                        border_style="blue",
                    ))
                else:
                    self.console.print(f"  [error]File not found: {args.strip()}[/error]")
            else:
                self.console.print("  [error]Usage: /read <filename>[/error]")

        elif cmd == "cost":
            self.console.print(f"  Messages this session: {self._message_count}")
            self.console.print(f"  Uptime: {self._format_uptime()}")
            self.console.print("  [dim]Detailed cost tracking coming in v0.2[/dim]")

        elif cmd in ("quit", "exit", "q"):
            self._running = False
            self.console.print("\n  [system]Goodbye! 💜[/system]\n")
            return False

        else:
            self.console.print(f"  [error]Unknown command: /{cmd}[/error]")
            self.console.print("  [dim]Type /help for available commands[/dim]")

        return True

    def _estimate_context_tokens(self) -> int:
        # lightweight estimate: ~4 chars/token
        ctx = self.agent.workspace.get_context(self.agent.state.mode.value)
        history_text = "\n".join(m.content for m in self.agent.backend.get_history())
        text = "\n".join(ctx.values()) + "\n" + history_text
        return max(1, len(text) // 4)

    def _resolve_current_model_id(self) -> str:
        backend_name = (self.agent.backend.name or "").lower()
        cfg = self.config.backend
        if not cfg:
            return ""

        candidates = {
            "openai_codex": ["openai_codex_model"],
            "openai-codex": ["openai_codex_model"],
            "openai": ["openai_model"],
            "anthropic": ["anthropic_model"],
            "ollama": ["ollama_model"],
            "codex_cli": ["codex_model"],
            "codex-cli": ["codex_model"],
            "codex": ["codex_model"],
            "claude_code": [],
            "claude-code": [],
        }

        for key, attrs in candidates.items():
            if key == backend_name:
                for attr in attrs:
                    value = getattr(cfg, attr, "")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                return ""

        # Fallback for custom/legacy backend names.
        for attr in (
            "openai_model",
            "anthropic_model",
            "ollama_model",
            "codex_model",
            "openai_codex_model",
        ):
            value = getattr(cfg, attr, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _estimate_context_window_tokens(self) -> int:
        model_id = self._resolve_current_model_id().lower()
        if model_id:
            for marker, size in self.CONTEXT_HINT_MODEL_WINDOWS:
                if marker in model_id:
                    return size

        max_tokens = getattr(getattr(self.agent.backend, "config", None), "max_tokens", 4096)
        try:
            max_tokens = int(max_tokens)
        except Exception:
            max_tokens = 4096
        return max(self.CONTEXT_WINDOW_FALLBACK_TOKENS, max_tokens * 8)

    def _parse_context_usage(self) -> dict:
        used = self._estimate_context_tokens()
        budget = self._estimate_context_window_tokens()
        ratio = used / max(1, budget)
        level = "ok"
        if ratio >= self.CONTEXT_CRITICAL_RATIO:
            level = "critical"
        elif ratio >= self.CONTEXT_WARNING_RATIO:
            level = "warn"
        return {
            "used": used,
            "budget": budget,
            "ratio": ratio,
            "level": level,
        }

    def _format_context_usage(self) -> str:
        usage = self._parse_context_usage()
        used = usage["used"]
        budget = usage["budget"]
        pct = int(usage["ratio"] * 100)
        if usage["level"] == "critical":
            return f"Context: {used:,}/{budget:,} tokens ({pct}%) — compact now"
        if usage["level"] == "warn":
            return f"Context: {used:,}/{budget:,} tokens ({pct}%) — nearing limit"
        return f"Context: {used:,}/{budget:,} tokens ({pct}%)"

    def _extract_bash_block(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"```(?:bash|sh)?\n([\s\S]*?)\n?```", text)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _extract_raw_unified_diff_block(text: str) -> Optional[str]:
        if not text:
            return None

        marker = text.find("diff --git")
        if marker < 0:
            return None

        candidate = text[marker:].strip()
        if not candidate:
            return None

        next_marker = candidate.find("\n\ndiff --git", 1)
        if next_marker > 0:
            candidate = candidate[:next_marker]
        return candidate

    @staticmethod
    def _strip_json_command_prefix(text: str) -> str:
        if not text:
            return text

        stripped = text.strip()
        if stripped.startswith("{") and "\"cmd\"" in stripped[:80]:
            close = stripped.find("}")
            if close != -1 and close + 1 < len(stripped):
                return stripped[close + 1 :].strip()
        return text.strip()

    def _extract_diff_block(self, text: str) -> Optional[str]:
        if not text:
            return None

        text = textwrap.dedent(text)
        for match in re.finditer(r"```(?:diff|apply_patch|patch)\n([\s\S]*?)\n?```", text):
            candidate = (match.group(1) or "").strip()
            embedded = self._extract_embedded_apply_patch(candidate)
            if embedded:
                return embedded
            if candidate:
                return candidate

        match = re.search(r"(?ms)^\s*\*\*\* Begin Patch[\s\S]*?^\s*\*\*\* End Patch", text)
        if match:
            return textwrap.dedent(match.group(0)).strip()

        raw = self._extract_raw_unified_diff_block(text)
        if raw:
            return raw

        return None

    def _apply_unified_diff(self, diff_text: str) -> str:
        candidate = textwrap.dedent(self._normalize_unified_diff(diff_text)).strip()
        if not candidate:
            return "FAILED_PERM; next_action=no valid diff; details=No valid diff content found."

        # Prefer the Agent's patch engine first (it has richer retry/repair logic).
        delegated_apply = getattr(self.agent, "_apply_unified_diff", None)
        if callable(delegated_apply):
            try:
                delegated_result = delegated_apply(candidate)
                d_status, d_next_action, d_details = self._parse_apply_contract(delegated_result)
                if d_status == "APPLIED":
                    return delegated_result
                if d_status == "FAILED_PERM" and "apply_patch utility not found" not in (d_details or "").lower():
                    return delegated_result
                if "apply_patch utility not found" in (d_details or "").lower():
                    op_result = self._apply_openai_patch_file_ops(candidate)
                    if op_result:
                        return op_result
                # If delegated strategy was retryable, continue into local fallback strategies below.
            except Exception:
                pass

        embedded = self._extract_embedded_apply_patch(candidate)
        if embedded:
            result = self._apply_apply_patch_block(embedded)
            status, next_action, details = self._parse_apply_contract(result)
            if status == "FAILED_PERM" and "apply_patch utility not found" in (details or "").lower():
                op_result = self._apply_openai_patch_file_ops(embedded)
                if op_result:
                    return op_result
                repaired = self._repair_patch_with_file_context(embedded)
                if repaired:
                    return repaired
                return (
                    "RETRYABLE; next_action=re-read target file context and regenerate patch; "
                    "details=apply_patch utility not found and deterministic repair did not match current file content."
                )
            if status != "RETRYABLE":
                return result

            repaired = self._repair_patch_with_file_context(candidate)
            if repaired:
                return repaired
            return result

        if candidate.lstrip().startswith("*** Begin Patch"):
            result = self._apply_apply_patch_block(candidate)
            status, next_action, details = self._parse_apply_contract(result)
            if status == "FAILED_PERM" and "apply_patch utility not found" in (details or "").lower():
                op_result = self._apply_openai_patch_file_ops(candidate)
                if op_result:
                    return op_result
                repaired = self._repair_patch_with_file_context(candidate)
                if repaired:
                    return repaired
                return (
                    "RETRYABLE; next_action=re-read target file context and regenerate patch; "
                    "details=apply_patch utility not found and deterministic repair did not match current file content."
                )
            if status != "RETRYABLE":
                return result

            repaired = self._repair_patch_with_file_context(candidate)
            if repaired:
                return repaired
            return result
        try:
            p = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                cwd=str(self.agent.workspace.path),
                input=candidate,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if p.returncode == 0:
                return "APPLIED; next_action=none; details=Applied unified diff successfully."
            repaired = self._repair_patch_with_file_context(candidate)
            if repaired:
                return repaired
            return (
                "RETRYABLE; next_action=re-read target file context and regenerate patch; "
                f"details={(p.stderr or p.stdout).strip()[:300]}"
            )
        except Exception as e:
            return f"FAILED_PERM; next_action=retry with a valid patch; details=Diff apply error: {e}"

    def _apply_openai_patch_file_ops(self, patch_text: str) -> Optional[str]:
        if not patch_text or "*** Begin Patch" not in patch_text:
            return None

        lines = textwrap.dedent(patch_text).splitlines()
        if not lines:
            return None

        segments: list[tuple[int, int]] = []
        scan_idx = 0
        while scan_idx < len(lines):
            if lines[scan_idx].strip() != "*** Begin Patch":
                scan_idx += 1
                continue
            end_idx = scan_idx + 1
            while end_idx < len(lines) and lines[end_idx].strip() != "*** End Patch":
                end_idx += 1
            if end_idx >= len(lines):
                break
            segments.append((scan_idx, end_idx))
            scan_idx = end_idx + 1

        if not segments:
            return None

        workspace_root = self.agent.workspace.path.resolve()

        def _resolve(path_text: str) -> tuple[Optional[Path], Optional[str]]:
            rel = (path_text or "").strip().lstrip("/")
            if not rel:
                return None, None
            target = (self.agent.workspace.path / rel).resolve()
            try:
                target.relative_to(workspace_root)
            except ValueError:
                return None, None
            return target, rel

        def _seek_sequence(haystack: list[str], pattern: list[str], start_idx: int) -> int:
            if not pattern:
                return -1
            if len(pattern) > len(haystack):
                return -1
            for i in range(start_idx, len(haystack) - len(pattern) + 1):
                if haystack[i : i + len(pattern)] == pattern:
                    return i
            return -1

        def _normalize_line(value: str) -> str:
            return re.sub(r"\s+", " ", (value or "").strip())

        def _seek_sequence_relaxed(haystack: list[str], pattern: list[str], start_idx: int) -> int:
            if not pattern:
                return -1
            if len(pattern) > len(haystack):
                return -1
            normalized_pattern = [_normalize_line(v) for v in pattern]
            for i in range(start_idx, len(haystack) - len(pattern) + 1):
                normalized_window = [_normalize_line(v) for v in haystack[i : i + len(pattern)]]
                if normalized_window == normalized_pattern:
                    return i
            return -1

        hunks: list[dict] = []
        for begin_idx, end_idx in segments:
            i = begin_idx + 1
            while i < end_idx:
                line = lines[i]
                if line.startswith("*** Add File:"):
                    path_text = line.split(":", 1)[1].strip()
                    i += 1
                    content_lines: list[str] = []
                    while i < end_idx and not lines[i].startswith("***"):
                        if lines[i].startswith("+"):
                            content_lines.append(lines[i][1:])
                        i += 1
                    hunks.append({"type": "add", "path": path_text, "contents": "\n".join(content_lines)})
                    continue

                if line.startswith("*** Delete File:"):
                    path_text = line.split(":", 1)[1].strip()
                    hunks.append({"type": "delete", "path": path_text})
                    i += 1
                    continue

                if line.startswith("*** Update File:"):
                    path_text = line.split(":", 1)[1].strip()
                    i += 1
                    move_path: Optional[str] = None
                    if i < end_idx and lines[i].startswith("*** Move to:"):
                        move_path = lines[i].split(":", 1)[1].strip()
                        i += 1

                    chunks: list[dict] = []
                    while i < end_idx and not lines[i].startswith("***"):
                        if lines[i].startswith("@@"):
                            context_line = lines[i][2:].strip()
                            i += 1
                            old_lines: list[str] = []
                            new_lines: list[str] = []
                            while i < end_idx and not lines[i].startswith("@@") and not lines[i].startswith("***"):
                                change_line = lines[i]
                                if change_line == "*** End of File":
                                    i += 1
                                    break
                                if change_line.startswith(" "):
                                    content = change_line[1:]
                                    old_lines.append(content)
                                    new_lines.append(content)
                                elif change_line.startswith("-"):
                                    old_lines.append(change_line[1:])
                                elif change_line.startswith("+"):
                                    new_lines.append(change_line[1:])
                                i += 1
                            chunks.append(
                                {
                                    "old_lines": old_lines,
                                    "new_lines": new_lines,
                                    "change_context": context_line or None,
                                }
                            )
                            continue
                        i += 1

                    hunks.append({"type": "update", "path": path_text, "move_path": move_path, "chunks": chunks})
                    continue

                i += 1

        if not hunks:
            return None

        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []

        for hunk in hunks:
            hunk_type = hunk.get("type")
            path_text = hunk.get("path", "")
            target, rel = _resolve(path_text)
            if target is None or rel is None:
                return "FAILED_PERM; next_action=provide valid patch; details=Invalid patch target path."

            if hunk_type == "add":
                target.parent.mkdir(parents=True, exist_ok=True)
                content = hunk.get("contents", "")
                if content and not content.endswith("\n"):
                    content += "\n"
                target.write_text(content)
                added.append(rel)
                continue

            if hunk_type == "delete":
                if target.exists():
                    target.unlink()
                deleted.append(rel)
                continue

            if hunk_type == "update":
                if not target.exists():
                    return None

                original_content = target.read_text()
                original_lines = original_content.split("\n")
                if original_lines and original_lines[-1] == "":
                    original_lines.pop()

                replacements: list[tuple[int, int, list[str]]] = []
                line_index = 0
                chunks = hunk.get("chunks", [])

                for chunk in chunks:
                    change_context = chunk.get("change_context")
                    old_lines = list(chunk.get("old_lines", []))
                    new_lines = list(chunk.get("new_lines", []))

                    if change_context:
                        context_idx = _seek_sequence(original_lines, [change_context], line_index)
                        if context_idx == -1:
                            context_idx = _seek_sequence_relaxed(original_lines, [change_context], line_index)
                        if context_idx == -1:
                            return None
                        line_index = context_idx + 1

                    if not old_lines:
                        insert_idx = len(original_lines)
                        replacements.append((insert_idx, 0, new_lines))
                        continue

                    pattern = old_lines
                    new_slice = new_lines
                    found = _seek_sequence(original_lines, pattern, line_index)
                    if found == -1 and pattern and pattern[-1] == "":
                        pattern = pattern[:-1]
                        if new_slice and new_slice[-1] == "":
                            new_slice = new_slice[:-1]
                        found = _seek_sequence(original_lines, pattern, line_index)
                    if found == -1:
                        found = _seek_sequence_relaxed(original_lines, pattern, line_index)

                    if found == -1:
                        return None

                    replacements.append((found, len(pattern), new_slice))
                    line_index = found + len(pattern)

                replacements.sort(key=lambda x: x[0])
                result_lines = list(original_lines)
                for start, old_len, new_segment in reversed(replacements):
                    del result_lines[start : start + old_len]
                    for idx, value in enumerate(new_segment):
                        result_lines.insert(start + idx, value)

                if not result_lines or result_lines[-1] != "":
                    result_lines.append("")
                new_content = "\n".join(result_lines)

                move_path = hunk.get("move_path")
                if move_path:
                    move_target, move_rel = _resolve(move_path)
                    if move_target is None or move_rel is None:
                        return "FAILED_PERM; next_action=provide valid patch; details=Invalid move target path."
                    move_target.parent.mkdir(parents=True, exist_ok=True)
                    move_target.write_text(new_content)
                    if move_target != target and target.exists():
                        target.unlink()
                    modified.append(move_rel)
                else:
                    target.write_text(new_content)
                    modified.append(rel)

        details_parts: list[str] = []
        if added:
            details_parts.append(f"created: {', '.join(added)}")
        if modified:
            details_parts.append(f"updated: {', '.join(modified)}")
        if deleted:
            details_parts.append(f"deleted: {', '.join(deleted)}")
        detail = "; ".join(details_parts) if details_parts else "No-op."
        return f"APPLIED; next_action=none; details=Applied OpenAI file ops fallback. {detail}"

    def _repair_patch_with_file_context(self, diff_text: str) -> Optional[str]:
        def normalize_context_line(line: str) -> str:
            return re.sub(r"\s+", " ", (line or "").strip())

        def is_heading(line: str) -> bool:
            return normalize_context_line(line).startswith("## ")

        def is_table_row(line: str) -> bool:
            normalized = normalize_context_line(line)
            return normalized.startswith("|") and normalized.endswith("|")

        def is_table_separator(line: str) -> bool:
            cells = [cell.strip() for cell in normalize_context_line(line).strip("|").split("|")]
            if not cells:
                return False
            return all((not cell) or bool(re.fullmatch(r"-{3,}", cell)) for cell in cells)

        def is_tableish(context: list[str], added: list[str]) -> bool:
            return any(is_table_row(line) for line in context) or any(is_table_row(line) for line in added)

        def resolve_file(path_text: str) -> Optional[Path]:
            rel = (path_text or "").strip().lstrip("/")
            if not rel:
                return None
            direct = self.agent.workspace.path / rel
            if direct.exists():
                return direct

            requested = Path(rel)
            requested_parts = [part.casefold() for part in requested.parts if part and part != "."]
            if not requested_parts:
                return None

            matches: list[Path] = []
            for candidate in self.agent.workspace.path.rglob("*"):
                if not candidate.is_file():
                    continue
                rel_candidate = candidate.relative_to(self.agent.workspace.path)
                candidate_parts = [part.casefold() for part in rel_candidate.parts]
                if len(candidate_parts) < len(requested_parts):
                    continue
                if candidate_parts[-len(requested_parts) :] == requested_parts:
                    matches.append(candidate)

            if len(matches) == 1:
                return matches[0]

            if matches:
                requested_name = requested.name.casefold()
                for candidate in matches:
                    if candidate.name.casefold() == requested_name:
                        return candidate
            return None

        def find_section_bounds(lines: list[str], context_lines: list[str]) -> tuple[int, int]:
            headings = [normalize_context_line(line) for line in context_lines if is_heading(line)]
            for heading in reversed(headings):
                heading_normalized = heading.casefold()
                for idx, line in enumerate(lines):
                    if normalize_context_line(line).casefold() == heading_normalized:
                        end = len(lines)
                        for j in range(idx + 1, len(lines)):
                            if is_heading(lines[j]):
                                end = j
                                break
                        return idx + 1, end
            if headings:
                # New section headings included in patch but absent in the current file.
                # Append the proposed block at end, preserving the intent to create that section.
                return len(lines), len(lines)
            return 0, len(lines)

        def find_last_context_index(lines: list[str], start: int, end: int, context_lines: list[str]) -> Optional[int]:
            normalized_context = [normalize_context_line(line) for line in context_lines if normalize_context_line(line)]
            if not normalized_context:
                return None
            for target in reversed(normalized_context):
                for idx in range(min(end, len(lines)) - 1, max(start, 0) - 1, -1):
                    if normalize_context_line(lines[idx]) == target:
                        return idx
            return None

        def table_end_from_anchor(lines: list[str], anchor: int, section_end: int) -> int:
            if anchor < 0:
                return 0
            idx = min(max(0, anchor), len(lines))
            if idx >= section_end:
                return min(idx, section_end)
            if not is_table_row(lines[idx]):
                return min(idx + 1, section_end)
            while idx < section_end and is_table_row(lines[idx]):
                idx += 1
            return min(idx, section_end)

        def infer_insert_index(
            content_lines: list[str], context_lines: list[str], added_lines: list[str]
        ) -> tuple[int, tuple[int, int]]:
            section_start, section_end = find_section_bounds(content_lines, context_lines)
            table_mode = is_tableish(context_lines, added_lines)

            if table_mode:
                table_context = [line for line in context_lines if is_table_row(line)]
                anchor = find_last_context_index(content_lines, section_start, section_end, table_context)
                if anchor is not None:
                    return table_end_from_anchor(content_lines, anchor, section_end), (section_start, section_end)

                for idx in range(section_start, section_end):
                    if is_table_row(content_lines[idx]) and idx + 1 < section_end and is_table_separator(content_lines[idx + 1]):
                        return table_end_from_anchor(content_lines, idx + 1, section_end), (section_start, section_end)

            context_idx = find_last_context_index(content_lines, section_start, section_end, context_lines)
            if context_idx is not None:
                return min(context_idx + 1, section_end), (section_start, section_end)

            return section_end, (section_start, section_end)

        def normalize_lines(lines: list[str]) -> list[str]:
            return [normalize_context_line(line) for line in lines]

        def block_exists(content_lines: list[str], block_lines: list[str], section: tuple[int, int]) -> bool:
            if not block_lines:
                return False
            start, end = section
            start = max(0, min(start, len(content_lines)))
            end = max(start, min(end, len(content_lines)))
            target = normalize_lines(block_lines)
            span = len(block_lines)
            if end - start < span:
                return False
            for i in range(start, end - span + 1):
                if normalize_lines(content_lines[i : i + span]) == target:
                    return True
            return False

        def _dedupe_added_table_lines(
            content_lines: list[str],
            section: tuple[int, int],
            added_lines: list[str],
        ) -> list[str]:
            if not added_lines:
                return added_lines

            start, end = section
            start = max(0, min(start, len(content_lines)))
            end = max(start, min(end, len(content_lines)))
            section_slice = content_lines[start:end]
            deduped = [line for line in added_lines if line is not None]
            if not deduped:
                return deduped

            # If a section heading already exists right before the insertion window, drop
            # a duplicate heading line from the patch payload.
            if start > 0 and deduped and is_heading(deduped[0]):
                if normalize_context_line(deduped[0]).casefold() == normalize_context_line(content_lines[start - 1]).casefold():
                    deduped = deduped[1:]

            if len(deduped) >= 2 and is_table_row(deduped[0]) and is_table_separator(deduped[1]):
                target_header = normalize_context_line(deduped[0])
                target_sep = normalize_context_line(deduped[1])
                for idx in range(len(section_slice) - 2):
                    if is_table_row(section_slice[idx]) and is_table_separator(section_slice[idx + 1]):
                        if normalize_context_line(section_slice[idx]) == target_header and normalize_context_line(section_slice[idx + 1]) == target_sep:
                            deduped = deduped[2:]
                            break

            return deduped

        def remove_block(content_lines: list[str], block_lines: list[str], section: tuple[int, int]) -> Optional[list[str]]:
            if not block_lines:
                return None
            start, end = section
            start = max(0, min(start, len(content_lines)))
            end = max(start, min(end, len(content_lines)))
            target = normalize_lines(block_lines)
            span = len(block_lines)
            for i in range(start, end - span + 1):
                if normalize_lines(content_lines[i : i + span]) == target:
                    return content_lines[:i] + content_lines[i + span :]
            return None

        def replace_block(
            content_lines: list[str],
            old_lines: list[str],
            new_lines: list[str],
            section: tuple[int, int],
        ) -> Optional[list[str]]:
            if not old_lines:
                return None
            start, end = section
            start = max(0, min(start, len(content_lines)))
            end = max(start, min(end, len(content_lines)))
            target = normalize_lines(old_lines)
            span = len(old_lines)
            for i in range(start, end - span + 1):
                if normalize_lines(content_lines[i : i + span]) == target:
                    return content_lines[:i] + new_lines + content_lines[i + span :]
            return None

        def normalize_line_for_match(line: str) -> str:
            normalized = (line or "").strip()
            normalized = normalized.strip("*")
            normalized = re.sub(r"^\s*[+-]+\s*", "", normalized)
            return normalized.strip()

        def replace_single_line(
            content_lines: list[str], old_line: str, new_line: str, section: tuple[int, int]
        ) -> Optional[list[str]]:
            start, end = section
            start = max(0, min(start, len(content_lines)))
            end = max(start, min(end, len(content_lines)))
            old_norm = normalize_line_for_match(old_line)
            for idx in range(start, end):
                if normalize_line_for_match(content_lines[idx]) == old_norm:
                    updated = content_lines.copy()
                    updated[idx] = new_line
                    return updated
            return None

        operations = self._extract_patch_line_ops(diff_text)
        if not operations:
            return None

        applied_files = []
        no_change_files = []

        for filename, hunks in operations.items():
            filepath = resolve_file(filename)
            if filepath is None or not filepath.exists():
                return (
                    "RETRYABLE; next_action=retry with valid patch; "
                    f"details=Target file not found for fallback patch: {filename}"
                )

            next_content = filepath.read_text()
            file_changed = False
            file_noop = False

            for removed_lines, added_lines, context_lines in hunks:
                if not removed_lines and not added_lines:
                    continue

                current_lines = next_content.splitlines()
                section_lines = list(context_lines)
                if not section_lines:
                    section_lines.extend(line for line in removed_lines + added_lines if line)
                section = find_section_bounds(current_lines, section_lines)

                if removed_lines and not added_lines:
                    updated = remove_block(current_lines, removed_lines, section)
                    if updated is None:
                        return None
                    next_content = "\n".join(updated)
                    file_changed = True
                    continue

                if added_lines and not removed_lines:
                    add_lines = _dedupe_added_table_lines(current_lines, section, list(added_lines))
                    if not add_lines:
                        file_noop = True
                        continue

                    insert_at, insertion_section = infer_insert_index(current_lines, section_lines, add_lines)
                    if block_exists(current_lines, add_lines, insertion_section):
                        file_noop = True
                        continue

                    if insert_at < 0:
                        insert_at = 0
                    insert_at = min(insert_at, len(current_lines))
                    current_lines[insert_at:insert_at] = add_lines
                    next_content = "\n".join(current_lines)
                    file_changed = True
                    continue

                if removed_lines and added_lines:
                    updated = None
                    if len(removed_lines) == 1 and len(added_lines) == 1:
                        updated = replace_single_line(current_lines, removed_lines[0], added_lines[0], section)
                    if updated is None:
                        updated = replace_block(current_lines, removed_lines, added_lines, section)

                    if updated is None:
                        if block_exists(current_lines, added_lines, section) and not block_exists(current_lines, removed_lines, section):
                            file_noop = True
                            continue
                        return None

                    next_content = "\n".join(updated)
                    file_changed = True

            if file_changed:
                filepath.write_text(next_content)
                applied_files.append(filename)
            elif file_noop:
                no_change_files.append(filename)

        if applied_files or no_change_files:
            detail_parts = []
            if applied_files:
                detail_parts.append(f"patched: {', '.join(sorted(applied_files))}")
            if no_change_files:
                detail_parts.append(f"no-op (already up-to-date): {', '.join(sorted(no_change_files))}")
            detail = "; ".join(detail_parts)
            return (
                "APPLIED; next_action=none; details=Deterministic fallback patch applied. "
                + detail
            )

        return None

    def _extract_patch_line_ops(
        self,
        patch_text: str,
    ) -> Dict[str, list[tuple[list[str], list[str], list[str]]]]:
        text = self._normalize_unified_diff((patch_text or "").strip())
        if not text:
            return {}

        lines = text.splitlines()
        operations: Dict[str, list[tuple[list[str], list[str], list[str]]]] = {}

        current_file: Optional[str] = None
        removed: list[str] = []
        added: list[str] = []
        context: list[str] = []
        in_patch = False
        in_hunk = False

        def flush() -> None:
            nonlocal removed, added, context
            if not current_file:
                removed, added, context = [], [], []
                return
            if removed or added:
                operations.setdefault(current_file, []).append((removed, added, context))
            removed, added, context = [], [], []

        for line in lines:
            if line.startswith("*** Begin Patch"):
                flush()
                in_patch = True
                in_hunk = False
                continue

            if line.startswith("*** End Patch"):
                flush()
                in_patch = False
                in_hunk = False
                continue

            if line.startswith("diff --git"):
                flush()
                m = re.match(r"^diff --git\s+a/(\S+)\s+b/(\S+)", line)
                if m:
                    current_file = m.group(2)
                    if current_file == "/dev/null":
                        current_file = None
                in_patch = True
                in_hunk = False
                continue

            if line.startswith("*** Update File:"):
                flush()
                current_file = line[len("*** Update File:"):].strip()
                in_patch = True
                in_hunk = False
                continue

            if line.startswith("---"):
                path = line[3:].strip()
                if path:
                    if path.startswith(("a/", "b/")):
                        path = path[2:]
                    if path != "/dev/null":
                        current_file = path
                in_patch = True
                in_hunk = False
                continue

            if line.startswith("+++"):
                path = line[3:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path and path != "/dev/null":
                    current_file = path
                continue

            if line.startswith("@@"):
                flush()
                in_hunk = True
                continue

            if not in_patch:
                continue

            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
                in_hunk = True
                continue

            if line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])
                in_hunk = True
                continue

            if line.startswith(" "):
                context.append(line[1:])
                continue

            if in_hunk and line.startswith("\\ No newline"):
                continue

            if in_hunk and line.startswith("\\"):
                continue

            if in_hunk and line.strip() == "":
                context.append("")
                continue

            if in_hunk and line and line.strip():
                context.append(line)
                continue

            if line.strip() == "":
                continue

        flush()

        cleaned: Dict[str, list[tuple[list[str], list[str], list[str]]]] = {}
        for filename, hunks in operations.items():
            filtered = [hunk for hunk in hunks if hunk[0] or hunk[1]]
            if filtered:
                cleaned[filename] = filtered

        return cleaned

    def _build_patch_retry_prompt(
        self,
        failure_details: str,
        attempt: int,
    ) -> str:
        return (
            "A prior patch failed to apply during this turn.\n"
            f"Failure reason: {failure_details}\n"
            f"This is retry attempt {attempt}.\n\n"
            "Read the current workspace file contents and return a corrected, single patch for the same goal.\n"
            "Use a clean patch only:\n"
            "- unified diff (```diff```), or\n"
            "- OpenAI apply patch block (*** Begin Patch ... *** End Patch).\n"
            "No extra explanation, no command wrappers."
        )

    @staticmethod
    def _looks_retryable_due_context_loss(next_action: str) -> bool:
        return "re-read target file context" in (next_action or "").lower()

    def _apply_unified_diff_with_retries(self, diff_text: str) -> str:
        """
        Apply a unified/OpenAI-style patch with one bounded retry path.

        If the first attempt returns RETRYABLE with a context refresh request, it requests
        a fresh patch from the model and immediately retries.
        """
        current_patch = diff_text
        last_result = ""
        max_attempts = max(1, self.AUTO_APPLY_MAX_ATTEMPTS)

        for attempt in range(1, max_attempts + 1):
            result = self._apply_unified_diff(current_patch)
            status, next_action, details = self._parse_apply_contract(result)
            last_result = result

            # Success or hard-failure stop immediately.
            if status != "RETRYABLE":
                break

            # If there's no contract reason to retry, stop.
            if not self._looks_retryable_due_context_loss(next_action):
                break

            # Last allowed attempt: report actionable status without another model call.
            if attempt >= max_attempts:
                break

            retry_prompt = self._build_patch_retry_prompt(details, attempt + 1)
            retry_raw = self.agent.handle_message(retry_prompt)
            retry_raw = self._strip_json_command_prefix(retry_raw)
            refreshed_diff = self._extract_diff_block(retry_raw)
            if refreshed_diff:
                current_patch = refreshed_diff
                continue

            refreshed_script = self._extract_bash_block(retry_raw)
            if refreshed_script:
                script_result = self._apply_shell_script_with_retries(refreshed_script, attempt + 1)
                return script_result

            last_result = (
                "RETRYABLE; next_action=manual retry with edited patch; "
                f"details=Could not extract a valid patch from model retry response after failure: {details}"
            )
            break

        return last_result

    def _apply_shell_script_with_retries(self, script: str, attempt: int) -> str:
        result = self._auto_apply_shell_script(script)
        status, next_action, details = self._parse_apply_contract(result)
        if status != "RETRYABLE":
            return result

        if not self._looks_retryable_due_context_loss(next_action):
            return result

        if attempt >= self.AUTO_APPLY_MAX_ATTEMPTS:
            return result

        retry_prompt = self._build_patch_retry_prompt(details, attempt + 1)
        retry_raw = self.agent.handle_message(retry_prompt)
        retry_raw = self._strip_json_command_prefix(retry_raw)
        refreshed_script = self._extract_bash_block(retry_raw)
        if not refreshed_script:
            refreshed_diff = self._extract_diff_block(retry_raw)
            if refreshed_diff:
                return self._apply_unified_diff_with_retries(refreshed_diff)
            return "RETRYABLE; next_action=manual retry with corrected command; details=Could not extract a valid rerun command."

        return self._auto_apply_shell_script(refreshed_script)

    @staticmethod
    def _shell_segment_command(segment: str) -> str:
        trimmed = (segment or "").strip()
        if not trimmed:
            return ""

        tokens = trimmed.split()
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                idx += 1
                continue
            return token.strip()
        return ""

    @staticmethod
    def _is_interactive_shell_segment(segment: str) -> bool:
        cmd = TUI._shell_segment_command(segment).lower()
        if not cmd:
            return False

        unsafe_commands = {
            "python",
            "python3",
            "node",
            "bun",
            "deno",
            "ruby",
            "perl",
            "php",
            "go",
            "cargo",
            "npm",
            "pnpm",
            "yarn",
            "pytest",
            "uvicorn",
            "flask",
            "gunicorn",
            "read",
            "bash",
            "sh",
            "zsh",
            "fish",
        }

        if cmd in unsafe_commands:
            return True
        if cmd.startswith(("./", "/")) and re.search(r"\.(py|sh|rb|js|ts)$", cmd):
            return True
        if cmd.endswith(".py"):
            return True
        return False

    def _sanitize_shell_script_for_autorun(self, script: str) -> tuple[str, List[str]]:
        if not script:
            return "", []

        safe_lines: List[str] = []
        unsafe_lines: List[str] = []
        in_here_doc = False
        here_marker = ""

        for line in script.splitlines():
            stripped = line.strip()
            if not stripped:
                safe_lines.append(line)
                continue

            if in_here_doc:
                safe_lines.append(line)
                if stripped == here_marker:
                    in_here_doc = False
                    here_marker = ""
                continue

            heredoc = re.search(r"<<\s*['\"]?([A-Za-z0-9_]+)['\"]?", stripped)
            if heredoc:
                safe_lines.append(line)
                in_here_doc = True
                here_marker = heredoc.group(1)
                continue

            segments = re.split(r"\s*(?:&&|\|\||;|\|)\s*", line)
            safe_segments: List[str] = []
            for segment in segments:
                seg = segment.strip()
                if not seg:
                    continue
                if self._is_interactive_shell_segment(seg):
                    unsafe_lines.append(seg)
                else:
                    safe_segments.append(seg)

            if safe_segments:
                safe_lines.append(" && ".join(safe_segments))

        safe_script = "\n".join(safe_lines).strip()
        return safe_script, unsafe_lines

    def _run_shell_script(self, script: str) -> str:
        result = subprocess.run(
            ["bash", "-lc", script],
            cwd=str(self.agent.workspace.path),
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode == 0:
            return f"APPLIED; next_action=none; details=Command output: {(out or 'success').strip()}"
        return f"RETRYABLE; next_action=retry with corrected script; details=Auto-apply failed (exit {result.returncode}). {(err or out)[:300]}".strip()

    def _parse_apply_contract(self, output: str) -> tuple[str, str, str]:
        if hasattr(self.agent, "_parse_heartbeat_action_contract"):
            try:
                status, next_action, details = self.agent._parse_heartbeat_action_contract(output)
                if status not in {"APPLIED", "RETRYABLE", "FAILED_PERM"}:
                    status = "RETRYABLE"
                return status, next_action, details
            except Exception:
                pass

        if not output:
            return "FAILED_PERM", "no output", "No output from apply step."

        upper = output.upper()
        if "APPLIED" in upper:
            status = "APPLIED"
        elif "FAILED_PERM" in upper:
            status = "FAILED_PERM"
        elif "RETRYABLE" in upper:
            status = "RETRYABLE"
        else:
            status = "RETRYABLE"

        details = output
        next_action = ""
        match = re.search(r"next_action\s*=\s*([^;]+)", output, flags=re.IGNORECASE)
        if match:
            next_action = match.group(1).strip()
        match = re.search(r"details\s*=\s*(.+)", output, flags=re.IGNORECASE)
        if match:
            details = match.group(1).strip()
        return status, next_action, details

    @staticmethod
    def _normalize_unified_diff(diff_text: str) -> str:
        candidate = (diff_text or "").strip()
        if not candidate:
            return ""

        if candidate.startswith("{") and "\"cmd\"" in candidate[:80]:
            close = candidate.find("}")
            if close != -1 and close + 1 < len(candidate):
                candidate = candidate[close + 1 :].strip()

        marker = candidate.find("diff --git")
        if marker > 0:
            candidate = candidate[marker:]

        return candidate

    def _sanitize_apply_patch(self, patch_text: str) -> str:
        candidate = (patch_text or "").strip()
        if not candidate:
            return ""

        # Remove markdown-style fences if accidentally included.
        candidate = re.sub(r"^```[^\n]*\n", "", candidate).strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()

        embedded = self._extract_embedded_apply_patch(candidate)
        if embedded:
            return f"{embedded}\n"

        if not candidate.lstrip().startswith("*** Begin Patch"):
            return ""
        return f"{candidate}\n"

    def _extract_embedded_apply_patch(self, text: str) -> Optional[str]:
        if not text:
            return None

        matches = re.findall(r"(?ms)^\s*\*\*\* Begin Patch[\s\S]*?^\s*\*\*\* End Patch", text)
        if matches:
            blocks = [textwrap.dedent(match).strip() for match in matches if match.strip()]
            if blocks:
                return "\n\n".join(blocks)
        return None

    def _apply_apply_patch_block(self, patch_text: str) -> str:
        apply_patch_path = shutil.which("apply_patch")
        if not apply_patch_path:
            return (
                "FAILED_PERM; next_action=re-read target file context and regenerate patch; "
                "details=apply_patch utility not found"
            )

        candidate = self._sanitize_apply_patch(patch_text)
        if not candidate:
            return "FAILED_PERM; next_action=convert to unified diff; details=not an apply_patch block"

        try:
            p = subprocess.run(
                [apply_patch_path],
                cwd=str(self.agent.workspace.path),
                input=candidate,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if p.returncode == 0:
                return "APPLIED; next_action=none; details=Applied OpenAI-style patch successfully."
            return (
                "RETRYABLE; next_action=re-read target file context and regenerate patch; "
                f"details={(p.stderr or p.stdout).strip()[:300]}"
            )
        except Exception as e:
            return f"FAILED_PERM; next_action=retry with a valid patch; details=Patch apply error: {e}"

    @staticmethod
    def _has_file_edit_hint(user_input: str) -> bool:
        lowered = (user_input or "").lower()
        if re.search(
            r"\b[\w./-]+\.(?:md|txt|rst|json|ya?ml|toml|ini|cfg|py|js|ts|tsx|jsx|html|css|sh|sql)\b",
            lowered,
        ):
            return True

        hints = (
            "this file",
            "that file",
            "the file",
            "in file ",
            "to file ",
            "into file ",
            "in the workspace",
        )
        return any(hint in lowered for hint in hints)

    def _should_auto_apply(self, user_input: str) -> bool:
        lowered = (user_input or "").strip().lower()
        if not lowered:
            return False

        if self._is_confirmation_request(lowered):
            return True

        edit_verbs = ("add", "apply", "edit", "update", "write", "fix", "patch", "implement", "fill", "set", "change")
        if not any(re.search(rf"\b{re.escape(verb)}\b", lowered) for verb in edit_verbs):
            return False

        return self._has_file_edit_hint(lowered)

    @staticmethod
    def _is_confirmation_request(user_input: str) -> bool:
        lowered = (user_input or "").strip().lower()
        confirmations = {
            "do it",
            "please do",
            "apply it",
            "go ahead",
            "go for it",
            "proceed",
            "yes",
            "yes please",
        }
        return lowered in confirmations

    def _looks_like_done_claim(self, text: str) -> bool:
        l = text.lower()
        markers = ["done", "updated", "i updated", "applied", "completed"]
        return any(m in l for m in markers)

    def _auto_apply_shell_script(self, script: str) -> str:
        # Guardrails: block obvious destructive commands.
        blocked = ["rm -rf /", "mkfs", "shutdown", "reboot", "diskutil erase"]
        s = script.lower()
        if any(b in s for b in blocked):
            return "FAILED_PERM; next_action=adjust command; details=Auto-apply blocked: destructive command detected"

        safe_script, unsafe_lines = self._sanitize_shell_script_for_autorun(script)
        if not safe_script and unsafe_lines:
            return (
                "RETRYABLE; next_action=apply edit-only commands only; "
                f"details=No safe commands available after sanitization. Skipped: {', '.join(unsafe_lines)}"
            )

        if not safe_script:
            return "FAILED_PERM; next_action=provide valid script; details=No executable shell content."

        try:
            outcome = self._run_shell_script(safe_script)
            status, _next_action, details = self._parse_apply_contract(outcome)

            if unsafe_lines:
                skipped = ", ".join(unsafe_lines)
                if status == "APPLIED":
                    return (
                        "RETRYABLE; next_action=run skipped runtime commands separately; "
                        f"details=Applied safe operations. Skipped commands: {skipped}. Details: {details}"
                    )
                return (
                    f"{status}; next_action=retry with corrected script; "
                    f"details={details}. Also skipped runtime commands: {skipped}"
                )

            return outcome
        except Exception as e:
            return f"FAILED_PERM; next_action=retry with corrected script; details=Auto-apply error: {e}"

    @staticmethod
    def _format_git_status_label(status_code: str) -> str:
        if status_code == "??":
            return TUI.GIT_STATUS_LABELS["??"]

        if not status_code:
            return "changed"

        flags = set(status_code)
        for key in ("U", "D", "R", "C", "A", "M"):
            if key in flags:
                return TUI.GIT_STATUS_LABELS[key]
        return "changed"

    @staticmethod
    def _parse_git_status_line(line: str) -> tuple[str, str]:
        trimmed = (line or "").rstrip()
        if not trimmed:
            return "", ""

        if trimmed.startswith("??"):
            return "??", trimmed[2:].strip()

        if len(trimmed) >= 3 and trimmed[1] == " ":
            return trimmed[:2], trimmed[3:].strip()

        if " " in trimmed:
            status, path = trimmed.split(" ", 1)
            return status.strip(), path.strip()
        return trimmed.strip(), ""

    @staticmethod
    def _truncate_for_log(value: str, limit: int = 160) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    def _build_compaction_summary(self, dropped_messages: list[Message]) -> str:
        if not dropped_messages:
            return "No earlier conversation to summarize."

        total = len(dropped_messages)
        user_count = sum(1 for message in dropped_messages if message.role == "user")
        assistant_count = sum(1 for message in dropped_messages if message.role == "assistant")
        lines = [
            f"Compacted from {total} older messages.",
            f"Included contributions: {user_count} user, {assistant_count} assistant.",
            "",
            "Recent dropped turns:",
        ]
        for message in dropped_messages[-6:]:
            content = (message.content or "").replace("\n", " ").strip()
            if not content:
                continue
            role = "User" if message.role == "user" else "Assistant"
            lines.append(f"- {role}: {self._truncate_for_log(content)}")
        if len(lines) == 4:
            lines.append("- (No readable content in dropped turns.)")
        return "\n".join(lines)

    # pure agentic mode: no deterministic profile shortcuts

    def _workspace_changes_summary(self) -> str:
        try:
            ws = str(self.agent.workspace.path)
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if top.returncode != 0:
                return "Workspace changes: unavailable (not a git repo)"
            root = (top.stdout or "").strip()
            if Path(root).resolve() != Path(ws).resolve():
                return "Workspace changes: unavailable (workspace is inside another repo)"

            r = subprocess.run(
                ["git", "status", "--short"],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode != 0:
                return "Workspace changes: unavailable"
            raw_lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
            lines = []
            for raw_line in raw_lines:
                code, path = self._parse_git_status_line(raw_line)
                if not path:
                    continue
                label = self._format_git_status_label(code)
                lines.append(f"{label}: {path}")
            if not lines:
                return "Workspace changes: none"
            tracked_lines = [line for line in lines if not line.startswith("untracked:")]
            if tracked_lines:
                lines = tracked_lines
            elif lines:
                return "Workspace changes: untracked only"
            preview = ", ".join(lines[:5])
            more = f" (+{len(lines)-5} more)" if len(lines) > 5 else ""
            return f"Workspace changes: {preview}{more}"
        except Exception:
            return "Workspace changes: unavailable"

    def _format_uptime(self) -> str:
        delta = datetime.now() - self._start_time
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}h {m}m"

    def _read_with_timeout(self, fd: int, timeout: float) -> Optional[bytes]:
        timeout = max(0.0, timeout)
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        ch = self._read_stdin(fd)
        if not ch:
            return None
        return ch

    @staticmethod
    def _consume_ansi_escape(seq: bytes) -> int:
        if not seq or seq[0] != 0x1B:
            return 0
        if len(seq) == 1:
            return 0

        # Non-CSI escapes are two-byte sequences.
        if seq[1] != ord("["):
            if 64 <= seq[1] <= 126:
                return 2
            return 0

        # CSI escapes terminate on the first byte in the final-range (@-~).
        for idx in range(2, len(seq)):
            if 64 <= seq[idx] <= 126:
                return idx + 1
        return 0

    def _read_escaped_sequence(self, fd: int, seed: bytes) -> str:
        if not seed:
            return ""

        seq = bytearray(seed)
        escape_deadline = time.monotonic() + 0.40
        while True:
            consumed = TUI._consume_ansi_escape(bytes(seq))
            if consumed:
                if consumed < len(seq):
                    self._unread_stdin(bytes(seq[consumed:]))
                return bytes(seq[:consumed]).decode("utf-8", "ignore")

            nxt = self._read_with_timeout(fd, max(0.0, escape_deadline - time.monotonic()))
            if nxt is None:
                break
            seq.extend(nxt)
            if len(seq) > 64:
                break

        return bytes(seq).decode("utf-8", "ignore")

    def _read_stdin(self, fd: int) -> Optional[bytes]:
        if self._stdin_buffer:
            data = self._stdin_buffer[:1]
            self._stdin_buffer = self._stdin_buffer[1:]
            return data
        ch = os.read(fd, 1)
        return ch

    def _read_bracketed_paste(self, fd: int) -> str:
        end = b"\x1b[201~"
        payload = bytearray()
        while True:
            ch = self._read_stdin(fd)
            if ch is None:
                break
            if not ch:
                break
            payload.extend(ch)
            if payload.endswith(end):
                break
        if payload.endswith(end):
            payload = payload[:-len(end)]
        return payload.decode("utf-8", "replace")

    def _unread_stdin(self, data: bytes) -> None:
        if data:
            self._stdin_buffer = data + self._stdin_buffer

    def _read_key_from_terminal(self, fd: int) -> str:
        first = self._read_stdin(fd)
        if not first:
            return "EOF"
        if first == b"\n":
            return "CTRL_J"
        if first == b"\r":
            timeout = 0.08
            tail = bytearray()
            end_time = time.monotonic() + timeout
            while time.monotonic() < end_time:
                nxt = self._read_with_timeout(fd, end_time - time.monotonic())
                if nxt is None:
                    break
                tail.extend(nxt)
            # If immediate follow-up data is buffered, this is paste/flow text.
            if tail.startswith(b"\r\n") or tail.startswith(b"\n\r"):
                tail = tail[2:]
            elif tail.startswith((b"\r", b"\n")):
                tail = tail[1:]
            if not tail:
                return "ENTER"
            if tail.startswith(b"\x1b"):
                self._unread_stdin(bytes(tail))
                esc = self._read_escaped_sequence(fd, b"\x1b")
                if esc == "\x1b[200~":
                    return f"\x00PASTE::{self._read_bracketed_paste(fd)}"
                if self._is_shift_enter_sequence(esc):
                    return "SHIFT_ENTER"
                if esc in {"\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"}:
                    return "ARROW"
                self._unread_stdin(esc.encode("utf-8"))
                return "SOFT_ENTER"
            self._unread_stdin(bytes(tail))
            return "SOFT_ENTER"
        if first in {b"\x03"}:
            raise KeyboardInterrupt
        if first in {b"\x7f", b"\b"}:
            return "BACKSPACE"
        if first == b"\x15":
            return "CTRL_U"
        if first == b"\x04":
            return "EOF"

        if first != b"\x1b":
            return first.decode("utf-8", "ignore")

        esc = self._read_escaped_sequence(fd, first)
        if esc == "\x1b[200~":
            return f"\x00PASTE::{self._read_bracketed_paste(fd)}"
        if self._is_shift_enter_sequence(esc):
            return "SHIFT_ENTER"
        if esc in {"\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"}:
            return "ARROW"
        return "ESC"

    @staticmethod
    def _is_shift_enter_sequence(seq: str) -> bool:
        if not seq.startswith("\x1b["):
            return False
        body = seq[2:]
        if len(body) < 2:
            return False
        if body[-1] not in {"~", "u", "U"}:
            return False

        params = body[:-1].split(";")
        if len(params) < 2:
            return False

        if (params[0] in {"10", "13"}) and params[-1] in {"2", "2u", "2U"}:
            return True

        return ("13" in params or "10" in params) and "2" in params

    @staticmethod
    def _strip_paste_line_numbers(line: str) -> str:
        line = re.sub(r"^\s*>+\s*", "", line, count=1)
        return re.sub(r"^\s*\d+\|\s*", "", line, count=1)

    def _apply_paste_cleanup(self, text: str) -> str:
        if not text:
            return text
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(self._strip_paste_line_numbers(line) for line in lines)

    @classmethod
    def _should_summarize_paste(cls, text: str) -> tuple[bool, int]:
        line_count = (text.count("\n") + 1) if text else 0
        summarize = line_count >= cls.PASTE_SUMMARY_MIN_LINES or len(text) >= cls.PASTE_SUMMARY_MIN_CHARS
        return summarize, line_count

    @staticmethod
    def _detect_preview_language(preview: str) -> str:
        value = (preview or "").strip()
        if not value:
            return "text"
        if "diff --git" in value or value.startswith("*** Begin Patch") or "\n@@ " in value:
            return "diff"
        if value.startswith("#!/") or re.search(r"(?m)^\s*(mkdir|cat|sed|awk|python3?|bash|sh|git)\b", value):
            return "bash"
        return "text"

    def _render_proposed_changes_panel(self, preview: str) -> Panel:
        snippet = (preview or "").strip()
        if len(snippet) > 8000:
            snippet = snippet[:8000].rstrip() + "\n... (truncated)"
        language = self._detect_preview_language(snippet)
        title = "Proposed diff" if language == "diff" else "Proposed changes"
        return Panel(
            Syntax(snippet or "(no preview)", language, line_numbers=False, word_wrap=True),
            title=title,
            border_style="bright_blue",
            box=box.ROUNDED,
        )

    def _format_input_line_prefix(self, line_no: int = 0) -> str:
        return "│ "

    @staticmethod
    def _normalize_file_token(token: str) -> str:
        cleaned = (token or "").strip().strip("'\"`")
        cleaned = cleaned.rstrip(".,:;!?)]}>")
        return cleaned.lstrip("./")

    def _resolve_workspace_file_reference(self, token: str) -> Optional[str]:
        ref = self._normalize_file_token(token)
        if not ref:
            return None

        direct = self.agent.workspace.path / ref
        if direct.is_file():
            return ref

        requested = Path(ref)
        requested_name = requested.name.casefold()
        requested_parts = [p.casefold() for p in requested.parts if p and p != "."]
        if not requested_parts:
            return None

        for candidate in self.agent.workspace.path.rglob("*"):
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(self.agent.workspace.path)
            rel_parts = [p.casefold() for p in rel.parts]
            if len(rel_parts) < len(requested_parts):
                continue
            if rel_parts[-len(requested_parts):] == requested_parts:
                return str(rel)
            if requested_name and rel.name.casefold() == requested_name:
                return str(rel)
        return None

    def _inject_referenced_file_context(self, user_input: str) -> str:
        text = user_input or ""
        refs = re.findall(r"(?i)\b[\w./-]+\.[a-z0-9]{1,8}\b", text)
        if not refs:
            return text

        resolved: list[str] = []
        for ref in refs:
            path = self._resolve_workspace_file_reference(ref)
            if path and path not in resolved:
                resolved.append(path)
            if len(resolved) >= 2:
                break

        if not resolved:
            return text

        blocks: list[str] = []
        for path in resolved:
            content = self.agent.workspace.read(path) or ""
            snippet = content[:12000]
            blocks.append(
                f"[Auto-loaded workspace file: {path}]\n```text\n{snippet}\n```"
            )

        return f"{text}\n\n" + "\n\n".join(blocks)

    def _read_multiline_input(self) -> str:
        if not sys.stdin.isatty():
            return Prompt.ask("\n [user]>[/user]")

        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        lines: list[str] = [""]
        paste_markers: dict[str, str] = {}
        next_paste_id = 1

        def _remove_markers_in_fragment(fragment: str) -> None:
            if not fragment:
                return
            for marker in list(paste_markers.keys()):
                if marker in fragment:
                    paste_markers.pop(marker, None)

        def _prune_stale_markers() -> None:
            composed = "\n".join(lines)
            for marker in list(paste_markers.keys()):
                if marker not in composed:
                    paste_markers.pop(marker, None)

        try:
            self.console.print(f"[dim]{self._composer_top_border()}[/dim]")
            tty.setraw(fd)
            sys.stdout.write("\x1b[?2004h")
            sys.stdout.flush()
            sys.stdout.write(self._format_input_line_prefix())
            sys.stdout.flush()

            while True:
                key = self._read_key_from_terminal(fd)
                if key == "":
                    continue

                if key == "ENTER":
                    composed = "\n".join(lines).strip("\n")
                    for marker, payload in paste_markers.items():
                        composed = composed.replace(marker, payload)
                    return composed

                if key.startswith("\x00PASTE::"):
                    paste = key[len("\x00PASTE::"):]
                    paste = self._apply_paste_cleanup(paste)
                    if not paste:
                        continue
                    paste_lines = paste.split("\n")
                    summarize, line_count = self._should_summarize_paste(paste)
                    if summarize:
                        marker = (
                            f"++ paste {next_paste_id}: {line_count} lines ++"
                            if line_count != 1
                            else f"++ paste {next_paste_id}: 1 line ++"
                        )
                        next_paste_id += 1
                        paste_markers[marker] = paste
                        lines[-1] += marker
                        sys.stdout.write(marker)
                        sys.stdout.flush()
                        continue
                    for idx, paste_line in enumerate(paste_lines):
                        if idx == 0:
                            lines[-1] += paste_line
                            if paste_line:
                                sys.stdout.write(paste_line)
                                sys.stdout.flush()
                        else:
                            lines.append(paste_line)
                            sys.stdout.write(f"\n{self._format_input_line_prefix()}")
                            sys.stdout.write(paste_line)
                            sys.stdout.flush()
                    continue

                if key in {"SHIFT_ENTER", "SOFT_ENTER", "CTRL_J"}:
                    lines.append("")
                    sys.stdout.write(f"\n{self._format_input_line_prefix()}")
                    sys.stdout.flush()
                    continue

                if key == "BACKSPACE":
                    current = lines[-1]
                    if not current:
                        if len(lines) == 1:
                            continue
                        removed = lines.pop()
                        _remove_markers_in_fragment(removed)
                        sys.stdout.write("\x1b[1A\r\033[K")
                        sys.stdout.write(f"{self._format_input_line_prefix()}{lines[-1]}")
                        sys.stdout.write("\x1b[J")
                        sys.stdout.flush()
                        _prune_stale_markers()
                        continue

                    matched_marker = None
                    for marker in sorted(paste_markers.keys(), key=len, reverse=True):
                        if current.endswith(marker):
                            matched_marker = marker
                            break
                    if matched_marker:
                        lines[-1] = current[: -len(matched_marker)]
                        paste_markers.pop(matched_marker, None)
                        sys.stdout.write("\r\033[K")
                        sys.stdout.write(f"{self._format_input_line_prefix()}{lines[-1]}")
                        sys.stdout.flush()
                        continue

                    lines[-1] = current[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                    _prune_stale_markers()
                    continue

                if key == "CTRL_U":
                    _remove_markers_in_fragment(lines[-1])
                    lines[-1] = ""
                    sys.stdout.write("\r\033[K")
                    sys.stdout.write(self._format_input_line_prefix())
                    sys.stdout.flush()
                    continue

                if key in {"ESC", "ARROW"}:
                    continue

                if key == "EOF":
                    raise KeyboardInterrupt

                lines[-1] += key
                sys.stdout.write(key)
                sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.console.print(f"[dim]{self._composer_bottom_border()}[/dim]")

    def run(self) -> None:
        """Run the TUI main loop."""
        self._running = True
        self._render_banner()

        try:
            while self._running:
                try:
                    # Prompt
                    user_input = self._read_multiline_input()

                    if not user_input.strip():
                        continue

                    is_confirmation = self._is_confirmation_request(user_input)
                    if not is_confirmation:
                        self._last_edit_proposal = None

                    # Handle commands
                    if user_input.startswith("/"):
                        command_text = user_input[1:].strip()
                        if not command_text:
                            command_text = None
                        else:
                            command_token = command_text.split(maxsplit=1)[0]
                            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]*", command_token):
                                command_text = None

                        if command_text is None:
                            # Treat pasted/escaped text beginning with slash as normal content.
                            pass
                        else:
                            command_name = command_text.split(maxsplit=1)[0].lower()
                            if command_name in self.COMMANDS:
                                if not self._handle_command(command_text):
                                    break
                                continue

                    # Display user message
                    self._message_count += 1

                    # Get response with visible execution phases
                    with self.console.status("[dim]Preparing request...[/dim]", spinner="dots") as status:
                        t0 = time.monotonic()
                        backend_name = self.agent.backend.name
                        status.update(f"[dim]Using backend: {backend_name} | {self._format_context_usage()}[/dim]")

                        add_dirs = []
                        docs_dir = str((Path.home() / "Documents").resolve())
                        workspace_root = str(self.agent.workspace.path)
                        permission_granted = False
                        if "documents" in user_input.lower() and not workspace_root.startswith(docs_dir):
                            status.stop()
                            allow = Prompt.ask(
                                "  [cyan]Allow write access to ~/Documents for this task?[/cyan]",
                                choices=["y", "n"],
                                default="y",
                            )
                            if allow == "y":
                                permission_granted = True
                                add_dirs.append(docs_dir)
                            status.start()
                            status.update("[dim]Permission handled. Continuing...[/dim]")

                        effective_input = self._inject_referenced_file_context(user_input)
                        if permission_granted:
                            effective_input = (
                                "Permission granted: you may create/modify files under ~/Documents for this task.\n\n"
                                + effective_input
                            )

                        if backend_name == "codex-cli" and hasattr(self.agent.backend, "complete_with_progress"):
                            self.agent._set_mode(AgentMode.DIRECT)
                            context = self.agent.workspace.get_context(mode="direct")
                            system_prompt = self.agent._build_system_prompt(context, is_heartbeat=False)

                            prior = [
                                m
                                for m in self.agent.backend.get_history()[-12:]
                                if not self.agent._is_heartbeat_history_message(m.content)
                            ]
                            self.agent.backend.add_message(Message(role="user", content=effective_input))

                            # Keep short rolling transcript for codex-cli progress path.
                            transcript_parts = []
                            for m in prior:
                                who = "User" if m.role == "user" else "Assistant"
                                transcript_parts.append(f"{who}: {m.content}")
                            transcript = "\n".join(transcript_parts)
                            codex_prompt = effective_input if not transcript else f"Previous conversation:\n{transcript}\n\nCurrent user message:\n{effective_input}"

                            def _progress(msg: str) -> None:
                                status.update(f"[dim]{msg}[/dim]")

                            resp = self.agent.backend.complete_with_progress(
                                prompt=codex_prompt,
                                system_prompt=system_prompt,
                                context=context,
                                progress_callback=_progress,
                                add_dirs=add_dirs,
                            )
                            if resp.content == "NO_REPLY":
                                resp.content = "Hi there — what would you like me to do?"
                            self.agent.backend.add_message(Message(role="assistant", content=resp.content))
                            self.agent.state.message_count += 1
                            self.agent.state.last_activity = datetime.now()
                            response = resp.content
                        elif backend_name == "openai-codex-oauth" and hasattr(self.agent.backend, "complete_with_progress"):
                            self.agent._set_mode(AgentMode.DIRECT)
                            context = self.agent.workspace.get_context(mode="direct")
                            system_prompt = self.agent._build_system_prompt(context, is_heartbeat=False)

                            prior = [
                                m
                                for m in self.agent.backend.get_history()[-12:]
                                if not self.agent._is_heartbeat_history_message(m.content)
                            ]
                            self.agent.backend.add_message(Message(role="user", content=effective_input))

                            transcript_parts = []
                            for m in prior:
                                who = "User" if m.role == "user" else "Assistant"
                                transcript_parts.append(f"{who}: {m.content}")
                            transcript = "\n".join(transcript_parts)
                            oauth_prompt = effective_input if not transcript else f"Previous conversation:\n{transcript}\n\nCurrent user message:\n{effective_input}"

                            def _progress(msg: str) -> None:
                                status.update(f"[dim]{msg}[/dim]")

                            resp = self.agent.backend.complete_with_progress(
                                prompt=oauth_prompt,
                                system_prompt=system_prompt,
                                context=context,
                                progress_callback=_progress,
                            )
                            if resp.content == "NO_REPLY":
                                resp.content = "Hi there — what would you like me to do?"
                            self.agent.backend.add_message(Message(role="assistant", content=resp.content))
                            self.agent.state.message_count += 1
                            self.agent.state.last_activity = datetime.now()
                            response = resp.content
                        else:
                            status.update("[dim]Waiting for model response...[/dim]")
                            response = self.agent.handle_message(effective_input)

                        elapsed = time.monotonic() - t0

                    response = self._strip_json_command_prefix(response)
                    script = self._extract_bash_block(response)
                    diff_block = self._extract_diff_block(response)
                    should_attempt_auto_apply = (
                        not (response or "").startswith("Error:")
                        and (
                            bool(script or diff_block)
                            or self._should_auto_apply(user_input)
                            or (is_confirmation and self._last_edit_proposal is not None)
                        )
                    )

                    # Optional auto-apply for coding-assistant style file actions.
                    if should_attempt_auto_apply:
                        auto_steps = ["Auto-apply: analyzing assistant output."]
                        self.console.print("  [dim]Auto-apply: analyzing assistant output for editable actions...[/dim]")

                        # Always require actionable patch/script for edit intents.
                        if not diff_block and not script:
                            cached_kind = None
                            cached_value = None
                            if is_confirmation and self._last_edit_proposal:
                                cached_kind, cached_value = self._last_edit_proposal

                            if not cached_value:
                                auto_steps.append("No patch found; requesting a clean patch from the model.")
                                diff_req = self.agent.handle_message(
                                    "Provide only an actionable patch for the requested edit. Prefer a single ```diff``` block. No prose."
                                )
                                diff_req = self._strip_json_command_prefix(diff_req)
                                diff_block = self._extract_diff_block(diff_req)
                                if not diff_block:
                                    script = self._extract_bash_block(diff_req)
                            elif cached_kind == "diff":
                                diff_block = cached_value
                            elif cached_kind == "script":
                                script = cached_value

                            if cached_value and is_confirmation:
                                auto_steps.append("Reusing latest editable proposal from previous turn.")
                            if cached_value and is_confirmation:
                                self._last_edit_proposal = None

                        if script or diff_block:
                            if diff_block:
                                self._last_edit_proposal = ("diff", diff_block)
                            elif script:
                                self._last_edit_proposal = ("script", script)
                            decision = self._approval_mode
                            if self._approval_mode == "ask":
                                self.console.print("\n  [command]Edit proposal detected.[/command]")
                                preview = diff_block or script or "(no preview)"
                                self.console.print(self._render_proposed_changes_panel(preview))
                                choice = Prompt.ask(
                                    "  Approve edits?",
                                    choices=["once", "always", "never"],
                                    default="once",
                                )
                                if choice == "once":
                                    decision = "once"
                                elif choice == "always":
                                    self._approval_mode = "always"
                                    decision = "always"
                                else:
                                    self._approval_mode = "never"
                                    decision = "never"

                            if decision in {"once", "always"}:
                                auto_steps.append(
                                    "Applying proposed changes."
                                    if diff_block
                                    else "Applying proposed shell command now."
                                )
                                self.console.print(f"  [system]{auto_steps[-1]}[/system]")
                                if diff_block:
                                    apply_result = self._apply_unified_diff_with_retries(diff_block)
                                else:
                                    apply_result = self._apply_shell_script_with_retries(script or "", 1)

                                status, next_action, details = self._parse_apply_contract(apply_result)
                                if status == "APPLIED" and not self._auto_apply_verbose:
                                    compact = f"Auto-apply: {status}"
                                    if next_action:
                                        compact += f"; {next_action}"
                                    if details:
                                        compact += f"; {details}"
                                    response = f"{response}\n\n---\n{compact}"
                                else:
                                    auto_steps.append(
                                        f"Auto-apply status: {status}"
                                        + (f"; next_action: {next_action}" if next_action else "")
                                    )
                                    if details:
                                        auto_steps.append(f"Details: {details}")
                                    if status == "RETRYABLE" and next_action:
                                        auto_steps.append(f"Retry action: {next_action}")
                                    response = f"{response}\n\n---\n" + "\n".join(auto_steps)
                            else:
                                auto_steps.append("Auto-apply skipped: user denied edits.")
                                response = f"{response}\n\n---\n" + "\n".join(auto_steps)
                                alt = self.agent.handle_message(
                                    "User denied the proposed file edits. Provide next best steps without modifying files."
                                )
                                alt = self._strip_json_command_prefix(alt)
                                response = f"{response}\n\n**Alternative plan:**\n{alt}"
                        else:
                            # Prevent fake "done" claims when no patch exists.
                            if self._looks_like_done_claim(response):
                                auto_steps.append("NOT_APPLIED: no actionable patch/script produced.")
                                response = (
                                    f"{response}\n\n---\n"
                                    "\n".join(auto_steps)
                                )

                    response = f"{response}\n\n{self._workspace_changes_summary()}"

                    # Display response
                    self.console.print()
                    self.console.print(Panel(
                        Markdown(response),
                        title=f"[assistant]Assistant[/assistant] [dim]({elapsed:.1f}s)[/dim]",
                        border_style="green",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    ))

                except KeyboardInterrupt:
                    self.console.print("\n  [system]Use /quit to exit[/system]")
                except EOFError:
                    self._running = False
                    self.console.print("\n  [system]Goodbye![/system]")
                except Exception as e:
                    self.console.print(f"  [error]Error: {e}[/error]")
        finally:
            self.agent.stop_proactive()


def start_tui(agent: Agent, config: Optional[Config] = None) -> None:
    """Start the TUI."""
    tui = TUI(agent, config)
    tui.run()
