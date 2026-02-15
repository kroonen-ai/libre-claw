"""Entry point for Libre Claw."""

import argparse
import subprocess
from pathlib import Path

import uvicorn

from .agent import Agent
from .api import create_app
from .backends import BackendConfig, get_backend
from .config import Config
from .memory import MemoryManager
from .tui import start_tui
from .workspace import Workspace


def main():
    """Main entry point for Libre Claw."""
    parser = argparse.ArgumentParser(
        description="Libre Claw - Agentic AI Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--config",
        "-c",
        help="Path to config file",
    )

    parser.add_argument(
        "--api",
        action="store_true",
        help="Start HTTP API server",
    )

    parser.add_argument(
        "--api-host",
        default="0.0.0.0",
        help="API server host",
    )

    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="API server port",
    )

    parser.add_argument(
        "--workspace",
        "-w",
        help="Path to workspace directory",
    )

    parser.add_argument(
        "--backend",
        choices=["claude_code", "codex_cli", "ollama", "anthropic", "openai"],
        help="Backend to use",
    )

    parser.add_argument(
        "--init",
        nargs="?",
        const=".",
        help="Initialize workspace at path",
    )

    parser.add_argument(
        "--heartbeat",
        action="store_true",
        help="Start with heartbeat enabled",
    )

    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Disable git sync",
    )

    args = parser.parse_args()

    # Handle workspace initialization
    if args.init:
        init_path = Path(args.init).expanduser().resolve()
        workspace = Workspace(str(init_path))

        # Load config for git settings
        config = Config.load(args.config)
        if args.no_git:
            config.git.enabled = False

        workspace.config = config
        workspace.init(force=False)
        print(f"Initialized workspace at: {workspace.path}")
        return

    # Determine workspace path first (default: repo-local .workspace)
    # If launched from inside .workspace, keep using that workspace (avoid nesting .workspace/.workspace)
    cwd = Path.cwd().resolve()
    if cwd.name == ".workspace":
        default_workspace = str(cwd)
    else:
        default_workspace = str((cwd / ".workspace").resolve())
    selected_workspace = args.workspace or default_workspace

    # Load configuration (workspace config takes precedence over global)
    config = Config.load(args.config, workspace_path=selected_workspace)

    # Override config with CLI args
    config.workspace.path = selected_workspace

    if args.backend:
        config.backend.type = args.backend
    else:
        # OpenClaw-like default: if Codex OAuth is active, use codex_cli automatically.
        try:
            codex_bin = config.backend.codex_path or "codex"
            status = subprocess.run([codex_bin, "login", "status"], capture_output=True, text=True, timeout=10)
            if status.returncode == 0:
                config.backend.type = "codex_cli"
        except Exception:
            pass

    if args.no_git:
        config.git.enabled = False

    # Ensure workspace exists and has baseline files/config
    ws = Workspace(config.workspace.path, config)
    if not ws.exists:
        ws.init(force=False)

    if args.api:
        # Start API server
        app = create_app(config)
        uvicorn.run(
            app,
            host=args.api_host,
            port=args.api_port,
            log_level="info",
        )
    else:
        # Start TUI
        backend = get_backend(
            config.backend.type,
            BackendConfig(
                claude_path=config.backend.claude_path,
                codex_path=config.backend.codex_path,
                codex_model=config.backend.codex_model,
                anthropic_api_key=config.backend.anthropic_api_key,
                anthropic_auth_file=config.backend.anthropic_auth_file,
                anthropic_model=config.backend.anthropic_model,
                anthropic_base_url=config.backend.anthropic_base_url,
                openai_api_key=config.backend.openai_api_key,
                openai_auth_file=config.backend.openai_auth_file,
                openai_model=config.backend.openai_model,
                openai_base_url=config.backend.openai_base_url,
                ollama_url=config.backend.ollama_url,
                ollama_model=config.backend.ollama_model,
            ),
        )

        workspace = ws
        memory = None
        if config.memory.enabled:
            memory = MemoryManager(config.memory.chromadb_url)

        agent = Agent(
            backend=backend,
            workspace=workspace,
            config=config,
            memory=memory,
        )

        # Start proactive heartbeat loop if enabled
        if args.heartbeat or config.heartbeat.enabled:
            agent.start_proactive()

        start_tui(agent, config)


if __name__ == "__main__":
    main()
