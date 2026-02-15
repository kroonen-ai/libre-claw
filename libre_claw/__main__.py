"""Entry point for Libre Claw."""

import argparse
import asyncio
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
        choices=["claude_code", "ollama", "anthropic"],
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

    # Load configuration
    config = Config.load(args.config)

    # Override config with CLI args
    if args.workspace:
        config.workspace.path = args.workspace

    if args.backend:
        config.backend.type = args.backend

    if args.no_git:
        config.git.enabled = False

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
                anthropic_api_key=config.backend.anthropic_api_key,
                ollama_url=config.backend.ollama_url,
                ollama_model=config.backend.ollama_model,
            ),
        )

        workspace = Workspace(config.workspace.path, config)
        memory = None
        if config.memory.enabled:
            memory = MemoryManager(config.memory.chromadb_url)

        agent = Agent(
            backend=backend,
            workspace=workspace,
            config=config,
            memory=memory,
        )

        # Start heartbeat if enabled
        if args.heartbeat or config.heartbeat.enabled:
            asyncio.run(agent.start_heartbeat())

        start_tui(agent, config)


if __name__ == "__main__":
    main()
