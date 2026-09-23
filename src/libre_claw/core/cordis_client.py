# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Offline client-extension guests. Only a validated render tree reaches the UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from libre_claw.core.cordis import CordisManager, _workspace
from libre_claw.core.cordis_client_manifest import validate_client_declaration
from libre_claw.core.cordis_security import prepare_cordis_process
from libre_claw.core.cordis_worker import CordisWorker
from libre_claw.core.runs import settle_finalization


_RUNTIME = Path(__file__).resolve().parents[1] / "cordis_runtime" / "client.mjs"
_SCHEMA = json.loads(_RUNTIME.with_name("client-schema.json").read_text())
_IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_DATA = re.compile(_SCHEMA["data_attribute_pattern"])
_CLIENT_CHUNK = re.compile(r"client\.[A-Za-z0-9][A-Za-z0-9._-]*\.js\Z")
MAX_CLIENT_GUESTS = 8
CLIENT_IDLE_SECONDS = 15 * 60


class CordisClientError(ValueError):
    """A client extension has invalid state, output, or revoked authority."""


class CordisClientEventError(CordisClientError):
    """A rejected UI event leaves the guest alive so its current tree can be refreshed."""


def _json(value: Any, limit: int) -> str:
    try:
        result = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise CordisClientError("Client data must be bounded JSON.") from None
    if len(result.encode()) > limit:
        raise CordisClientError("Client data exceeds its size limit.")
    return result


def _identity(value: Any) -> bool:
    return isinstance(value, str) and _IDENTITY.fullmatch(value) is not None


def validate_snapshot(value: Any) -> dict[str, Any]:
    """Validate the shared, inert HTML subset independently of guest code."""
    _json(value, _SCHEMA["limits"]["snapshot_bytes"])
    if (not isinstance(value, dict) or not {"revision", "views", "status"} <= value.keys()
            or set(value) - {"revision", "views", "status", "warnings"}
            or value["status"] != "active" or type(value["revision"]) is not int
            or not 0 <= value["revision"] < 2**53):
        raise CordisClientError("Invalid client snapshot.")
    warnings = value.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 32 or any(not isinstance(item, str) or len(item) > 512 for item in warnings):
        raise CordisClientError("Invalid client compatibility warnings.")
    views = value["views"]
    if not isinstance(views, list) or len(views) > _SCHEMA["limits"]["views"]:
        raise CordisClientError("Invalid client views.")
    nodes: set[str] = set()
    events: set[str] = set()
    view_ids: set[str] = set()

    def scalar(item: Any) -> bool:
        return (item is None or type(item) is bool or isinstance(item, str) and len(item) <= 16_384
                or type(item) in {float, int} and abs(item) < 2**53 and math.isfinite(item))

    def visit(node: Any, depth: int) -> None:
        if (not isinstance(node, dict) or not {"id", "tag", "props", "events", "children"} <= node.keys()
                or set(node) - {"id", "tag", "text", "props", "events", "children"}
                or not _identity(node["id"]) or node["id"] in nodes
                or depth > _SCHEMA["limits"]["depth"]):
            raise CordisClientError("Invalid client tree node.")
        nodes.add(node["id"])
        if len(nodes) > _SCHEMA["limits"]["nodes"]:
            raise CordisClientError("Client tree has too many nodes.")
        if node["tag"] == "#text":
            if (not isinstance(node.get("text"), str) or node["props"] or node["events"] or node["children"]):
                raise CordisClientError("Invalid client text node.")
        elif node["tag"] not in _SCHEMA["tags"] or "text" in node:
            raise CordisClientError("Unsupported client element.")
        props = node["props"]
        if not isinstance(props, dict) or len(props) > 64:
            raise CordisClientError("Invalid client element properties.")
        for name, item in props.items():
            if not isinstance(name, str) or name not in _SCHEMA["props"] and _DATA.fullmatch(name) is None:
                raise CordisClientError("Unsupported client element property.")
            if name == "style":
                if not isinstance(item, dict) or set(item) - set(_SCHEMA["styles"]):
                    raise CordisClientError("Unsupported client style.")
                for style in item.values():
                    if not scalar(style) or isinstance(style, str) and re.search(r"[\\<>]|url\s*\(|expression\s*\(|@import", style, re.I):
                        raise CordisClientError("Unsafe client style.")
            elif not scalar(item):
                raise CordisClientError("Invalid client property value.")
        if node["tag"] == "input" and props.get("type", "text") not in _SCHEMA["input_types"]:
            raise CordisClientError("Unsupported client input type.")
        bindings = node["events"]
        if not isinstance(bindings, dict) or set(bindings) - set(_SCHEMA["events"]):
            raise CordisClientError("Invalid client events.")
        for event_id in bindings.values():
            if not _identity(event_id) or event_id in events:
                raise CordisClientError("Invalid client event identity.")
            events.add(event_id)
        if not isinstance(node["children"], list):
            raise CordisClientError("Invalid client children.")
        for child in node["children"]:
            visit(child, depth + 1)

    for view in views:
        if (not isinstance(view, dict) or not {"id", "slot", "mode", "tree"} <= view.keys()
                or set(view) - {"id", "slot", "mode", "key", "tree"}
                or not _identity(view["id"]) or view["id"] in view_ids
                or view["slot"] not in _SCHEMA["slots"] or not isinstance(view["mode"], str)
                or view["mode"] not in {"panel", "summary", "page"}
                or "key" in view and (not isinstance(view["key"], str) or len(view["key"]) > 256)
                or not isinstance(view["tree"], list)):
            raise CordisClientError("Invalid client view.")
        view_ids.add(view["id"])
        for node in view["tree"]:
            visit(node, 0)
    return value


