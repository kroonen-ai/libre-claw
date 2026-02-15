"""Terminal User Interface for Libre Claw.

Rich-based TUI with slash commands, streaming output, and a polished experience.
"""

import time
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .agent import Agent, AgentMode
from .config import Config

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
        "mode": "Show or switch mode (usage: /mode [direct|heartbeat])",
        "context": "Show loaded workspace context files",
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

        elif cmd == "context":
            ctx = self.agent.workspace.get_context(self.agent.state.mode.value)
            if ctx:
                for filename in ctx:
                    size = len(ctx[filename])
                    self.console.print(f"  [dim]●[/dim] {filename} ({size:,} chars)")
            else:
                self.console.print("  [system]No context files loaded[/system]")

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

                # Get response with spinner
                with self.console.status("[dim]Thinking...[/dim]", spinner="dots"):
                    t0 = time.monotonic()
                    response = self.agent.handle_message(user_input)
                    elapsed = time.monotonic() - t0

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


def start_tui(agent: Agent, config: Optional[Config] = None) -> None:
    """Start the TUI."""
    tui = TUI(agent, config)
    tui.run()
