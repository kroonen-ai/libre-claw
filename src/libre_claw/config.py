# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from importlib import resources
import json
from pathlib import Path
import re
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10 and earlier.
    import tomli as tomllib


ConfigTable = dict[str, Any]


class ConfigError(RuntimeError):
    """Raised when Libre Claw configuration cannot be loaded."""


@dataclass(frozen=True)
class GeneralConfig:
    default_provider: str
    default_model: str
    working_directory: Path
    theme: str
    log_level: str


@dataclass(frozen=True)
class AgentConfig:
    max_tool_calls_per_turn: int
    auto_compact_threshold: float
    context_window_tokens: int
    provider_retry_attempts: int
    provider_retry_initial_delay: float
    system_prompt: str
    system_prompt_extra: str


@dataclass(frozen=True)
class PermissionsConfig:
    default_level: str
    auto_approve_read: bool


@dataclass(frozen=True)
class SandboxConfig:
    command_timeout: int
    allow_sudo: bool
    blocked_patterns: tuple[str, ...]
    restrict_to_working_dir: bool


@dataclass(frozen=True)
class AuthConfig:
    keyring_service: str
    fallback_keys_path: Path
    jwt_secret_env: str
    oauth_issuer: str
    oauth_client_id: str
    oauth_redirect_uri: str
    token_ttl_seconds: int


@dataclass(frozen=True)
class TUIConfig:
    show_file_tree: bool
    show_status_bar: bool
    vim_keybindings: bool
    use_daemon: bool
    mouse: bool
    inline: bool


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    use_daemon: bool
    bot_token_env: str
    allowed_user_ids: tuple[int, ...]
    max_message_length: int
    stream_update_interval: float
    default_provider: str
    default_model: str


@dataclass(frozen=True)
class GoalConfig:
    max_turns: int
    judge_provider: str
    judge_model: str
    judge_temperature: float
    judge_max_tokens: int


@dataclass(frozen=True)
class FallbackRouteConfig:
    provider: str
    model: str
    api_key_env: str


@dataclass(frozen=True)
class FallbackConfig:
    enabled: bool
    routes: tuple[FallbackRouteConfig, ...]


@dataclass(frozen=True)
class HeartbeatConfig:
    enabled: bool
    interval_minutes: int
    route: str
    telegram_chat_id: int
    provider: str
    model: str
    checklist: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool
    auto_extract: bool
    auto_summarize: bool
    inject_relevant: bool
    max_injected_items: int
    max_injected_tokens: int
    redact_secrets: bool
    archive_sessions: bool


@dataclass(frozen=True)
class DaemonConfig:
    host: str
    port: int
    poll_interval: float


@dataclass(frozen=True)
class AutomationsConfig:
    enabled: bool
    root: Path
    poll_interval: float
    max_due_per_tick: int
    auto_approve_tools: tuple[str, ...]
    finalizer_max_tokens: int
    finalizer_max_context_chars: int


@dataclass(frozen=True)
class BrowserConfig:
    allowed_domains: tuple[str, ...]
    denied_domains: tuple[str, ...]
    profile_dir: Path
    downloads_dir: Path
    screenshots_dir: Path
    default_timeout_ms: int
    headless: bool


@dataclass(frozen=True)
class MCPConfig:
    enabled: bool
    allowlist: tuple[str, ...]
    permission_level: str
    tool_timeout: int
    servers: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class LibreClawConfig:
    general: GeneralConfig
    agent: AgentConfig
    permissions: PermissionsConfig
    sandbox: SandboxConfig
    auth: AuthConfig
    tui: TUIConfig
    telegram: TelegramConfig
    goal: GoalConfig
    fallback: FallbackConfig
    heartbeat: HeartbeatConfig
    memory: MemoryConfig
    daemon: DaemonConfig
    automations: AutomationsConfig
    browser: BrowserConfig
    mcp: MCPConfig
    providers: Mapping[str, Mapping[str, Any]]
    source_paths: tuple[Path, ...] = field(default_factory=tuple)


