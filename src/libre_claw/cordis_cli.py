# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""User-controlled local Cordis plugin management. Never model-callable."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import click

from libre_claw.config import LibreClawConfig, load_config
from libre_claw.core.cordis import CordisManager


def manager_for(config: LibreClawConfig) -> CordisManager:
    return CordisManager(tool_timeout=config.cordis.tool_timeout)


def plugin_status(config: LibreClawConfig) -> dict:
    return {
        "enabled": config.cordis.enabled,
        "workspace": str(config.general.working_directory.resolve()),
        "plugins": manager_for(config).list_plugins(config.general.working_directory),
        "privacy": {
            "telemetry": False,
            "history_shared": False,
            "credentials_inherited": False,
            "default_network": "denied",
        },
    }


def format_plugins(payload: dict) -> str:
    lines = ["Cordis plugins", "No telemetry. No inherited credentials or conversation history."]
    if not payload["enabled"]:
        lines.append("Cordis tools are disabled in configuration.")
    for plugin in payload["plugins"]:
        state = "enabled" if plugin.get("enabled") else "disabled"
        lines.append(f"- {plugin['id']} · {plugin.get('version', '')} · {state} · integrity: {plugin.get('integrity', 'unknown')}")
    if not payload["plugins"]:
        lines.append("No plugins installed. Create one with `libre-claw cordis new ./my-plugin`.")
    lines.append("Commands: /plugins, /plugins inspect <id>, /plugins enable <id>, /plugins disable <id>.")
    return "\n".join(lines)


async def plugin_command(config: LibreClawConfig, argument: str) -> str:
    parts = argument.split()
    if not parts or parts == ["list"]:
        return format_plugins(plugin_status(config))
    if len(parts) != 2 or parts[0] not in {"enable", "disable", "inspect"}:
        return "Use /plugins [list|inspect <id>|enable <id>|disable <id>]. Install local packages with libre-claw cordis install."
    action, plugin_id = parts
    manager = manager_for(config)
    workspace = config.general.working_directory
    if action == "disable":
        manager.disable(plugin_id, workspace)
        return f"Disabled {plugin_id} for this project. Existing tool grants are revoked."
    if not config.cordis.enabled:
        raise ValueError("Cordis is disabled. Set [cordis].enabled = true before enabling or inspecting plugins.")
    if action == "enable":
        manager.enable(plugin_id, workspace)
        return f"Enabled {plugin_id} offline for this project. Start a new task to discover its tools."
    return json.dumps(await manager.inspect(plugin_id, workspace), indent=2)


@click.group("cordis")
def cordis_group() -> None:
    """Manage local Cordis plugins and their project-specific grants."""


def _config(ctx: click.Context) -> LibreClawConfig:
    obj = ctx.find_root().obj or {}
    return load_config(config_path=obj.get("config_path"), working_directory=obj.get("working_directory"))


def _print(value: object) -> None:
    click.echo(json.dumps(value, indent=2))


@cordis_group.command("list")
@click.pass_context
def list_command(ctx: click.Context) -> None:
    """List installed plugins without executing them."""
    try:
        _print(plugin_status(_config(ctx)))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("install")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.pass_context
def install_command(ctx: click.Context, source: Path) -> None:
    """Copy a local plugin into the user store. Does not run or enable it."""
    try:
        _print(manager_for(_config(ctx)).install(source))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("enable")
@click.argument("plugin_id")
@click.option("--allow-network", is_flag=True, help="Explicitly permit network access to any host.")
@click.option("--read", "read_paths", multiple=True, type=click.Path(exists=True, path_type=Path), help="Grant reads of this exact file or directory tree.")
@click.option("--write", "write_paths", multiple=True, type=click.Path(exists=True, path_type=Path), help="Grant writes in addition to private plugin state.")
@click.pass_context
def enable_command(ctx: click.Context, plugin_id: str, allow_network: bool, read_paths: tuple[Path, ...], write_paths: tuple[Path, ...]) -> None:
    """Enable a trusted plugin for this project. Offline without extra grants."""
    try:
        config = _config(ctx)
        if not config.cordis.enabled:
            raise ValueError("Cordis is disabled in configuration.")
        _print(manager_for(config).enable(plugin_id, config.general.working_directory,
            allow_network=allow_network, read_paths=read_paths, write_paths=write_paths))
        click.echo("Start a new task to discover newly enabled tools. Tool calls still require normal approval.")
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("disable")
@click.argument("plugin_id")
@click.pass_context
def disable_command(ctx: click.Context, plugin_id: str) -> None:
    """Revoke a plugin's tools for this project."""
    try:
        config = _config(ctx)
        _print(manager_for(config).disable(plugin_id, config.general.working_directory))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("inspect")
@click.argument("plugin_id")
@click.pass_context
def inspect_command(ctx: click.Context, plugin_id: str) -> None:
    """Mount an enabled plugin under its grants and inspect its Cordis runtime."""
    try:
        config = _config(ctx)
        if not config.cordis.enabled:
            raise ValueError("Cordis is disabled in configuration.")
        _print(asyncio.run(manager_for(config).inspect(plugin_id, config.general.working_directory)))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("remove")
@click.argument("plugin_id")
@click.pass_context
def remove_command(ctx: click.Context, plugin_id: str) -> None:
    """Remove an installed plugin and its local state."""
    try:
        _print(manager_for(_config(ctx)).remove(plugin_id))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


_EXAMPLE = '''export default {
  name: "local-word-count",
  inject: ["libre"],
  apply(ctx) {
    ctx.libre.registerTool({
      name: "count_words",
      description: "Count words in supplied text locally without network access.",
      input_schema: {
        type: "object", properties: {text: {type: "string", maxLength: 50000}},
        required: ["text"], additionalProperties: false
      }
    }, async ({text}) => {
      if (typeof text !== "string" || text.length > 50000) throw new Error("Invalid text");
      return {content: JSON.stringify({words: text.trim() ? text.trim().split(/\\s+/u).length : 0})};
    });
  }
};
'''


@cordis_group.command("new")
@click.argument("directory", type=click.Path(path_type=Path))
@click.option("--id", "plugin_id", default="local-word-count", show_default=True)
def new_command(directory: Path, plugin_id: str) -> None:
    """Create an offline example plugin without installing or running it."""
    if (not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", plugin_id) or "__" in plugin_id
            or len(f"cordis__{plugin_id}__count_words") > 64):
        raise click.ClickException("Use a lowercase example plugin ID of at most 43 characters, without double underscores.")
    if directory.exists():
        raise click.ClickException("The destination already exists. Choose a new directory.")
    manifest = {
        "id": plugin_id, "name": "Local word count", "version": "1.0.0", "entry": "plugin.mjs",
        "tools": [{"name": "count_words", "description": "Count words in supplied text locally without network access.",
                   "input_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 50000}},
                                    "required": ["text"], "additionalProperties": False}}],
        "config": {},
    }
    try:
        directory.mkdir(parents=True, mode=0o700)
        (directory / "libre-claw-plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (directory / "plugin.mjs").write_text(_EXAMPLE)
        click.echo(f"Created {directory}. Review it, then use libre-claw cordis install and enable.")
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc
