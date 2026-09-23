# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Reviewed Cordis core extensions; registrations carry no application payloads."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Callable

from libre_claw.config import LibreClawConfig

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")


def validate_engine_declaration(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"entry", "services"}:
        raise ValueError("Engine declarations require entry and services.")
    entry = value["entry"]
    if (not isinstance(entry, str) or not entry or "\\" in entry or "\0" in entry
            or PurePosixPath(entry).is_absolute()
            or any(part.startswith(".") for part in entry.split("/"))
            or any(not part for part in entry.split("/"))
            or PurePosixPath(entry).suffix not in {".mjs", ".js"}):
        raise ValueError("Engine entry must be a relative JavaScript module inside the package.")
    rows = value["services"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError("Declare between one and sixteen engine services.")
    seen = set()
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "title", "dependencies", "methods"}:
            raise ValueError("Each engine service requires id, title, dependencies, and methods.")
        identity = row["id"]
        if not isinstance(identity, str) or not _IDENTIFIER.fullmatch(identity) or identity in seen:
            raise ValueError("Engine service IDs must be unique identifiers.")
        seen.add(identity)
        if not isinstance(row["title"], str) or not 1 <= len(row["title"]) <= 160:
            raise ValueError("Engine service titles must contain 1–160 characters.")
        for name, minimum, maximum in (("methods", 1, 32), ("dependencies", 0, 16)):
            values = row[name]
            if (not isinstance(values, list) or not minimum <= len(values) <= maximum
                    or not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in values)
                    or len(values) != len(set(values))):
                raise ValueError(f"Engine service {name} must be bounded unique identifiers.")
        normalized.append({key: list(item) if isinstance(item, list) else item for key, item in row.items()})
    return {"entry": entry, "services": normalized}


def engine_plugin_specs(config: LibreClawConfig) -> list[dict[str, Any]]:
    from libre_claw.core.cordis import CordisManager, _workspace

    if not config.cordis.enabled:
        return []
    manager = CordisManager(config=config)
    _, workspace_key = _workspace(config.general.working_directory)
    specs = []
    for plugin_id, record in sorted(manager._read_registry()["plugins"].items()):
        grants = record.get("workspaces", {}).get(workspace_key, {})
        if grants.get("allow_engine") is not True:
            continue
        root, manifest = manager._verify(plugin_id, record)
        declaration = validate_engine_declaration(manifest.get("engine"))
        if declaration["entry"] not in record["files"]:
            raise ValueError("The approved engine entry is missing from its verified snapshot.")
        specs.append({"plugin_id": plugin_id, "digest": record["digest"],
                      "revision": record.get("revisions", {}).get(workspace_key, 0),
                      "root": str(root), **declaration,
                      "config": manager._effective_config(record, manifest, workspace_key)})
    return specs


def engine_for(config: LibreClawConfig | Callable[[], LibreClawConfig]):
    from libre_claw.core.cordis_engine import CordisEngine

    getter = config if callable(config) else lambda: config
    return CordisEngine(plugin_loader=lambda: engine_plugin_specs(getter()))
