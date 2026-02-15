"""Terminal User Interface for Libre Claw.

Rich-based TUI with slash commands, streaming output, and a polished experience.
"""

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
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
        "login": "Import/login provider auth (usage: /login openai)",
        "model": "Show/set model for current backend (usage: /model [model-id])",
        "models": "List available models for current backend",
        "context": "Show loaded workspace context files + estimated context usage",
        "compact": "Compact conversation history (keeps recent turns)",
        "approval": "Edit approval mode (usage: /approval [ask|always|never|status])",
        "daily": "Append to today's daily note (usage: /daily <text>)",
        "files": "List workspace files",
        "read": "Read a workspace file (usage: /read <filename>)",
        "cost": "Show token usage and cost estimate",
        "quit": "Exit Libre Claw",
    }

    def __init__(self, agent: Agent, config: Optional[Config] = None):
        self.agent = agent
        self.config = config or Config()
        self.console = Console(theme=THEME)
        self._running = False
        self._start_time = datetime.now()
        self._message_count = 0
        self._approval_mode = "ask"  # ask | always | never

    def _openai_auth_target_path(self) -> Path:
        configured = self.config.backend.openai_auth_file or "~/.config/libre-claw/auth/openai.json"
        return Path(configured).expanduser()

    def _workspace_config_path(self) -> Path:
        return self.agent.workspace.path / "config.yaml"

    def _save_user_config(self) -> None:
        target = self._workspace_config_path()
        self.config.save(target)

    def _import_openai_auth_from_codex(self) -> Optional[str]:
        """Try importing OpenAI/Codex auth from common local locations."""
        candidates = [
            Path("~/.codex/auth.json").expanduser(),
            Path("~/.config/codex/auth.json").expanduser(),
            Path("~/.local/share/codex/auth.json").expanduser(),
            Path("~/.config/openai/auth.json").expanduser(),
            Path("~/.openai/auth.json").expanduser(),
        ]

        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
                token = data.get("access_token") or data.get("api_key")
                if token:
                    target = self._openai_auth_target_path()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({"access_token": token}, indent=2) + "\n")
                    return str(path)
            except Exception:
                continue

        return None

    def _render_banner(self) -> None:
        """Render the startup banner."""
        banner = Text()
        banner.append("╔══════════════════════════════════════╗\n", style="accent")
        banner.append("║         ", style="accent")
        banner.append("LIBRE CLAW", style="bold cyan")
        banner.append(" v0.1.0", style="dim")
        banner.append("         ║\n", style="accent")
        banner.append("║   ", style="accent")
        banner.append("Agentic AI Framework", style="green")
        banner.append("              ║\n", style="accent")
        banner.append("║   ", style="accent")
        banner.append("Kroonen AI Inc.", style="dim")
        banner.append("                   ║\n", style="accent")
        banner.append("╚══════════════════════════════════════╝", style="accent")
        self.console.print(banner)
        self.console.print()

        # Session info bar
        info = self.agent.get_session_info()
        bar = Text()
        bar.append("  ● ", style="green")
        bar.append(f"Backend: {info['backend']}", style="info")
        bar.append("  │  ", style="dim")
        bar.append(f"Mode: {info['mode']}", style="info")
        bar.append("  │  ", style="dim")
        bar.append(f"Workspace: {info['workspace']}", style="info")
        self.console.print(bar)
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

        self.console.print("  [dim]Type /help for commands, /quit to exit[/dim]")
        self.console.print()

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
            est = self._estimate_context_tokens()
            self.console.print(f"  [system]Estimated context: ~{est:,} tokens[/system]")

        elif cmd == "compact":
            history = self.agent.backend.get_history()
            keep = 12
            if len(history) <= keep:
                self.console.print("  [system]Nothing to compact[/system]")
            else:
                dropped = len(history) - keep
                self.agent.backend._conversation_history = history[-keep:]
                self.console.print(f"  [system]Compacted history: dropped {dropped} messages, kept {keep}[/system]")

        elif cmd == "approval":
            mode = (args or "status").strip().lower()
            if mode in {"ask", "always", "never"}:
                self._approval_mode = mode
                self.console.print(f"  [system]Approval mode set to: {mode}[/system]")
            else:
                self.console.print(f"  Current approval mode: [bold]{self._approval_mode}[/bold]")
                self.console.print("  [dim]Modes: ask (default), always (danger), never[/dim]")

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

    def _extract_bash_block(self, text: str) -> Optional[str]:
        match = re.search(r"```(?:bash|sh)?\n([\s\S]*?)\n```", text)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_diff_block(self, text: str) -> Optional[str]:
        match = re.search(r"```diff\n([\s\S]*?)\n```", text)
        if not match:
            return None
        return match.group(1).strip()

    def _apply_unified_diff(self, diff_text: str) -> str:
        try:
            p = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                cwd=str(self.agent.workspace.path),
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if p.returncode == 0:
                return "Applied unified diff successfully."
            return f"Diff apply failed: {(p.stderr or p.stdout).strip()[:300]}"
        except Exception as e:
            return f"Diff apply error: {e}"

    def _should_auto_apply(self, user_input: str) -> bool:
        lowered = user_input.lower()
        triggers = ["edit", "update", "create", "write", "fix", "patch", "implement"]
        return any(t in lowered for t in triggers)

    def _looks_like_done_claim(self, text: str) -> bool:
        l = text.lower()
        markers = ["done", "updated", "i updated", "applied", "completed"]
        return any(m in l for m in markers)

    def _auto_apply_shell_script(self, script: str) -> str:
        # Guardrails: block obvious destructive commands.
        blocked = ["rm -rf /", "mkfs", "shutdown", "reboot", "diskutil erase"]
        s = script.lower()
        if any(b in s for b in blocked):
            return "Auto-apply blocked: destructive command detected"

        try:
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
                return f"Auto-applied changes successfully. {out[:300]}".strip()
            return f"Auto-apply failed (exit {result.returncode}). {err[:300]}".strip()
        except Exception as e:
            return f"Auto-apply error: {e}"

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
            lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
            if not lines:
                return "Workspace changes: none"
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

    def run(self) -> None:
        """Run the TUI main loop."""
        self._running = True
        self._render_banner()

        try:
            while self._running:
                try:
                    # Prompt
                    user_input = Prompt.ask("\n [user]>[/user]")

                    if not user_input.strip():
                        continue

                    # Handle commands
                    if user_input.startswith("/"):
                        if not self._handle_command(user_input[1:]):
                            break
                        continue

                    # Display user message
                    self._message_count += 1

                    # Get response with visible execution phases
                    with self.console.status("[dim]Preparing request...[/dim]", spinner="dots") as status:
                        t0 = time.monotonic()
                        backend_name = self.agent.backend.name
                        est_ctx = self._estimate_context_tokens()
                        status.update(f"[dim]Using backend: {backend_name} | ctx~{est_ctx:,} tok[/dim]")

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

                        effective_input = user_input
                        if permission_granted:
                            effective_input = (
                                "Permission granted: you may create/modify files under ~/Documents for this task.\n\n"
                                + user_input
                            )

                        if backend_name == "codex-cli" and hasattr(self.agent.backend, "complete_with_progress"):
                            context = self.agent.workspace.get_context(mode="direct")
                            system_prompt = self.agent._build_system_prompt(context, is_heartbeat=False)

                            prior = self.agent.backend.get_history()[-12:]
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
                            self.agent.backend.add_message(Message(role="assistant", content=resp.content))
                            self.agent.state.message_count += 1
                            self.agent.state.last_activity = datetime.now()
                            response = resp.content
                        elif backend_name == "openai-codex-oauth" and hasattr(self.agent.backend, "complete_with_progress"):
                            context = self.agent.workspace.get_context(mode="direct")
                            system_prompt = self.agent._build_system_prompt(context, is_heartbeat=False)

                            prior = self.agent.backend.get_history()[-12:]
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
                            self.agent.backend.add_message(Message(role="assistant", content=resp.content))
                            self.agent.state.message_count += 1
                            self.agent.state.last_activity = datetime.now()
                            response = resp.content
                        else:
                            status.update("[dim]Waiting for model response...[/dim]")
                            response = self.agent.handle_message(effective_input)

                        elapsed = time.monotonic() - t0

                    # Optional auto-apply for coding-assistant style file actions.
                    if self._should_auto_apply(user_input):
                        script = self._extract_bash_block(response)
                        diff_block = self._extract_diff_block(response)

                        # Always require actionable patch/script for edit intents.
                        if not diff_block and not script:
                            diff_req = self.agent.handle_message(
                                "Provide only an actionable patch for the requested edit. Prefer a single ```diff``` block. No prose."
                            )
                            diff_block = self._extract_diff_block(diff_req)
                            if not diff_block:
                                script = self._extract_bash_block(diff_req)

                        if script or diff_block:
                            decision = self._approval_mode
                            if self._approval_mode == "ask":
                                self.console.print("\n  [command]Edit proposal detected.[/command]")
                                preview = diff_block or script or "(no preview)"
                                self.console.print(Panel(
                                    Markdown(f"```\n{preview[:4000]}\n```"),
                                    title="Proposed changes",
                                    border_style="blue",
                                ))
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
                                if diff_block:
                                    apply_result = self._apply_unified_diff(diff_block)
                                else:
                                    apply_result = self._auto_apply_shell_script(script or "")
                                response = f"{response}\n\n---\n**Auto-apply:** {apply_result}"
                            else:
                                response = f"{response}\n\n---\n**Auto-apply:** denied by user preference"
                                alt = self.agent.handle_message(
                                    "User denied the proposed file edits. Provide next best steps without modifying files."
                                )
                                response = f"{response}\n\n**Alternative plan:**\n{alt}"
                        else:
                            # Prevent fake "done" claims when no patch exists.
                            if self._looks_like_done_claim(response):
                                response = (
                                    f"{response}\n\n---\n"
                                    "**Auto-apply:** NOT_APPLIED (no actionable diff/script produced)."
                                )

                    response = f"{response}\n\n{self._workspace_changes_summary()}"

                    # Display response
                    self.console.print()
                    self.console.print(Panel(
                        Markdown(response),
                        title=f"[assistant]Assistant[/assistant] [dim]({elapsed:.1f}s)[/dim]",
                        border_style="green",
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