def validate_event(value: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    _json(value, _SCHEMA["limits"]["event_bytes"])
    if (not isinstance(value, dict) or not {"revision", "event_id"} <= value.keys()
            or set(value) - {"revision", "event_id", "target_id", "values"}
            or type(value["revision"]) is not int or value["revision"] != snapshot["revision"]
            or not _identity(value["event_id"])):
        raise CordisClientEventError("This client event is invalid or stale; refresh the interface.")
    nodes, parents, views = {}, {}, {}
    pending = [(node, None, view["id"]) for view in snapshot["views"] for node in view["tree"]]
    while pending:
        node, parent, view_id = pending.pop()
        nodes[node["id"]] = node
        parents[node["id"]], views[node["id"]] = parent, view_id
        pending.extend((child, node["id"], view_id) for child in node["children"])

    def within(node_id: str, ancestor: str) -> bool:
        while node_id in nodes:
            if node_id == ancestor:
                return True
            node_id = parents[node_id]
        return False

    owner = next((node for node in nodes.values() if value["event_id"] in node["events"].values()), None)
    target = value.get("target_id", owner["id"] if owner else "")
    if owner is None or not isinstance(target, str) or not within(target, owner["id"]):
        raise CordisClientEventError("Client events belong to their displayed element.")
    scope = owner
    while parents[scope["id"]] is not None and scope["tag"] != "form":
        scope = nodes[parents[scope["id"]]]
    values = value.get("values", {})
    if not isinstance(values, dict) or len(values) > 128:
        raise CordisClientEventError("Invalid client form values.")
    for node_id, fields in values.items():
        node = nodes.get(node_id)
        if (node is None or views[node_id] != views[owner["id"]]
                or scope["tag"] == "form" and not within(node_id, scope["id"])
                or node["tag"] not in {"input", "textarea", "select"}
                or not isinstance(fields, dict) or set(fields) - {"value", "checked"}
                or "value" in fields and (not isinstance(fields["value"], str) or len(fields["value"]) > 16_384)
                or "checked" in fields and type(fields["checked"]) is not bool):
            raise CordisClientEventError("Client values must belong to displayed form controls.")
    return value


def _public_client_config(value: Any) -> Any:
    if isinstance(value, str):
        from libre_claw.core.memory import redact_secrets

        def query_values(query):
            result = []
            for name, item in parse_qsl(query, keep_blank_values=True):
                key = re.sub(r"[^a-z0-9]", "", name.lower())
                if key in {"key", "auth", "authorization", "code", "sig", "signature"} or key.endswith(("apikey", "token", "password", "secret", "credential")):
                    item = "[REDACTED]"
                result.append((name, redact_secrets(item)))
            return urlencode(result)

        def url(match):
            try:
                parts = urlsplit(match.group())
                fragment = query_values(parts.fragment) if "=" in parts.fragment else redact_secrets(parts.fragment)
                return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], redact_secrets(parts.path),
                                   query_values(parts.query), fragment))
            except ValueError:
                return "[REDACTED URL]"

        chunks, previous = [], 0
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+", value):
            chunks.extend((redact_secrets(value[previous:match.start()]), url(match)))
            previous = match.end()
        chunks.append(redact_secrets(value[previous:]))
        return "".join(chunks)
    if isinstance(value, list):
        return [_public_client_config(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if (normalized in {"key", "authorization", "cookie", "credentials"}
                or normalized.endswith(("apikey", "password", "secret", "token", "privatekey", "credential"))):
            continue
        result[key] = _public_client_config(item)
    return result


def client_spec(manager: CordisManager, plugin_id: str, workspace: Path) -> dict[str, Any]:
    if manager.config is not None and not manager.config.cordis.enabled:
        raise CordisClientError("Cordis plugins are disabled.")
    directory, key = _workspace(workspace)
    record = manager._record(plugin_id)
    grants = record.get("workspaces", {}).get(key)
    if not isinstance(grants, dict) or grants.get("allow_client") is not True:
        raise CordisClientError("Enable this plugin with explicit client-interface access first.")
    snapshot, manifest = manager._verify(plugin_id, record)
    client = validate_client_declaration(manifest.get("client"))
    if client["entry"] not in record["files"]:
        raise CordisClientError("The reviewed client entry is missing.")
    # Only files in the verified immutable snapshot, beside the client entry,
    # can satisfy the compiler's package-local require.async protocol.
    client_parent = Path(client["entry"]).parent
    chunks = {Path(name).name: str(snapshot / name) for name in record["files"]
              if Path(name).parent == client_parent and name != client["entry"]
              and _CLIENT_CHUNK.fullmatch(Path(name).name)}
    public = manager.details(plugin_id, directory)
    identity = {"digest": record["digest"], "grants": grants,
                "config": record.get("configs", {}).get(key, {}),
                "revision": record.get("revisions", {}).get(key, 0)}
    components = [plugin_id]
    pending = list(manifest.get("harness", {}).get("components", []))
    while pending:
        component = pending.pop()
        components.append(component["id"])
        pending.extend(component.get("children", []))
    return {"entry": str(snapshot / client["entry"]), "package_name": client["package_name"],
            "chunks": chunks,
            "root": snapshot, "workspace": directory, "config": _public_client_config(public["config"]),
            "component_ids": list(dict.fromkeys(components)),
            "identity": hashlib.sha256(_json(identity, 1024 * 1024).encode()).hexdigest()}


@dataclass
class _Guest:
    plugin_id: str
    workspace: Path
    identity: str
    worker: CordisWorker
    temporary: tempfile.TemporaryDirectory
    snapshot: dict[str, Any] | None = None
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CordisClientPool:
    def __init__(self, manager: CordisManager) -> None:
        self.manager = manager
        self._guests: dict[str, _Guest] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def open(self, plugin_id: str, workspace: Path, *, context: dict[str, str] | None = None) -> dict[str, Any]:
        await self.reconcile()
        spec = await asyncio.to_thread(client_spec, self.manager, plugin_id, workspace)
        if context is not None and (set(context) != {"run_id", "state"}
                                    or any(not isinstance(item, str) or len(item) > 256 for item in context.values())):
            raise CordisClientError("Invalid client task context.")
        temporary = tempfile.TemporaryDirectory(prefix="libre-claw-client-")
        guest = None
        token = secrets.token_urlsafe(24)
        try:
            prepared = await asyncio.to_thread(prepare_cordis_process, self.manager.node_executable,
                _RUNTIME, spec["root"], Path(temporary.name))
            initialize = {"plugin_id": plugin_id, "entry": spec["entry"], "package_name": spec["package_name"],
                          "chunks": spec["chunks"],
                          "state_dir": temporary.name, "locale": "en", "config": spec["config"],
                          "component_ids": spec["component_ids"], **({"context": context} if context else {})}
            worker = CordisWorker(prepared, initialize)
            guest = _Guest(plugin_id, spec["workspace"], spec["identity"], worker, temporary)
            async with self._lock:
                if self._closed or len(self._guests) >= MAX_CLIENT_GUESTS:
                    raise CordisClientError("Close an existing client interface before opening another.")
                self._guests[token] = guest
            guest.snapshot = validate_snapshot(await worker.start())
            await self._authorize(guest)
            return {"ui_session_id": token, "snapshot": guest.snapshot}
        except BaseException:
            if guest is not None:
                await self.close(plugin_id, token)
                await guest.worker.aclose()
            temporary.cleanup()
            raise

    async def _authorize(self, guest: _Guest) -> None:
        spec = await asyncio.to_thread(client_spec, self.manager, guest.plugin_id, guest.workspace)
        if spec["identity"] != guest.identity:
            raise CordisClientError("Client code, configuration or grants changed; reopen its interface.")

    async def request(self, plugin_id: str, token: str, event: Any = None) -> dict[str, Any]:
        guest = self._guests.get(token)
        if guest is None or guest.plugin_id != plugin_id:
            raise CordisClientError("This client interface is closed or does not belong to the plugin.")
        try:
            async with guest.lock:
                if time.monotonic() - guest.touched > CLIENT_IDLE_SECONDS:
                    raise CordisClientError("This client interface expired; reopen it.")
                await self._authorize(guest)
                if event is not None:
                    if guest.snapshot is None:
                        raise CordisClientError("The client interface is still starting.")
                    validate_event(event, guest.snapshot)
                result = await guest.worker.request("event" if event is not None else "snapshot", event or {}, timeout=10)
                snapshot = validate_snapshot(result)
                await self._authorize(guest)
                guest.snapshot, guest.touched = snapshot, time.monotonic()
                return {"ui_session_id": token, "snapshot": snapshot}
        except CordisClientEventError:
            raise
        except BaseException:
            await self.close(plugin_id, token)
            raise

    async def close(self, plugin_id: str, token: str) -> None:
        async with self._lock:
            guest = self._guests.get(token)
            if guest is None:
                return
            if guest.plugin_id != plugin_id:
                raise CordisClientError("This client interface belongs to another plugin.")
            del self._guests[token]
        async def cleanup() -> None:
            try:
                await guest.worker.aclose()
            finally:
                guest.temporary.cleanup()
        _, cancelled = await settle_finalization(asyncio.create_task(cleanup()))
        if cancelled:
            raise asyncio.CancelledError

    async def reconcile(self) -> None:
        for token, guest in list(self._guests.items()):
            try:
                await self._authorize(guest)
                if time.monotonic() - guest.touched > CLIENT_IDLE_SECONDS:
                    raise CordisClientError("Client interface expired.")
            except (ValueError, OSError, RuntimeError):
                await self.close(guest.plugin_id, token)

    async def aclose(self) -> None:
        self._closed = True
        for token, guest in list(self._guests.items()):
            await self.close(guest.plugin_id, token)