ENV_OVERRIDES: Mapping[str, tuple[str, str]] = {
    "LIBRE_CLAW_DEFAULT_PROVIDER": ("general", "default_provider"),
    "LIBRE_CLAW_DEFAULT_MODEL": ("general", "default_model"),
    "LIBRE_CLAW_WORKING_DIRECTORY": ("general", "working_directory"),
    "LIBRE_CLAW_THEME": ("general", "theme"),
    "LIBRE_CLAW_LOG_LEVEL": ("general", "log_level"),
}


def load_config(
    config_path: Path | str | None = None,
    working_directory: Path | str | None = None,
) -> LibreClawConfig:
    """Load defaults, optional user TOML, environment overrides, and CLI overrides."""
    data = _load_default_config()
    source_paths: list[Path] = []

    default_path = default_config_path()
    if default_path.exists():
        source_paths.append(default_path)

    user_path = _resolve_user_config_path(config_path)
    if user_path is not None:
        user_data = _read_toml(user_path)
        _deep_merge(data, user_data)
        source_paths.append(user_path)

    _apply_environment_overrides(data)

    if working_directory is not None:
        data.setdefault("general", {})["working_directory"] = str(working_directory)

    _normalize_provider_aliases(data)

    return _build_config(data, tuple(source_paths))


def default_config_path() -> Path:
    """Return the repository default config path used for local development."""
    return Path(__file__).resolve().parents[2] / "config" / "default.toml"


def packaged_default_config_text() -> str:
    """Return the packaged default TOML used when the repo config is unavailable."""
    return resources.files("libre_claw").joinpath("default.toml").read_text(encoding="utf-8")


def user_config_path() -> Path:
    """Return the default per-user configuration path."""
    return Path.home() / ".libre-claw" / "config.toml"


