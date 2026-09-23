# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""User-controlled local Cordis plugin management. Never model-callable."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from typing import TextIO

import click

from libre_claw.config import LibreClawConfig, load_config
from libre_claw.core.cordis import CordisManager
from libre_claw.core.cordis_packages import CordisPackagePreviews, catalog
from libre_claw.core.cordis_engine_plugins import engine_for


def manager_for(config: LibreClawConfig, *, persistent: bool = False) -> CordisManager:
    return CordisManager(tool_timeout=config.cordis.tool_timeout, config=config, persistent=persistent)


async def _runtime_operation(config, operation, manager=None):
    """Executable CLI plugin operations own a core graph; metadata stays offline."""
    if manager is not None and manager.engine is not None:
        return await operation(manager)
    manager = manager or manager_for(config)
    async with engine_for(config) as engine:
        manager.engine = engine
        try:
            return await operation(manager)
        finally:
            close = getattr(manager, "aclose", None)
            if close is not None:
                await close()


def plugin_status(config: LibreClawConfig, *, manager: CordisManager | None = None) -> dict:
    return {
        "enabled": config.cordis.enabled,
        "workspace": str(config.general.working_directory.resolve()),
        "plugins": (manager or manager_for(config)).list_plugins(config.general.working_directory),
        "catalog": catalog(),
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
        lines.append("No plugins installed. Use /plugins catalog or open Plugins in the dashboard.")
    lines.append("Commands: /plugins catalog|install|details|inspect|enable|disable. Configure plugins in the dashboard.")
    return "\n".join(lines)


async def plugin_command(config: LibreClawConfig, argument: str, *, manager: CordisManager | None = None) -> str:
    parts = shlex.split(argument)
    if not parts or parts == ["list"]:
        return format_plugins(plugin_status(config))
    if parts == ["catalog"]:
        return "Included plugins\n" + "\n".join(
            f"- {item['name']}: {item['description']}\n  /plugins install {item['source']}" for item in catalog())
    allow_model = len(parts) == 3 and parts[0] == "enable" and parts[2] == "--allow-model"
    if allow_model:
        parts = parts[:2]
    if len(parts) != 2 or parts[0] not in {"enable", "disable", "inspect", "details", "install"}:
        return "Use /plugins catalog, /plugins install <source>, or /plugins details|inspect|enable|disable <id>. Quote paths containing spaces."
    action, plugin_id = parts
    manager = manager or manager_for(config)
    workspace = config.general.working_directory
    if action == "install":
        plugin = await install_package(config, plugin_id)
        state = ("It remains enabled for this project with its existing permissions."
                 if plugin["enabled"] else f"It is disabled. Use /plugins enable {plugin['id']} to enable it offline.")
        return f"Installed {plugin['name']} {plugin['version']}. {state}"
    if action == "details":
        return json.dumps(await asyncio.to_thread(manager.details, plugin_id, workspace), indent=2)
    if action == "disable":
        manager.disable(plugin_id, workspace)
        return f"Disabled {plugin_id} for this project. Existing tool grants are revoked."
    if not config.cordis.enabled:
        raise ValueError("Cordis is disabled. Set [cordis].enabled = true before enabling or inspecting plugins.")
    if action == "enable":
        await _runtime_operation(config, lambda selected: selected.enable_async(plugin_id, workspace, allow_model=allow_model), manager)
        return f"Enabled {plugin_id} for this project. Direct network access is denied. Its tools are available on your next message."
    return json.dumps(await _runtime_operation(config, lambda selected: selected.inspect(plugin_id, workspace), manager), indent=2)


@click.group("cordis")
def cordis_group() -> None:
    """Manage local plugins and their project access."""


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
@click.argument("source")
@click.pass_context
def install_command(ctx: click.Context, source: str) -> None:
    """Install a folder, archive, included plugin, or npm:package. New plugins stay off."""
    try:
        _print(asyncio.run(install_package(_config(ctx), _cli_package_source(source))))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


def _cli_package_source(source: str) -> str:
    if source.startswith(("builtin:", "npm:", "github:")) or "://" in source:
        return source
    if not source.startswith((".", "~", "/")) and not Path(source).exists() and re.fullmatch(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*(?:@[^/\s]+)?", source):
        return source
    return str(Path(source).expanduser().absolute())


async def install_package(config: LibreClawConfig, source: str) -> dict:
    previews = CordisPackagePreviews(manager_for(config))
    try:
        preview = await previews.preview(source, config.general.working_directory)
        return previews.install(preview["token"], config.general.working_directory)
    finally:
        previews.close()


@cordis_group.command("catalog")
def catalog_command() -> None:
    """Show included plugins without making network requests."""
    _print(catalog())


@cordis_group.command("preview")
@click.argument("source")
@click.pass_context
def preview_command(ctx: click.Context, source: str) -> None:
    """Check package metadata without installing or executing it."""
    async def preview() -> dict:
        config = _config(ctx)
        previews = CordisPackagePreviews(manager_for(config))
        try:
            result = await previews.preview(_cli_package_source(source), config.general.working_directory)
            return {key: value for key, value in result.items() if key not in {"token", "expires_at"}}
        finally:
            previews.close()
    try:
        _print(asyncio.run(preview()))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("config")
@click.argument("plugin_id")
@click.option("--file", "config_file", type=click.File("r"), help="Save a JSON configuration file; use - for standard input.")
@click.pass_context
def config_command(ctx: click.Context, plugin_id: str, config_file: TextIO | None) -> None:
    """Show redacted settings, or save project settings without changing grants."""
    try:
        config = _config(ctx)
        manager = manager_for(config)
        if config_file is None:
            result = manager.details(plugin_id, config.general.working_directory)
        else:
            submitted = json.load(config_file)
            result = asyncio.run(_runtime_operation(config, lambda selected: selected.configure_async(
                plugin_id, config.general.working_directory, submitted), manager))
        _print(result)
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("enable")
@click.argument("plugin_id")
@click.option("--allow-network", is_flag=True, help="Explicitly permit network access to any host.")
@click.option("--allow-model", is_flag=True, help="Permit model calls through Libre Claw's providers. Keys stay in Libre Claw.")
@click.option("--allow-engine", is_flag=True, help="Permit declared core service replacements. Restart the engine after reviewing this grant.")
@click.option("--allow-client", is_flag=True, help="Permit the declared client module in an isolated offline UI guest.")
@click.option("--read", "read_paths", multiple=True, type=click.Path(exists=True, path_type=Path), help="Grant reads of this exact file or directory tree.")
@click.option("--write", "write_paths", multiple=True, type=click.Path(exists=True, path_type=Path), help="Grant writes in addition to private plugin state.")
@click.pass_context
def enable_command(ctx: click.Context, plugin_id: str, allow_network: bool, allow_model: bool, allow_engine: bool, allow_client: bool, read_paths: tuple[Path, ...], write_paths: tuple[Path, ...]) -> None:
    """Enable a trusted plugin for this project. Offline without extra grants."""
    try:
        config = _config(ctx)
        if not config.cordis.enabled:
            raise ValueError("Cordis is disabled in configuration.")
        _print(asyncio.run(_runtime_operation(config, lambda selected: selected.enable_async(plugin_id, config.general.working_directory,
            allow_network=allow_network, allow_model=allow_model, read_paths=read_paths, write_paths=write_paths,
            **({"allow_engine": True} if allow_engine else {}), **({"allow_client": True} if allow_client else {})))))
        click.echo("Enabled tools are available on your next message. Tool calls still require normal approval.", err=True)
        if allow_engine:
            click.echo("Core service access granted. Restart the engine to load the reviewed service replacements.", err=True)
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
        _print(asyncio.run(_runtime_operation(config, lambda selected: selected.inspect(plugin_id, config.general.working_directory))))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cordis_group.command("remove")
@click.argument("plugin_id")
@click.pass_context
def remove_command(ctx: click.Context, plugin_id: str) -> None:
    """Remove an installed plugin and its local state."""
    try:
        _print(asyncio.run(manager_for(_config(ctx)).remove_async(plugin_id)))
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
