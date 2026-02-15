"""Configuration management for Libre Claw.

Loads settings from YAML config files with Pydantic validation.
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class BackendConfig(BaseModel):
    """Backend configuration."""

    type: str = Field(default="claude_code", description="Backend type: claude_code, codex_cli, anthropic, openai, ollama")
    claude_path: str = Field(default="/opt/homebrew/bin/claude", description="Path to Claude Code CLI")
    codex_path: str = Field(default="/opt/homebrew/bin/codex", description="Path to Codex CLI")
    codex_model: Optional[str] = Field(default=None, description="Codex CLI model override")
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key (sk-ant-*)")
    anthropic_auth_file: Optional[str] = Field(default="~/.config/libre-claw/auth/anthropic.json", description="Path to Anthropic auth JSON")
    anthropic_model: str = Field(default="claude-3-7-sonnet-latest", description="Anthropic model id")
    anthropic_base_url: str = Field(default="https://api.anthropic.com/v1", description="Anthropic API base URL")

    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    openai_auth_file: Optional[str] = Field(default="~/.config/libre-claw/auth/openai.json", description="Path to OpenAI auth JSON with access_token")
    openai_model: str = Field(default="gpt-4.1", description="OpenAI model id")
    openai_base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI API base URL")

    ollama_url: str = Field(default="http://localhost:11434", description="Ollama API URL")
    ollama_model: str = Field(default="llama2", description="Default Ollama model")


class WorkspaceConfig(BaseModel):
    """Workspace configuration."""

    path: str = Field(default="~/.libre-claw/workspace", description="Workspace directory path")


class HeartbeatConfig(BaseModel):
    """Heartbeat configuration."""

    enabled: bool = Field(default=True, description="Enable heartbeat system")
    interval_seconds: int = Field(default=30, description="Heartbeat poll interval")
    prompt: str = Field(
        default="Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
        "Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK.",
        description="Heartbeat poll prompt"
    )


class MemoryConfig(BaseModel):
    """Memory configuration."""

    chromadb_url: str = Field(default="http://stargate.local:8420", description="ChromaDB server URL")
    enabled: bool = Field(default=True, description="Enable memory integration")


class GitConfig(BaseModel):
    """Git sync configuration."""

    enabled: bool = Field(default=True, description="Enable git sync")
    auto_commit: bool = Field(default=True, description="Auto-commit changes")
    commit_message: str = Field(default="sync: workspace update", description="Default commit message")
    remote: Optional[str] = Field(default=None, description="Git remote (e.g., origin)")


class APIServerConfig(BaseModel):
    """API server configuration."""

    host: str = Field(default="0.0.0.0", description="API server host")
    port: int = Field(default=8000, description="API server port")
    reload: bool = Field(default=False, description="Enable auto-reload")


class Config(BaseSettings):
    """Main configuration class."""

    backend: BackendConfig = Field(default_factory=BackendConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    api: APIServerConfig = Field(default_factory=APIServerConfig)

    config_file: Optional[Path] = Field(default=None, description="Path to config file")

    model_config = {
        "env_prefix": "LIBRE_CLAW_",
        "env_nested_delimiter": "__",
    }

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load configuration from a YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        # Remove config_file from data if present (it's set explicitly below)
        data.pop("config_file", None)

        return cls(**data, config_file=path)

    @classmethod
    def load(cls, config_path: Optional[str] = None, workspace_path: Optional[str] = None) -> "Config":
        """Load configuration from default or specified path.

        Searches for config in:
        1. Specified path
        2. <workspace>/config.yaml (if workspace provided)
        3. ./config.yaml
        4. ~/.config/libre-claw/config.yaml
        5. Environment variables
        """
        if config_path:
            path = Path(config_path).expanduser()
            if path.exists():
                return cls.from_yaml(path)

        if workspace_path:
            ws_config = Path(workspace_path).expanduser() / "config.yaml"
            if ws_config.exists():
                return cls.from_yaml(ws_config)

        # Check current directory
        local_config = Path("config.yaml")
        if local_config.exists():
            return cls.from_yaml(local_config)

        # Check user config directory
        user_config = Path.home() / ".config" / "libre-claw" / "config.yaml"
        if user_config.exists():
            return cls.from_yaml(user_config)

        return cls()

    def save(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)