def set_global_default_model(
    provider: str,
    model: str,
    config_path: Path | str | None = None,
) -> Path:
    """Persist the default provider/model in the user-level config file."""
    clean_provider = provider.strip().lower()
    clean_model = model.strip()
    if not clean_provider:
        raise ConfigError("Provider cannot be empty.")
    if not clean_model:
        raise ConfigError("Model cannot be empty.")

    path = Path(config_path).expanduser() if config_path is not None else user_config_path()
    updates = {
        "general": {
            "default_provider": clean_provider,
            "default_model": clean_model,
        },
        "telegram": {
            "default_provider": clean_provider,
            "default_model": clean_model,
        },
        f"providers.{clean_provider}": {
            "default_model": clean_model,
        },
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _update_toml_sections(existing, updates)
        tomllib.loads(updated)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        msg = f"Could not write config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Could not update config file {path}: {exc}"
        raise ConfigError(msg) from exc
    return path


def set_global_working_directory(
    working_directory: Path | str,
    config_path: Path | str | None = None,
) -> Path:
    """Persist the default working directory in the user-level config file."""
    directory = Path(working_directory).expanduser().resolve()
    path = Path(config_path).expanduser() if config_path is not None else user_config_path()
    updates = {
        "general": {
            "working_directory": str(directory),
        },
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _update_toml_sections(existing, updates)
        tomllib.loads(updated)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        msg = f"Could not write config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Could not update config file {path}: {exc}"
        raise ConfigError(msg) from exc
    return path


def set_global_theme(
    theme: str,
    config_path: Path | str | None = None,
) -> Path:
    """Persist the default UI theme in the user-level config file."""
    clean_theme = theme.strip()
    if not clean_theme:
        raise ConfigError("Theme cannot be empty.")

    path = Path(config_path).expanduser() if config_path is not None else user_config_path()
    updates = {
        "general": {
            "theme": clean_theme,
        },
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _update_toml_sections(existing, updates)
        tomllib.loads(updated)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        msg = f"Could not write config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Could not update config file {path}: {exc}"
        raise ConfigError(msg) from exc
    return path


def configure_telegram(
    allowed_user_ids: tuple[int, ...],
    *,
    enabled: bool = True,
    use_daemon: bool = True,
    bot_token_env: str = "TELEGRAM_BOT_TOKEN",
    config_path: Path | str | None = None,
) -> Path:
    """Persist Telegram bridge config without storing the bot token."""
    if not allowed_user_ids:
        raise ConfigError("At least one Telegram user ID is required.")
    if not bot_token_env.strip():
        raise ConfigError("Telegram bot token env var name cannot be empty.")

    path = Path(config_path).expanduser() if config_path is not None else user_config_path()
    updates = {
        "telegram": {
            "enabled": enabled,
            "use_daemon": use_daemon,
            "bot_token_env": bot_token_env.strip(),
            "allowed_user_ids": list(allowed_user_ids),
        },
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _update_toml_sections(existing, updates)
        tomllib.loads(updated)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        msg = f"Could not write config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Could not update config file {path}: {exc}"
        raise ConfigError(msg) from exc
    return path


def global_config_path(config: LibreClawConfig | None = None) -> Path:
    """Return the config file that `/model --global` should update."""
    if config is None:
        return user_config_path()

    repo_default = default_config_path().resolve()
    for path in reversed(config.source_paths):
        resolved = path.expanduser().resolve()
        if resolved != repo_default:
            return resolved
    return user_config_path()


def _load_default_config() -> ConfigTable:
    path = default_config_path()
    if path.exists():
        return _read_toml(path)

    try:
        return dict(tomllib.loads(packaged_default_config_text()))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass

    return {
        "general": {
            "default_provider": "anthropic",
            "default_model": "claude-opus-4-8",
            "working_directory": ".",
            "theme": "dark",
            "log_level": "info",
        },
        "agent": {
            "max_tool_calls_per_turn": 50,
            "auto_compact_threshold": 0.8,
            "context_window_tokens": 200000,
            "provider_retry_attempts": 2,
            "provider_retry_initial_delay": 1.0,
            "system_prompt": (
                "You are Libre Claw, an autonomous coding agent built by Kroonen AI "
                "(https://kroonen.ai) running in the user's terminal.\n"
                "You have access to tools for reading files, writing files, editing files, "
                "listing directories, searching code, inspecting git, browsing web pages, "
                "extracting browser data, running browser JavaScript, fetching HTTP URLs/APIs, "
                "clicking and typing in browser pages, downloading browser files, "
                "thinking through plans, and running shell commands.\n\n"
                "RULES:\n"
                "- Always read before editing. Understand the codebase before making changes.\n"
                "- Make minimal, surgical edits. Never rewrite entire files when a targeted fix suffices.\n"
                "- Explain what you're about to do before doing it, but keep it brief.\n"
                "- If a task is ambiguous, make a reasonable assumption, proceed, and note the assumption.\n"
                "- After making changes, verify them with available commands unless the user says otherwise.\n"
                "- Never delete files or run destructive commands without explicit user approval.\n"
                "- When you're done, summarize what you changed and why.\n\n"
                "Current toolset: read_file, write_file, edit_file, list_directory, "
                "glob, search_files, git_status, git_commit, think, browser_navigate, "
                "browser_read, browser_extract, browser_execute, browser_dismiss_cookies, "
                "browser_click, browser_type, browser_wait, browser_download, browser_screenshot, "
                "http_request, and bash."
            ),
            "system_prompt_extra": "",
        },
        "providers": {
            "anthropic": {
                "api_key_env": "ANTHROPIC_API_KEY",
                "default_model": "claude-opus-4-8",
                "max_tokens": 16384,
            },
            "openai": {
                "api_key_env": "OPENAI_API_KEY",
                "default_model": "gpt-5.5",
                "max_tokens": 16384,
            },
            "openrouter": {
                "api_key_env": "OPENROUTER_API_KEY",
                "base_url": "https://openrouter.ai/api/v1",
                "default_model": "openrouter/auto",
                "max_tokens": 16384,
            },
            "codex": {
                "default_model": "gpt-5.5",
                "executable": "codex",
                "sandbox": "workspace-write",
                "approval_policy": "never",
                "timeout": 900,
            },
            "ollama": {
                "base_url": "http://localhost:11434",
                "default_model": "qwen3.6:27b",
                "api_format": "ollama",
                "api_key_env": "OLLAMA_API_KEY",
                "max_tokens": 16384,
                "supports_tools": True,
                "tool_mode": "auto",
            },
        },
        "permissions": {
            "default_level": "ask",
            "auto_approve_read": True,
        },
        "sandbox": {
            "command_timeout": 120,
            "allow_sudo": False,
            "blocked_patterns": [
                "rm -rf /",
                "rm -fr /",
                "rm -rf -- /",
                ":(){ :|:& };:",
                "curl | sh",
                "curl|sh",
                "curl | bash",
                "curl|bash",
                "wget | sh",
                "wget|sh",
                "wget | bash",
                "wget|bash",
            ],
            "restrict_to_working_dir": True,
        },
        "auth": {
            "keyring_service": "libre-claw",
            "fallback_keys_path": "~/.libre-claw/.keys",
            "jwt_secret_env": "LIBRE_CLAW_JWT_SECRET",
            "oauth_issuer": "libre-claw",
            "oauth_client_id": "libre-claw-local",
            "oauth_redirect_uri": "http://127.0.0.1:8765/callback",
            "token_ttl_seconds": 3600,
        },
        "tui": {
            "show_file_tree": False,
            "show_status_bar": True,
            "vim_keybindings": False,
            "use_daemon": False,
            "mouse": False,
            "inline": False,
        },
        "telegram": {
            "enabled": False,
            "use_daemon": False,
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "allowed_user_ids": [123456789],
            "max_message_length": 4000,
            "stream_update_interval": 1.5,
            "default_provider": "anthropic",
            "default_model": "claude-opus-4-8",
        },
        "goal": {
            "max_turns": 20,
            "judge_provider": "current",
            "judge_model": "",
            "judge_temperature": 0.0,
            "judge_max_tokens": 1024,
        },
        "fallback": {
            "enabled": False,
            "routes": [],
        },
        "heartbeat": {
            "enabled": False,
            "interval_minutes": 60,
            "route": "tui",
            "telegram_chat_id": 0,
            "provider": "",
            "model": "",
            "checklist": [
                "Review active and recent runs.",
                "Check blocked approvals.",
                "Look for notable repository changes.",
                "Report risks, next actions, and anything that needs attention.",
            ],
            "prompt": "",
        },
        "memory": {
            "enabled": True,
            "auto_extract": True,
            "auto_summarize": True,
            "inject_relevant": True,
            "max_injected_items": 8,
            "max_injected_tokens": 1200,
            "redact_secrets": True,
            "archive_sessions": True,
        },
        "daemon": {
            "host": "127.0.0.1",
            "port": 8766,
            "poll_interval": 0.5,
        },
        "automations": {
            "enabled": True,
            "root": "~/.libre-claw/automations",
            "poll_interval": 30.0,
            "max_due_per_tick": 5,
            "auto_approve_tools": [
                "bash",
                "http_request",
                "read_file",
                "list_directory",
                "search_files",
                "glob",
                "browser_read",
                "browser_screenshot",
                "browser_extract",
            ],
            "finalizer_max_tokens": 3000,
            "finalizer_max_context_chars": 70000,
        },
        "browser": {
            "allowed_domains": [],
            "denied_domains": [],
            "profile_dir": "~/.libre-claw/browser/profiles",
            "downloads_dir": ".libre-claw/browser/downloads",
            "screenshots_dir": ".libre-claw/browser/screenshots",
            "default_timeout_ms": 30000,
            "headless": True,
        },
        "mcp": {
            "enabled": False,
            "allowlist": [],
            "permission_level": "ask",
            "tool_timeout": 30,
            "servers": {},
        },
    }


def _resolve_user_config_path(config_path: Path | str | None) -> Path | None:
    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.exists():
            msg = f"Config file does not exist: {path}"
            raise ConfigError(msg)
        return path

    path = user_config_path()
    if path.exists():
        return path
    return None


_SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=).*$")


def _update_toml_sections(text: str, updates: Mapping[str, Mapping[str, Any]]) -> str:
    lines = text.splitlines()
    output: list[str] = []
    seen: dict[str, set[str]] = {section: set() for section in updates}
    current_section: str | None = None

    def insert_missing(section: str | None) -> None:
        if section is None or section not in updates:
            return
        missing = [key for key in updates[section] if key not in seen[section]]
        for key in missing:
            output.append(f"{key} = {_toml_value(updates[section][key])}")
            seen[section].add(key)

    for line in lines:
        section_match = _SECTION_RE.match(line)
        if section_match:
            insert_missing(current_section)
            current_section = section_match.group(1)
            output.append(line)
            continue

        key_match = _KEY_RE.match(line)
        if key_match and current_section in updates:
            key = key_match.group(2)
            section_updates = updates[current_section]
            if key in section_updates:
                output.append(f"{key_match.group(1)}{key}{key_match.group(3)} {_toml_value(section_updates[key])}")
                seen[current_section].add(key)
                continue

        output.append(line)

    insert_missing(current_section)

    for section, values in updates.items():
        missing = [key for key in values if key not in seen[section]]
        if not missing:
            continue
        if output and output[-1] != "":
            output.append("")
        output.append(f"[{section}]")
        for key in missing:
            output.append(f"{key} = {_toml_value(values[key])}")
            seen[section].add(key)

    return "\n".join(output).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, tuple | list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)


def _read_toml(path: Path) -> ConfigTable:
    try:
        with path.open("rb") as handle:
            return dict(tomllib.load(handle))
    except OSError as exc:
        msg = f"Could not read config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Could not parse config file {path}: {exc}"
        raise ConfigError(msg) from exc


def _deep_merge(target: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> None:
    for key, value in overlay.items():
        existing = target.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _deep_merge(existing, value)
        else:
            target[key] = value


def _apply_environment_overrides(data: ConfigTable) -> None:
    for env_name, (section, key) in ENV_OVERRIDES.items():
        value = os.getenv(env_name)
        if value is not None:
            data.setdefault(section, {})[key] = value


def _normalize_provider_aliases(data: ConfigTable) -> None:
    general = data.setdefault("general", {})
    if isinstance(general, MutableMapping) and str(general.get("default_provider", "")).lower() == "local":
        general["default_provider"] = "ollama"

    telegram = data.get("telegram")
    if isinstance(telegram, MutableMapping) and str(telegram.get("default_provider", "")).lower() == "local":
        telegram["default_provider"] = "ollama"

    providers = data.get("providers")
    if not isinstance(providers, MutableMapping):
        return

    local_config = providers.pop("local", None)
    if not isinstance(local_config, Mapping):
        return

    existing = providers.get("ollama")
    if isinstance(existing, MutableMapping):
        _deep_merge(existing, local_config)
    else:
        providers["ollama"] = dict(local_config)


def _build_config(data: Mapping[str, Any], source_paths: tuple[Path, ...]) -> LibreClawConfig:
    general = _section(data, "general")
    agent = _section(data, "agent")
    permissions = _section(data, "permissions")
    sandbox = _section(data, "sandbox")
    auth = _section(data, "auth")
    tui = _section(data, "tui")
    telegram = _section(data, "telegram")
    goal = _section(data, "goal")
    fallback = _section(data, "fallback")
    heartbeat = _section(data, "heartbeat")
    memory = _section(data, "memory")
    daemon = _section(data, "daemon")
    automations = _section(data, "automations")
    browser = _section(data, "browser")
    mcp = _section(data, "mcp")

    return LibreClawConfig(
        general=GeneralConfig(
            default_provider=_str(general, "default_provider"),
            default_model=_str(general, "default_model"),
            working_directory=_path(general, "working_directory"),
            theme=_str(general, "theme"),
            log_level=_str(general, "log_level"),
        ),
        agent=AgentConfig(
            max_tool_calls_per_turn=_int(agent, "max_tool_calls_per_turn"),
            auto_compact_threshold=_float(agent, "auto_compact_threshold"),
            context_window_tokens=_int(agent, "context_window_tokens"),
            provider_retry_attempts=_int(agent, "provider_retry_attempts"),
            provider_retry_initial_delay=_float(agent, "provider_retry_initial_delay"),
            system_prompt=_str(agent, "system_prompt"),
            system_prompt_extra=_str(agent, "system_prompt_extra"),
        ),
        permissions=PermissionsConfig(
            default_level=_str(permissions, "default_level"),
            auto_approve_read=_bool(permissions, "auto_approve_read"),
        ),
        sandbox=SandboxConfig(
            command_timeout=_int(sandbox, "command_timeout"),
            allow_sudo=_bool(sandbox, "allow_sudo"),
            blocked_patterns=tuple(_list(sandbox, "blocked_patterns", str)),
            restrict_to_working_dir=_bool(sandbox, "restrict_to_working_dir"),
        ),
        auth=AuthConfig(
            keyring_service=_str(auth, "keyring_service"),
            fallback_keys_path=_path(auth, "fallback_keys_path"),
            jwt_secret_env=_str(auth, "jwt_secret_env"),
            oauth_issuer=_str(auth, "oauth_issuer"),
            oauth_client_id=_str(auth, "oauth_client_id"),
            oauth_redirect_uri=_str(auth, "oauth_redirect_uri"),
            token_ttl_seconds=_int(auth, "token_ttl_seconds"),
        ),
        tui=TUIConfig(
            show_file_tree=_bool(tui, "show_file_tree"),
            show_status_bar=_bool(tui, "show_status_bar"),
            vim_keybindings=_bool(tui, "vim_keybindings"),
            use_daemon=_bool(tui, "use_daemon"),
            mouse=_bool(tui, "mouse"),
            inline=_bool(tui, "inline"),
        ),
        telegram=TelegramConfig(
            enabled=_bool(telegram, "enabled"),
            use_daemon=_bool(telegram, "use_daemon"),
            bot_token_env=_str(telegram, "bot_token_env"),
            allowed_user_ids=tuple(_list(telegram, "allowed_user_ids", int)),
            max_message_length=_int(telegram, "max_message_length"),
            stream_update_interval=_float(telegram, "stream_update_interval"),
            default_provider=_str(telegram, "default_provider"),
            default_model=_str(telegram, "default_model"),
        ),
        goal=GoalConfig(
            max_turns=_int(goal, "max_turns"),
            judge_provider=_str(goal, "judge_provider"),
            judge_model=_str(goal, "judge_model"),
            judge_temperature=_float(goal, "judge_temperature"),
            judge_max_tokens=_int(goal, "judge_max_tokens"),
        ),
        fallback=FallbackConfig(
            enabled=_bool(fallback, "enabled"),
            routes=_fallback_routes(fallback),
        ),
        heartbeat=HeartbeatConfig(
            enabled=_bool(heartbeat, "enabled"),
            interval_minutes=_int(heartbeat, "interval_minutes"),
            route=_str(heartbeat, "route"),
            telegram_chat_id=_int(heartbeat, "telegram_chat_id"),
            provider=_str(heartbeat, "provider"),
            model=_str(heartbeat, "model"),
            checklist=tuple(_list(heartbeat, "checklist", str)),
            prompt=_str(heartbeat, "prompt"),
        ),
        memory=MemoryConfig(
            enabled=_bool(memory, "enabled"),
            auto_extract=_bool(memory, "auto_extract"),
            auto_summarize=_bool(memory, "auto_summarize"),
            inject_relevant=_bool(memory, "inject_relevant"),
            max_injected_items=_int(memory, "max_injected_items"),
            max_injected_tokens=_int(memory, "max_injected_tokens"),
            redact_secrets=_bool(memory, "redact_secrets"),
            archive_sessions=_bool(memory, "archive_sessions"),
        ),
        daemon=DaemonConfig(
            host=_str(daemon, "host"),
            port=_int(daemon, "port"),
            poll_interval=_float(daemon, "poll_interval"),
        ),
        automations=AutomationsConfig(
            enabled=_bool(automations, "enabled"),
            root=_path(automations, "root"),
            poll_interval=_float(automations, "poll_interval"),
            max_due_per_tick=_int(automations, "max_due_per_tick"),
            auto_approve_tools=tuple(_list(automations, "auto_approve_tools", str)),
            finalizer_max_tokens=_int(automations, "finalizer_max_tokens"),
            finalizer_max_context_chars=_int(automations, "finalizer_max_context_chars"),
        ),
        browser=BrowserConfig(
            allowed_domains=tuple(_list(browser, "allowed_domains", str)),
            denied_domains=tuple(_list(browser, "denied_domains", str)),
            profile_dir=_path(browser, "profile_dir"),
            downloads_dir=_path(browser, "downloads_dir"),
            screenshots_dir=_path(browser, "screenshots_dir"),
            default_timeout_ms=_int(browser, "default_timeout_ms"),
            headless=_bool(browser, "headless"),
        ),
        mcp=MCPConfig(
            enabled=_bool(mcp, "enabled"),
            allowlist=tuple(_list(mcp, "allowlist", str)),
            permission_level=_str(mcp, "permission_level"),
            tool_timeout=_int(mcp, "tool_timeout"),
            servers=_mcp_servers(mcp),
        ),
        providers=_providers(data),
        source_paths=source_paths,
    )


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        msg = f"Missing or invalid [{key}] config section"
        raise ConfigError(msg)
    return value


def _providers(data: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    value = data.get("providers", {})
    if not isinstance(value, Mapping):
        raise ConfigError("Missing or invalid [providers] config section")
    return {str(name): dict(config) for name, config in value.items() if isinstance(config, Mapping)}


def _mcp_servers(mcp: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    value = mcp.get("servers", {})
    if not isinstance(value, Mapping):
        raise ConfigError("Config value mcp.servers must be a table")
    return {str(name): dict(config) for name, config in value.items() if isinstance(config, Mapping)}


def _fallback_routes(fallback: Mapping[str, Any]) -> tuple[FallbackRouteConfig, ...]:
    raw_routes = fallback.get("routes", [])
    if not isinstance(raw_routes, list):
        raise ConfigError("Config value fallback.routes must be a list of tables")
    routes: list[FallbackRouteConfig] = []
    for index, raw_route in enumerate(raw_routes):
        if not isinstance(raw_route, Mapping):
            raise ConfigError(f"Config value fallback.routes[{index}] must be a table")
        provider = str(raw_route.get("provider", "")).strip().lower()
        model = str(raw_route.get("model", "")).strip()
        api_key_env = str(raw_route.get("api_key_env", "")).strip()
        if not provider:
            raise ConfigError(f"Config value fallback.routes[{index}].provider cannot be empty")
        routes.append(FallbackRouteConfig(provider=provider, model=model, api_key_env=api_key_env))
    return tuple(routes)


def _str(section: Mapping[str, Any], key: str) -> str:
    value = _required(section, key)
    if not isinstance(value, str):
        msg = f"Config value {key} must be a string"
        raise ConfigError(msg)
    return value


def _int(section: Mapping[str, Any], key: str) -> int:
    value = _required(section, key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"Config value {key} must be an integer"
        raise ConfigError(msg)
    return value


def _float(section: Mapping[str, Any], key: str) -> float:
    value = _required(section, key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"Config value {key} must be a number"
        raise ConfigError(msg)
    return float(value)


def _bool(section: Mapping[str, Any], key: str) -> bool:
    value = _required(section, key)
    if not isinstance(value, bool):
        msg = f"Config value {key} must be a boolean"
        raise ConfigError(msg)
    return value


def _list(section: Mapping[str, Any], key: str, item_type: type[Any]) -> list[Any]:
    value = _required(section, key)
    if not isinstance(value, list):
        msg = f"Config value {key} must be a list"
        raise ConfigError(msg)
    if not all(isinstance(item, item_type) for item in value):
        msg = f"Config value {key} contains invalid item types"
        raise ConfigError(msg)
    return value


def _path(section: Mapping[str, Any], key: str) -> Path:
    value = _str(section, key)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _required(section: Mapping[str, Any], key: str) -> Any:
    try:
        return section[key]
    except KeyError as exc:
        msg = f"Missing config value: {key}"
        raise ConfigError(msg) from exc
