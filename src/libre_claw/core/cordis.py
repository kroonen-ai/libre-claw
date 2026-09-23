# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Local, explicitly enabled Cordis plugins with per-workspace process grants."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
import copy
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from libre_claw.config import LibreClawConfig

from libre_claw.core.cordis_config import (
    CordisConfigError, bounded_json, effective_config, preserve_secrets,
    public_config, public_schema, validate_config, validate_schema,
)
from libre_claw.core.tools import BaseTool, PermissionLevel, ToolContext, ToolResult


MANIFEST_NAME = "libre-claw-plugin.json"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 256
MAX_PACKAGE_DIRECTORIES = 256
MAX_PACKAGE_ENTRIES = 1024
MAX_PACKAGE_DEPTH = 32
MAX_RPC_LINE = 1024 * 1024
MAX_RPC_BYTES = 4 * 1024 * 1024
CLEANUP_GRACE_SECONDS = 3.0
MAX_PERSISTENT_WORKERS = 16
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_EXCLUDED = {"node_modules", "__pycache__"}
_GRANT_KEYS = {"permissions", "grants", "allow_network", "allow_model", "read_paths", "write_paths"}


class CordisError(RuntimeError):
    """A plugin cannot be safely installed, enabled, or executed."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _parse(data: bytes | str) -> Any:
    try:
        return json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CordisError("Invalid plugin JSON.") from exc


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise CordisError(f"Invalid {label}; use lowercase letters, numbers, underscores, or hyphens.")
    return value


def _regular_bytes(path: Path, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CordisError("Plugin files must be regular files.")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise CordisError("Plugin exceeds the allowed size.")
        return data
    except OSError as exc:
        raise CordisError("Cannot safely read plugin files.") from exc


def _scan(directory: Path, *, exclude: bool) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise CordisError("Plugin source must be a local directory, not a symlink.")
    files: dict[str, bytes] = {}
    size = 0
    entries = 0
    directories = 1
    pending = [(directory, 0)]
    while pending:
        parent, depth = pending.pop()
        if parent.is_symlink():
            raise CordisError("Plugin symlinks are not allowed.")
        children: list[tuple[Path, bool]] = []
        try:
            # os.walk materializes each entire directory before yielding and
            # silently ignores unreadable subtrees. Bound enumeration itself.
            with os.scandir(parent) as listing:
                for child in listing:
                    entries += 1
                    if entries > MAX_PACKAGE_ENTRIES:
                        raise CordisError("Plugin contains too many directory entries.")
                    is_directory = child.is_dir(follow_symlinks=False)
                    if exclude and child.name.startswith("."):
                        continue
                    if exclude and child.name in _EXCLUDED and (is_directory or child.is_dir(follow_symlinks=True)):
                        continue
                    if exclude and not is_directory and Path(child.name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
                        continue
                    if child.is_symlink():
                        raise CordisError("Plugin symlinks are not allowed.")
                    if is_directory:
                        directories += 1
                        if directories > MAX_PACKAGE_DIRECTORIES:
                            raise CordisError("Plugin contains too many directories.")
                        if depth + 1 > MAX_PACKAGE_DEPTH:
                            raise CordisError("Plugin directory nesting exceeds the allowed depth.")
                    children.append((Path(child.path), is_directory))
        except OSError as exc:
            raise CordisError("Cannot safely read plugin directories.") from exc
        for path, is_directory in sorted(children, key=lambda item: item[0].name):
            if is_directory:
                pending.append((path, depth + 1))
                continue
            if len(files) >= MAX_PACKAGE_FILES:
                raise CordisError("Plugin contains too many files.")
            data = _regular_bytes(path, MAX_PACKAGE_BYTES - size)
            size += len(data)
            files[path.relative_to(directory).as_posix()] = data
    return files


def _checksums(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}


def _manifest(data: bytes) -> dict[str, Any]:
    value = _parse(data)
    if not isinstance(value, dict):
        raise CordisError("Plugin manifest must be an object.")
    if _GRANT_KEYS.intersection(value):
        raise CordisError("Plugin manifests cannot grant permissions; use explicit workspace enable grants.")
    harness = value.get("format") == "deepseek-harness"
    if harness:
        _validate_harness(value.get("harness"))
    plugin_id = _name(value.get("id"), "plugin id")
    if "__" in plugin_id:
        raise CordisError("Plugin ids cannot contain double underscores, which delimit tool namespaces.")
    for field in ("name", "version"):
        if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > 160:
            raise CordisError(f"Plugin manifest requires a valid {field}.")
    if not isinstance(value.get("description", ""), str) or len(value.get("description", "")) > 4000:
        raise CordisError("Plugin description must be a string of at most 4000 characters.")
    entry = value.get("entry")
    if not isinstance(entry, str) or not entry or "\\" in entry:
        raise CordisError("Plugin entry must be a relative JavaScript file.")
    entry_path = PurePosixPath(entry)
    if entry_path.is_absolute() or any(part in {".", ".."} or part.startswith(".") for part in entry_path.parts):
        raise CordisError("Plugin entry must remain inside its snapshot.")
    if entry_path.suffix not in {".js", ".mjs", ".cjs"}:
        raise CordisError("Plugin entry must be a JavaScript file.")
    try:
        if "config_schema" in value:
            validate_schema(value["config_schema"])
        validate_config(value.get("config", {}), value.get("config_schema"), partial=True)
    except CordisConfigError as exc:
        raise CordisError(str(exc)) from exc
    tools = value.get("tools")
    if not isinstance(tools, list) or not (0 if harness else 1) <= len(tools) <= 64:
        raise CordisError("Plugin manifest must declare between 1 and 64 tools.")
    seen = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"name", "description", "input_schema"}:
            raise CordisError("Each plugin tool must declare name, description, and input_schema.")
        name = _name(tool["name"], "tool name")
        if name in seen or len(f"cordis__{plugin_id}__{name}") > 64:
            raise CordisError("Plugin tool names must be unique and fit within 64 characters with their prefix.")
        seen.add(name)
        if not isinstance(tool["description"], str) or not tool["description"].strip() or len(tool["description"]) > 4000:
            raise CordisError("Plugin tools require descriptions of at most 4000 characters.")
        schema = tool["input_schema"]
        if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
            raise CordisError("Plugin tool input_schema must describe an object.")
        if len(_json(schema).encode()) > 32 * 1024:
            raise CordisError("Plugin tool schemas cannot exceed 32 KiB.")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) and item in schema.get("properties", {}) for item in required):
            raise CordisError("Plugin tool required fields must name declared properties.")
    # Leave room for the JSONL response envelope and delimiter; every accepted
    # manifest must fit the actual runtime catalog transport, not just disk.
    if len(_json({"tools": tools}).encode()) > MAX_RPC_LINE - 4096:
        raise CordisError("Combined plugin tool catalog exceeds the runtime frame budget.")
    value["config"] = value.get("config", {})
    return value


def _validate_harness(value: Any) -> None:
    """Validate resolver metadata before it can influence child module loading."""
    from libre_claw.core.cordis_harness import relative_path
    try:
        bounded_json(value, limit=256 * 1024, label="Harness metadata")
    except CordisConfigError as exc:
        raise CordisError(str(exc)) from exc
    if (not isinstance(value, dict) or not {"package", "components", "packages"} <= value.keys()
            or set(value) - {"package", "components", "packages", "adapter", "adapter_version", "adapter_fingerprint"}
            or not isinstance(value["package"], str)):
        raise CordisError("Invalid Harness metadata.")
    seen: set[str] = set()
    def components(rows: Any, depth: int = 0) -> None:
        if not isinstance(rows, list) or len(rows) > 128 or depth > 16:
            raise CordisError("Invalid Harness component tree.")
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"id", "enabled", "module", "config", "group", "children"}:
                raise CordisError("Invalid Harness component.")
            identity = row.get("id")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 160 or identity in seen or len(seen) >= 128 or type(row.get("enabled")) is not bool:
                raise CordisError("Invalid Harness component identity or enablement.")
            seen.add(identity)
            if row.get("group") is True:
                components(row.get("children"), depth + 1)
            else:
                module = row.get("module")
                if not isinstance(module, str) or not module or len(module) > 1024 or "\\" in module or "\0" in module:
                    raise CordisError("Invalid Harness module.")
                if module.startswith("."):
                    relative_path(module, "module")
                elif not re.fullmatch(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~/-]*", module):
                    raise CordisError("Invalid Harness package module.")
    components(value["components"])
    packages = value["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 32:
        raise CordisError("Invalid Harness package graph.")
    paths: set[str] = set()
    for package in packages:
        if (not isinstance(package, dict) or set(package) != {"name", "version", "path", "dependencies"}
                or not isinstance(package["name"], str) or not isinstance(package["version"], str)
                or not isinstance(package["dependencies"], dict)):
            raise CordisError("Invalid Harness dependency package.")
        path = package["path"]
        if path != ".":
            path = relative_path(path, "dependency")
        if path in paths:
            raise CordisError("Duplicate Harness dependency directory.")
        paths.add(path)
    if "." not in paths or any(not isinstance(target, str) or target not in paths
                               for package in packages for target in package["dependencies"].values()):
        raise CordisError("Harness dependency references must name reviewed packages.")


def _public_components(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    def public(row: dict[str, Any]) -> dict[str, Any]:
        return {key: [public(child) for child in value] if key == "children" else value
                for key, value in row.items() if key != "config"}
    return [public(row) for row in manifest.get("harness", {}).get("components", [])]


def _runtime_record(record: dict[str, Any], key: str) -> dict[str, Any]:
    """Only executable changes revoke a running workspace's capabilities."""
    return {"digest": record.get("digest"), "files": record.get("files"),
            "grants": record.get("workspaces", {}).get(key),
            "config": record.get("configs", {}).get(key, {}),
            "catalog": record.get("catalogs", {}).get(key, []),
            "revision": record.get("revisions", {}).get(key, 0)}


def _revise(record: dict[str, Any], key: str) -> None:
    record.setdefault("revisions", {})[key] = record.get("revisions", {}).get(key, 0) + 1


def _workspace(workspace: str | Path) -> tuple[Path, str]:
    path = Path(workspace).expanduser().resolve()
    if not path.is_dir():
        raise CordisError("Workspace must be an existing directory.")
    return path, hashlib.sha256(os.fsencode(path)).hexdigest()


def _grant_paths(paths) -> list[str]:
    if isinstance(paths, (str, bytes)):
        raise CordisError("Path grants must be a list of explicit paths.")
    try:
        return sorted({str(Path(path).expanduser().resolve()) for path in paths})
    except (TypeError, ValueError, OSError) as exc:
        raise CordisError("Invalid plugin path grant.") from exc


def _registry_mutation(method):
    @functools.wraps(method)
    def locked(manager, *args, **kwargs):
        # CLI processes can enable/disable concurrently. Serialize complete
        # read/modify/write transactions so one cannot restore revoked grants.
        import fcntl

        if manager.root.is_symlink():
            raise CordisError("Cordis registry root cannot be a symlink.")
        manager.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        manager.root.chmod(0o700)
        descriptor = os.open(manager.root / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                return method(manager, *args, **kwargs)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return locked


class CordisManager:
    """Manage only the user's local registry; projects cannot choose plugin roots."""

    def __init__(self, root: Path | str | None = None, node_executable: str = "node", tool_timeout: float = 30, *, config: LibreClawConfig | None = None, persistent: bool = False) -> None:
        root_path = Path(root if root is not None else Path.home() / ".libre-claw" / "cordis").expanduser().absolute()
        if root_path.is_symlink():
            raise CordisError("Cordis registry root cannot be a symlink.")
        # Node permissions use canonical paths, including macOS /private/var.
        # Use those same paths for snapshots and initialization RPC arguments.
        self.root = root_path.resolve()
        self.node_executable = node_executable
        self.tool_timeout = float(tool_timeout)
        self.config = config
        self.engine = None
        self.persistent = persistent
        self._workers: dict[tuple[str, str], dict[str, Any]] = {}
        self._worker_errors: dict[tuple[str, str], dict[str, Any]] = {}
        self._worker_lock = asyncio.Lock()
        self._closed = False
        if not math.isfinite(self.tool_timeout) or self.tool_timeout <= 0:
            raise CordisError("Cordis tool timeout must be positive and finite.")
        self.runtime_path = Path(__file__).resolve().parents[1] / "cordis_runtime" / "runtime.mjs"

    def _read_registry(self) -> dict[str, Any]:
        path = self.root / "registry.json"
        if not path.exists() and not path.is_symlink():
            return {"version": 1, "plugins": {}}
        value = _parse(_regular_bytes(path, MAX_PACKAGE_BYTES))
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("plugins"), dict):
            raise CordisError("Invalid Cordis registry.")
        return value

    def _write_registry(self, registry: dict[str, Any]) -> None:
        data = _json(registry) + "\n"
        if len(data.encode()) > MAX_PACKAGE_BYTES:
            raise CordisError("Cordis registry exceeds the allowed size.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.root / "registry.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _record(self, plugin_id: str, registry=None) -> dict[str, Any]:
        _name(plugin_id, "plugin id")
        record = (registry or self._read_registry())["plugins"].get(plugin_id)
        if not isinstance(record, dict):
            raise CordisError(f"Plugin {plugin_id} is not installed.")
        if not isinstance(record.get("digest"), str) or not _DIGEST.fullmatch(record["digest"]):
            raise CordisError("Invalid plugin snapshot reference.")
        if not isinstance(record.get("workspaces"), dict):
            raise CordisError("Invalid plugin workspace grants.")
        if not isinstance(record.get("configs", {}), dict):
            raise CordisError("Invalid plugin workspace configuration.")
        if not isinstance(record.get("catalogs", {}), dict):
            raise CordisError("Invalid plugin workspace tool catalog.")
        if not isinstance(record.get("config_schemas", {}), dict) or not isinstance(record.get("diagnostics", {}), dict):
            raise CordisError("Invalid plugin component metadata.")
        revisions = record.get("revisions", {})
        if not isinstance(revisions, dict) or any(type(value) is not int or value < 0 for value in revisions.values()):
            raise CordisError("Invalid plugin workspace revision.")
        return record

    def _snapshot(self, plugin_id: str, record: dict[str, Any]) -> Path:
        parent = self.root / "plugins" / plugin_id
        if (self.root / "plugins").is_symlink() or parent.is_symlink():
            raise CordisError("Plugin snapshot directories cannot be symlinks.")
        return parent / record["digest"]

    def _verify(self, plugin_id: str, record: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        snapshot = self._snapshot(plugin_id, record)
        files = _scan(snapshot, exclude=False)
        checksums = _checksums(files)
        if checksums != record.get("files") or hashlib.sha256(_json(checksums).encode()).hexdigest() != record["digest"]:
            raise CordisError("Plugin snapshot changed after installation; remove and reinstall it, then explicitly enable it again.")
        if MANIFEST_NAME not in files:
            raise CordisError("Plugin manifest is missing.")
        manifest = _manifest(files[MANIFEST_NAME])
        if manifest.get("harness", {}).get("adapter"):
            from libre_claw.core.cordis_harness import validate_native_adapter
            validate_native_adapter(files, manifest)
        if manifest["id"] != plugin_id or manifest["entry"] not in files:
            raise CordisError("Plugin identity or entry does not match its snapshot.")
        return snapshot, manifest

    def _source(self, source: str | Path) -> tuple[dict[str, bytes], dict[str, Any], dict[str, str], str]:
        source = Path(source).expanduser().absolute()
        files = _scan(source, exclude=True)
        if MANIFEST_NAME not in files:
            raise CordisError(f"Plugin requires {MANIFEST_NAME}.")
        manifest = _manifest(files[MANIFEST_NAME])
        if manifest.get("harness", {}).get("adapter"):
            from libre_claw.core.cordis_harness import validate_native_adapter
            validate_native_adapter(files, manifest)
        if manifest["entry"] not in files:
            raise CordisError("Plugin entry is missing or excluded from installation.")
        checksums = _checksums(files)
        digest = hashlib.sha256(_json(checksums).encode()).hexdigest()
        return files, manifest, checksums, digest

    def preview(self, source: str | Path) -> dict[str, Any]:
        """Review exact local package bytes without installing or executing code."""
        _, manifest, _, digest = self._source(source)
        return {
            "id": manifest["id"], "name": manifest["name"], "version": manifest["version"],
            "description": manifest.get("description", ""), "digest": digest,
            "tools": [tool["name"] for tool in manifest["tools"]],
            "tool_count": len(manifest["tools"]), "tool_definitions": manifest["tools"],
            "format": manifest.get("format", "libre-claw"),
            "components": _public_components(manifest),
            **({"adapter": manifest["harness"]["adapter"], "requires_model_access": True}
               if manifest.get("harness", {}).get("adapter") else {}),
            **({"requires_model_access": True} if self._is_bundled_orchestration(manifest["id"], digest) else {}),
        }

    def _is_bundled_orchestration(self, plugin_id: str, digest: str) -> bool:
        """Special host privileges belong only to the exact shipped source."""
        if plugin_id != "orchestration":
            return False
        source = Path(__file__).resolve().parents[1] / "cordis_runtime" / "examples" / "orchestration"
        _, _, _, included_digest = self._source(source)
        return digest == included_digest

    def orchestration_profile(self, workspace: str | Path) -> dict[str, Any]:
        """Load an explicitly enabled included profile without starting any work.

        The returned authorize callback is local-only. Persist only plugin_id,
        digest and config in a task checkpoint, never the callback itself.
        """
        from libre_claw.core.orchestration import validate_profile

        directory, key = _workspace(workspace)
        plugin_id = "orchestration"

        def checked() -> tuple[dict[str, Any], dict[str, Any]]:
            if self.config is not None and not self.config.cordis.enabled:
                raise CordisError("Cordis plugins are disabled in configuration.")
            record = self._record(plugin_id)
            _, manifest = self._verify(plugin_id, record)
            if not self._is_bundled_orchestration(plugin_id, record["digest"]):
                raise CordisError("Orchestration requires the unchanged included plugin. Reinstall builtin:orchestration.")
            grants = record["workspaces"].get(key)
            if not isinstance(grants, dict) or grants.get("allow_model") is not True:
                raise CordisError("Enable the orchestration plugin with model access for this project first.")
            return record, manifest

        record, manifest = checked()
        config = validate_profile(self._effective_config(record, manifest, key))
        baseline = copy.deepcopy(_runtime_record(record, key))

        def authorize() -> bool:
            if not directory.is_dir():
                raise CordisError("The orchestration workspace no longer exists.")
            current, _ = checked()
            if _runtime_record(current, key) != baseline:
                raise CordisError("Orchestration grants, configuration, or source changed. Start a new task.")
            return True

        return {"plugin_id": plugin_id, "digest": record["digest"], "config": config, "authorize": authorize}

    @_registry_mutation
    def install(self, source: str | Path, *, expected_digest: str | None = None) -> dict[str, Any]:
        files, manifest, checksums, digest = self._source(source)
        if expected_digest is not None and (not isinstance(expected_digest, str) or not _DIGEST.fullmatch(expected_digest) or digest != expected_digest):
            raise CordisError("Plugin source changed since its preview. Review the package again before installation.")
        plugin_id = manifest["id"]
        registry = self._read_registry()
        previous = registry["plugins"].get(plugin_id, {})
        record = {
            "digest": digest, "files": checksums,
            "name": manifest["name"], "version": manifest["version"],
            "workspaces": previous.get("workspaces", {}) if previous.get("digest") == digest else {},
            # New code must not silently inherit either grants or credentials.
            "configs": previous.get("configs", {}) if previous.get("digest") == digest else {},
            "catalogs": previous.get("catalogs", {}) if previous.get("digest") == digest else {},
            "config_schemas": previous.get("config_schemas", {}) if previous.get("digest") == digest else {},
            "diagnostics": previous.get("diagnostics", {}) if previous.get("digest") == digest else {},
            "revisions": previous.get("revisions", {}) if previous.get("digest") == digest else {},
        }
        destination = self._snapshot(plugin_id, record)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists() or destination.is_symlink():
            self._verify(plugin_id, record)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=destination.parent))
            try:
                for name, data in files.items():
                    target = temporary / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(data)
                    target.chmod(0o400)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        registry["plugins"][plugin_id] = record
        self._write_registry(registry)
        return self._describe(plugin_id, record, None)

    @_registry_mutation
    def enable(self, plugin_id: str, workspace: str | Path, allow_network: bool = False, read_paths=(), write_paths=(), *, allow_model: bool = False) -> dict[str, Any]:
        _, key = _workspace(workspace)
        if not isinstance(allow_network, bool):
            raise CordisError("Network permission must be explicitly true or false.")
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        _, manifest = self._verify(plugin_id, record)
        if manifest.get("format") == "deepseek-harness":
            raise CordisError("Harness plugins require asynchronous activation and tool discovery.")
        if not isinstance(allow_model, bool):
            raise CordisError("Model permission must be explicitly true or false.")
        self._effective_config(record, manifest, key)
        record["workspaces"][key] = {
            "allow_network": allow_network,
            "read_paths": _grant_paths(read_paths),
            "write_paths": _grant_paths(write_paths),
            **({"allow_model": True} if allow_model else {}),
        }
        _revise(record, key)
        self._write_registry(registry)
        return self._describe(plugin_id, record, key)

    def _tools_for(self, record: dict[str, Any], manifest: dict[str, Any], key: str | None) -> list[dict[str, Any]]:
        if manifest.get("format") != "deepseek-harness":
            return manifest["tools"]
        tools = record.get("catalogs", {}).get(key, [])
        return _manifest(_json({**manifest, "tools": tools}).encode())["tools"]

    def _host_service_spec(self, plugin_id: str, workspace_key: str, record: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        from libre_claw.core.cordis_ipc import validate_native_config
        if manifest.get("harness", {}).get("adapter") != "native-provider":
            raise CordisError("This plugin does not declare a reviewed host service adapter.")
        grants = record["workspaces"].get(workspace_key)
        if not isinstance(grants, dict) or grants.get("allow_model") is not True:
            raise CordisError("The native provider requires an explicit model access grant for this workspace.")
        component = manifest["harness"]["components"][0]
        config = self._effective_config(record, manifest, workspace_key)
        override = config.get("components", {}).get(component["id"], {})
        if not isinstance(override, dict) or type(override.get("enabled", component["enabled"])) is not bool:
            raise CordisError("Invalid native-provider component configuration.")
        state_dir = self.root / "ipc" / hashlib.sha256((workspace_key + ":" + plugin_id).encode()).hexdigest()[:12]
        try:
            normalized = validate_native_config(override.get("config", component["config"]), state_dir=state_dir,
                                                write_paths=tuple(Path(path) for path in grants.get("write_paths", [])))
        except (ValueError, OSError) as exc:
            raise CordisError(str(exc)) from exc
        return {"plugin_id": plugin_id, "adapter": "native-provider", "adapter_version": 1,
                "adapter_fingerprint": manifest["harness"]["adapter_fingerprint"], "digest": record["digest"],
                "revision": record.get("revisions", {}).get(workspace_key, 0),
                "state_dir": str(state_dir), "config": normalized,
                "enabled": override.get("enabled", component["enabled"])}

    def host_service_spec(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        """Return verified configuration for a first-party host service adapter."""
        _, key = _workspace(workspace)
        record = self._record(plugin_id)
        _, manifest = self._verify(plugin_id, record)
        return self._host_service_spec(plugin_id, key, record, manifest)

    async def enable_async(self, plugin_id: str, workspace: str | Path, allow_network: bool = False,
                           read_paths=(), write_paths=(), *, allow_model: bool = False) -> dict[str, Any]:
        """Activate explicit candidate grants, then publish its validated catalog."""
        _, key = _workspace(workspace)
        previous = self._record(plugin_id)
        _, manifest = self._verify(plugin_id, previous)
        if manifest.get("format") != "deepseek-harness":
            return self.enable(plugin_id, workspace, allow_network, read_paths, write_paths, allow_model=allow_model)
        if type(allow_network) is not bool or type(allow_model) is not bool:
            raise CordisError("Network and model permissions must be explicitly true or false.")
        candidate = copy.deepcopy(previous)
        candidate["workspaces"][key] = {"allow_network": allow_network, "allow_model": allow_model,
                                         "read_paths": _grant_paths(read_paths), "write_paths": _grant_paths(write_paths)}
        _revise(candidate, key)
        result = await self._invoke(plugin_id, workspace, _candidate=(previous, candidate))
        if result.get("activation_error"):
            self._save_diagnostics(plugin_id, previous, key, result)
            raise CordisError("Harness activation failed. Review the component settings and required services.")
        self._runtime_metadata(candidate, key, result)
        candidate.setdefault("catalogs", {})[key] = result["tool_definitions"]
        self._commit_candidate(plugin_id, previous, candidate)
        if self.persistent and not manifest.get("harness", {}).get("adapter"):
            await self.inspect(plugin_id, workspace)
        return self.details(plugin_id, workspace)

    def _runtime_metadata(self, record: dict[str, Any], key: str, result: dict[str, Any]) -> None:
        components = result.get("runtime_components", [])
        # Disabled components are not imported again. Retain their previously
        # observed secret annotations so disabling one cannot expose its values.
        properties = copy.deepcopy(record.get("config_schemas", {}).get(key, {}).get("properties", {})
                                   .get("components", {}).get("properties", {}))
        for component in components:
            schema = component.get("config_schema")
            if schema is None:
                continue
            try:
                validate_schema(schema)
            except CordisConfigError:
                continue  # The child reports unsupported schema details separately.
            properties[component["id"]] = {"type": "object", "properties": {
                "enabled": {"type": "boolean"}, "config": schema,
            }, "additionalProperties": True}
        if properties:
            schema = {"type": "object", "properties": {"components": {
                "type": "object", "properties": properties, "additionalProperties": True,
            }}, "additionalProperties": True}
            try:
                validate_schema(schema)
            except CordisConfigError:
                pass  # Never publish a schema larger than the supported form budget.
            else:
                record.setdefault("config_schemas", {})[key] = schema
        record.setdefault("diagnostics", {})[key] = components

    def _save_diagnostics(self, plugin_id: str, previous: dict[str, Any], key: str, result: dict[str, Any]) -> None:
        diagnostic = copy.deepcopy(previous)
        self._runtime_metadata(diagnostic, key, result)
        self._commit_candidate(plugin_id, previous, diagnostic)

    def _config_schema(self, record: dict[str, Any], manifest: dict[str, Any], key: str) -> dict[str, Any] | None:
        explicit = manifest.get("config_schema")
        if explicit is not None:
            return explicit
        schema = copy.deepcopy(record.get("config_schemas", {}).get(key))
        if schema is None:
            return None
        # The loader accepts scoped component IDs and the legacy unscoped
        # alias. Redact either spelling without displaying duplicate empty forms.
        actual = {**manifest.get("config", {}).get("components", {}),
                  **record.get("configs", {}).get(key, {}).get("components", {})}
        properties = schema.get("properties", {}).get("components", {}).get("properties", {})
        def aliases(rows: list[dict[str, Any]], parent: str = "") -> None:
            for row in rows:
                path = parent + "/" + row["id"] if parent else row["id"]
                if path in properties and row["id"] != path and row["id"] in actual:
                    properties[row["id"]] = properties[path]
                    if path not in actual:
                        del properties[path]
                if row.get("group"):
                    aliases(row["children"], path)
        aliases(manifest.get("harness", {}).get("components", []))
        return schema

    @_registry_mutation
    def _commit_candidate(self, plugin_id: str, previous: dict[str, Any], candidate: dict[str, Any]) -> None:
        registry = self._read_registry()
        if self._record(plugin_id, registry) != previous:
            raise CordisError("Plugin settings changed during activation; review and retry.")
        registry["plugins"][plugin_id] = candidate
        self._write_registry(registry)

    async def configure_async(self, plugin_id: str, workspace: str | Path, config: dict[str, Any]) -> dict[str, Any]:
        """Validate active Harness configuration and rediscover tools atomically."""
        _, key = _workspace(workspace)
        previous = self._record(plugin_id)
        _, manifest = self._verify(plugin_id, previous)
        if manifest.get("format") != "deepseek-harness" or key not in previous["workspaces"]:
            return self.configure(plugin_id, workspace, config)
        candidate = copy.deepcopy(previous)
        try:
            validate_config(config, None)
            overrides = preserve_secrets(config, candidate.get("configs", {}).get(key, {}), self._config_schema(previous, manifest, key))
            bounded_json(overrides)
        except CordisConfigError as exc:
            raise CordisError(str(exc)) from exc
        candidate.setdefault("configs", {})[key] = overrides
        _revise(candidate, key)
        result = await self._invoke(plugin_id, workspace, _candidate=(previous, candidate))
        if result.get("activation_error"):
            self._save_diagnostics(plugin_id, previous, key, result)
            raise CordisError("Harness configuration failed. Review the component settings and required services.")
        self._runtime_metadata(candidate, key, result)
        candidate.setdefault("catalogs", {})[key] = result["tool_definitions"]
        self._commit_candidate(plugin_id, previous, candidate)
        if self.persistent and not manifest.get("harness", {}).get("adapter"):
            await self.inspect(plugin_id, workspace)
        return self.details(plugin_id, workspace)

    def _effective_config(self, record: dict[str, Any], manifest: dict[str, Any], key: str, *, partial: bool = False) -> dict[str, Any]:
        try:
            overrides = record.get("configs", {}).get(key, {})
            # Validate the envelope first; secret null tombstones are resolved
            # before applying the declared schema to the resulting config.
            validate_config(overrides, None)
            result = effective_config(manifest["config"], overrides, self._config_schema(record, manifest, key))
            validate_config(result, manifest.get("config_schema"), partial=partial)
            return result
        except CordisConfigError as exc:
            raise CordisError(str(exc)) from exc

    def details(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        """Return editable metadata without executing code or exposing secrets."""
        _, key = _workspace(workspace)
        record = self._record(plugin_id)
        _, manifest = self._verify(plugin_id, record)
        schema = self._config_schema(record, manifest, key)
        config, configured = public_config(self._effective_config(record, manifest, key, partial=True), schema)
        return {
            **self._describe(plugin_id, record, key), "digest": record["digest"],
            "config_schema": public_schema(schema),
            "config": config, "configured_secrets": configured,
            "tool_definitions": self._tools_for(record, manifest, key),
            "runtime_components": record.get("diagnostics", {}).get(key, []),
        }

    @_registry_mutation
    def configure(self, plugin_id: str, workspace: str | Path, config: dict[str, Any]) -> dict[str, Any]:
        """Save workspace overrides; omission retains secrets, null clears them.

        Ordinary fields replace previous overrides and inherit manifest defaults.
        Disabling a plugin does not discard config. Installing changed code does.
        """
        _, key = _workspace(workspace)
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        _, manifest = self._verify(plugin_id, record)
        if manifest.get("format") == "deepseek-harness" and key in record["workspaces"]:
            raise CordisError("Active Harness plugins require asynchronous configuration and tool discovery.")
        try:
            validate_config(config, None)
            previous = record.get("configs", {}).get(key, {})
            validate_config(previous, None)
            overrides = preserve_secrets(config, previous, self._config_schema(record, manifest, key))
            bounded_json(overrides)
        except CordisConfigError as exc:
            raise CordisError(str(exc)) from exc
        record.setdefault("configs", {})[key] = overrides
        _revise(record, key)
        self._effective_config(record, manifest, key)
        self._write_registry(registry)
        return self.details(plugin_id, workspace)

    @_registry_mutation
    def disable(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        _, key = _workspace(workspace)
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        record["workspaces"].pop(key, None)
        record.get("catalogs", {}).pop(key, None)
        _revise(record, key)
        self._write_registry(registry)
        return self._describe(plugin_id, record, key)

    @_registry_mutation
    def _unregister(self, plugin_id: str) -> None:
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        self._snapshot(plugin_id, record)
        del registry["plugins"][plugin_id]
        self._write_registry(registry)

    @_registry_mutation
    def _cleanup_removed(self, plugin_id: str) -> bool:
        _name(plugin_id, "plugin id")
        if plugin_id in self._read_registry()["plugins"]:
            return False  # A concurrent reinstall owns its new code and state.
        directory = self.root / "plugins" / plugin_id
        if (self.root / "plugins").is_symlink() or directory.is_symlink():
            raise CordisError("Plugin snapshot directories cannot be symlinks.")
        if directory.exists():
            shutil.rmtree(directory)
        state = self.root / "state"
        if state.is_dir() and not state.is_symlink():
            for workspace in state.iterdir():
                if workspace.is_dir() and not workspace.is_symlink() and _DIGEST.fullmatch(workspace.name):
                    plugin_state = workspace / plugin_id
                    if plugin_state.is_symlink():
                        plugin_state.unlink()
                    elif plugin_state.is_dir():
                        shutil.rmtree(plugin_state)
        return True

    def remove(self, plugin_id: str) -> dict[str, Any]:
        """Remove an unowned plugin; app owners use remove_async to join workers."""
        self._unregister(plugin_id)
        self._cleanup_removed(plugin_id)
        return {"id": plugin_id, "removed": True}

    async def remove_async(self, plugin_id: str) -> dict[str, Any]:
        """Revoke access, join owned disposers, then delete remaining private data."""
        self._unregister(plugin_id)
        async def finish() -> dict[str, Any]:
            async with self._worker_lock:
                entries = [self._workers.pop(key) for key in list(self._workers) if key[1] == plugin_id]
                for key in list(self._worker_errors):
                    if key[1] == plugin_id:
                        self._worker_errors.pop(key, None)
                await asyncio.gather(*(entry["worker"].aclose() for entry in entries), return_exceptions=True)
            await asyncio.to_thread(self._cleanup_removed, plugin_id)
            return {"id": plugin_id, "removed": True}
        cleanup = asyncio.create_task(finish())
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _describe(self, plugin_id: str, record: dict[str, Any], workspace_key: str | None) -> dict[str, Any]:
        error = None
        description = ""
        tools: list[str] = []
        manifest: dict[str, Any] = {}
        try:
            _, manifest = self._verify(plugin_id, record)
            description = manifest.get("description", "")
            tools = [tool["name"] for tool in self._tools_for(record, manifest, workspace_key)]
        except (CordisError, OSError) as exc:
            error = str(exc)
        grants = record.get("workspaces", {}).get(workspace_key) if workspace_key else None
        result = {
            "id": plugin_id, "name": record.get("name", plugin_id), "version": record.get("version", ""),
            "description": description,
            "enabled": grants is not None,
            "integrity": "changed" if error else "valid",
            "tools": tools, "tool_count": len(tools),
            "grants": grants or {"allow_network": False, "read_paths": [], "write_paths": []},
            "format": manifest.get("format", "libre-claw"),
            "components": _public_components(manifest),
            "runtime_lifetime": "per-call",
        }
        if manifest and self._is_bundled_orchestration(plugin_id, record["digest"]):
            result["requires_model_access"] = True
        if manifest.get("harness", {}).get("adapter"):
            result.update(adapter=manifest["harness"]["adapter"], runtime_lifetime="host-service",
                          state="HOST_SERVICE" if grants is not None else "DISABLED")
        elif self.persistent:
            result["runtime_lifetime"] = "persistent"
            worker_key = (workspace_key, plugin_id)
            entry = self._workers.get(worker_key)
            running = entry is not None and entry["worker"].running
            if running:
                result["state"] = "ACTIVE" if _runtime_record(entry["baseline"], workspace_key) == _runtime_record(record, workspace_key) and grants is not None else "RESTARTING" if grants is not None else "STOPPING"
            else:
                result["state"] = "STOPPED" if grants is not None else "DISABLED"
            if entry and entry["worker"].running:
                result["process_id"] = entry["worker"].pid
            failure = self._worker_errors.get(worker_key)
            if failure and failure["baseline"] == record:
                result["runtime_error"] = failure["message"]
        if error:
            result["error"] = error
        return result

    def list_plugins(self, workspace: str | Path) -> list[dict[str, Any]]:
        _, key = _workspace(workspace)
        registry = self._read_registry()
        results = []
        for plugin_id in sorted(registry["plugins"]):
            try:
                results.append(self._describe(plugin_id, self._record(plugin_id, registry), key))
            except CordisError as exc:
                results.append({"id": plugin_id, "enabled": False, "integrity": "changed", "error": str(exc), "tools": [], "tool_count": 0})
        return results

    def create_tools(self, context: ToolContext) -> list[BaseTool]:
        tools: list[BaseTool] = []
        try:
            plugins = self.list_plugins(context.working_directory)
        except (CordisError, OSError):
            return tools
        for plugin in plugins:
            if not plugin["enabled"] or plugin["integrity"] != "valid":
                continue
            try:
                record = self._record(plugin["id"])
                _, manifest = self._verify(plugin["id"], record)
                _, key = _workspace(context.working_directory)
                tools.extend(CordisTool(context, self, plugin["id"], item, record["digest"]) for item in self._tools_for(record, manifest, key))
            except (CordisError, OSError):
                continue
        return tools

    async def inspect(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        return await self._invoke(plugin_id, workspace)

    async def _invoke(self, plugin_id: str, workspace: str | Path, tool_name: str | None = None, arguments: dict[str, Any] | None = None, *, expected_digest: str | None = None, context: ToolContext | None = None, _candidate: tuple[dict[str, Any], dict[str, Any]] | None = None) -> dict[str, Any]:
        from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
        from libre_claw.core.cordis_host import CordisHost

        if self.config is not None and not self.config.cordis.enabled:
            raise CordisError("Cordis plugins are disabled in configuration.")
        started = time.monotonic()
        _, key = _workspace(workspace)
        record = self._record(plugin_id)
        baseline = copy.deepcopy(record)
        if _candidate is not None:
            if baseline != _candidate[0]:
                raise CordisError("Plugin settings changed before activation; review and retry.")
            record = _candidate[1]
        if expected_digest is not None and record["digest"] != expected_digest:
            raise CordisError("Plugin changed since this task loaded its tools. Start a new task to use the updated plugin.")
        grants = record["workspaces"].get(key)
        if not isinstance(grants, dict):
            raise CordisError("Plugin is not enabled for this workspace.")
        if not isinstance(grants.get("allow_network"), bool) or type(grants.get("allow_model", False)) is not bool or not isinstance(grants.get("read_paths"), list) or not isinstance(grants.get("write_paths"), list):
            raise CordisError("Invalid plugin grants; enable the plugin again.")
        snapshot, manifest = self._verify(plugin_id, record)
        config = self._effective_config(record, manifest, key)
        if manifest.get("harness", {}).get("adapter"):
            spec = self._host_service_spec(plugin_id, key, record, manifest)
            if tool_name is not None:
                raise CordisError("This host service adapter does not expose agent tools.")
            return {**self._describe(plugin_id, record, key), "state": "HOST_SERVICE",
                    "tool_definitions": [], "host_service": spec,
                    "runtime_components": [{"id": manifest["harness"]["components"][0]["id"],
                                             "state": "HOST_SERVICE" if spec["enabled"] else "DISABLED"}]}
        expected_tools = self._tools_for(record, manifest, key)
        if tool_name is not None and tool_name not in {tool["name"] for tool in expected_tools}:
            raise CordisError("Plugin tool is not declared in its manifest.")
        def authorize() -> dict[str, Any]:
            if self.config is not None and not self.config.cordis.enabled:
                raise CordisError("Cordis plugins are disabled in configuration.")
            latest = self._record(plugin_id)
            if _runtime_record(latest, key) != _runtime_record(baseline, key):
                raise CordisError("Plugin grants or settings changed during execution.")
            return grants
        orchestration_authorize = None
        if (tool_name is not None and context is not None and grants.get("allow_model") is True
                and self._is_bundled_orchestration(plugin_id, record["digest"])):
            orchestration_authorize = self.orchestration_profile(workspace)["authorize"]
        host = CordisHost(plugin_id, authorize, config=self.config, context=context, engine=self.engine,
                          orchestration_authorize=orchestration_authorize)
        host_parameters = await host.initialize() if manifest.get("format") == "deepseek-harness" else {}
        state_dir = self.root / "state" / key / plugin_id
        for path in (self.root / "state", state_dir.parent, state_dir):
            if path.is_symlink():
                raise CordisError("Plugin state directories cannot be symlinks.")
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            prepared = await asyncio.wait_for(asyncio.to_thread(
                prepare_cordis_process, self.node_executable, self.runtime_path, snapshot, state_dir,
                allow_network=grants["allow_network"], read_paths=tuple(Path(path) for path in grants["read_paths"]), write_paths=tuple(Path(path) for path in grants["write_paths"]),
            ), timeout=self.tool_timeout)
        except asyncio.TimeoutError as exc:
            raise CordisError("Plugin security preparation timed out; no plugin was started.") from exc
        except CordisSecurityError as exc:
            raise CordisError(str(exc)) from exc
        if self.persistent and _candidate is None:
            return await self._persistent_invoke(plugin_id, workspace, key, baseline, manifest, expected_tools,
                                                 prepared, snapshot, state_dir, config, host_parameters, host,
                                                 tool_name, arguments, started)
        process = None
        stderr_task = None
        request_id = 0
        response_bytes = 0
        sent_bytes = 0
        host_ids: set[str | int] = set()

        async def send(payload: dict[str, Any]) -> None:
            nonlocal sent_bytes
            data = (_json(payload) + "\n").encode()
            sent_bytes += len(data)
            if len(data) > MAX_RPC_LINE or sent_bytes > MAX_RPC_BYTES:
                raise CordisError("Plugin request exceeds the allowed size.")
            process.stdin.write(data)
            await process.stdin.drain()

        async def host_request(reply: dict[str, Any]) -> None:
            identifier = reply.get("host_call_id")
            if (type(identifier) not in {str, int} or isinstance(identifier, str) and not 1 <= len(identifier) <= 128
                    or identifier in host_ids or len(host_ids) >= 256
                    or set(reply) - {"host_call_id", "method", "params", "stream"}
                    or not isinstance(reply.get("method"), str) or type(reply.get("stream", False)) is not bool):
                raise CordisError("Plugin returned an invalid host request.")
            host_ids.add(identifier)
            authorize()
            try:
                if reply.get("stream"):
                    stream = host.stream(reply["method"], reply.get("params"))
                    try:
                        async for chunk in stream:
                            await send({"host_call_id": identifier, "chunk": chunk})
                    finally:
                        await stream.aclose()
                    result = None
                else:
                    result = await host.dispatch(reply["method"], reply.get("params"))
                authorize()
            except Exception:
                # Arbitrary provider/handler exceptions and request payloads can
                # contain secrets. The child receives only a stable diagnosis.
                await send({"host_call_id": identifier, "error": {"message": "Plugin host operation was denied or could not complete."}})
                return
            await send({"host_call_id": identifier, "result": result})

        async def rpc(method: str, params: dict[str, Any] | None = None):
            nonlocal request_id, response_bytes
            request_id += 1
            try:
                await send({"id": request_id, "method": method, "params": params or {}})
                while True:
                    line = await process.stdout.readline()
                    response_bytes += len(line)
                    if not line or len(line) > MAX_RPC_LINE or response_bytes > MAX_RPC_BYTES:
                        raise CordisError("Plugin runtime closed or exceeded the output limit.")
                    reply = _parse(line)
                    if isinstance(reply, dict) and "host_call_id" in reply:
                        await host_request(reply)
                        continue
                    break
            except (BrokenPipeError, ConnectionError, ValueError) as exc:
                raise CordisError("Plugin runtime closed or exceeded the output limit.") from exc
            if not isinstance(reply, dict) or reply.get("id") != request_id:
                raise CordisError("Plugin runtime returned an invalid response.")
            if "error" in reply:
                error = reply["error"]
                message = error.get("message") if isinstance(error, dict) else None
                raise CordisError(str(message or "Plugin runtime failed.")[:4000])
            if "result" not in reply:
                raise CordisError("Plugin runtime omitted its result.")
            return reply["result"]

        async def drain_stderr():
            while await process.stderr.read(4096):
                pass  # Never retain or log plugin stderr, which may contain config secrets.

        async def run():
            nonlocal process, stderr_task
            process = await asyncio.create_subprocess_exec(
                *prepared.command, env=prepared.env, cwd=prepared.cwd,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=MAX_RPC_LINE,
            )
            stderr_task = asyncio.create_task(drain_stderr())
            authorize()
            await rpc("initialize", {"entry": str(snapshot / manifest["entry"]), "plugin_id": plugin_id, "config": config, "state_dir": str(state_dir),
                                     **({"harness": manifest["harness"]} if manifest.get("format") == "deepseek-harness" else {}), **host_parameters})
            catalog = await rpc("tools/list")
            actual = catalog.get("tools") if isinstance(catalog, dict) else None
            if not isinstance(actual, list) or not all(isinstance(tool, dict) and isinstance(tool.get("name"), str) for tool in actual):
                raise CordisError("Plugin runtime returned an invalid tool catalog.")
            _manifest(_json({**manifest, "tools": actual}).encode())
            if _candidate is None and _json(sorted(actual, key=lambda tool: tool["name"])) != _json(sorted(expected_tools, key=lambda tool: tool["name"])):
                raise CordisError("Plugin runtime tools differ from the installed manifest; no tool was executed.")
            authorize()
            if tool_name is not None:
                result = await rpc("tools/call", {"name": tool_name, "arguments": arguments or {}, "context": host.execution})
                authorize()
                if not isinstance(result, dict) or not isinstance(result.get("content"), str) or not isinstance(result.get("error"), bool):
                    raise CordisError("Plugin runtime returned an invalid tool result.")
                for field in ("meta", "additional_contexts", "concludes_turn"):
                    if field in result:
                        try:
                            bounded_json(result[field], label="Plugin result metadata")
                        except CordisConfigError as exc:
                            raise CordisError(str(exc)) from exc
                if "additional_contexts" in result and not isinstance(result["additional_contexts"], list) or "concludes_turn" in result and not isinstance(result["concludes_turn"], bool):
                    raise CordisError("Plugin runtime returned invalid result control fields.")
                return result
            result = await rpc("inspect")
            if not isinstance(result, dict) or result.get("plugin_id") != plugin_id or result.get("state") != "ACTIVE":
                raise CordisError("Plugin runtime did not activate the requested plugin.")
            components = result.get("harness", {}).get("components", [])
            failed = any(row.get("state") not in {"ACTIVE", "DISABLED"} or row.get("missing_services") for row in components)
            if failed and _candidate is None:
                raise CordisError("Harness component requires a host service that is unavailable or not granted.")
            authorize()
            return {**self._describe(plugin_id, record, key), "state": result["state"], "runtime_version": result.get("runtime_version"),
                    "isolation": prepared.isolation, "tool_definitions": actual, "tools": [item["name"] for item in actual],
                    "tool_count": len(actual), "runtime_components": components, **({"activation_error": True} if failed else {})}

        async def dispose(normal_completion: bool) -> bool:
            if process is None:
                return True
            cleaned = False
            if process.returncode is None:
                async def shutdown():
                    unmounted = await rpc("unmount")
                    stopped = await rpc("shutdown")
                    return unmounted == {"unmounted": True} and stopped == {"shutdown": True}

                with contextlib.suppress(Exception):
                    cleaned = await asyncio.wait_for(
                        shutdown(), timeout=CLEANUP_GRACE_SECONDS if normal_completion else 0.6,
                    )
                try:
                    if cleaned:
                        await asyncio.wait_for(process.wait(), timeout=0.3)
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=0.3)
                except asyncio.TimeoutError:
                    cleaned = False
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            if stderr_task is not None:
                if not stderr_task.done():
                    stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            return cleaned

        normal_completion = False
        try:
            async with asyncio.timeout(max(0.0, self.tool_timeout - (time.monotonic() - started))) as timeout:
                host.timeout = timeout
                result = await run()
            normal_completion = True
            return result
        except asyncio.TimeoutError as exc:
            raise CordisError("Plugin execution timed out.") from exc
        except OSError as exc:
            raise CordisError("Unable to start the isolated Cordis runtime.") from exc
        finally:
            cleanup = asyncio.create_task(dispose(normal_completion))
            try:
                cleaned = await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            if normal_completion and not cleaned:
                raise CordisError("Plugin returned a result, but managed cleanup did not finish; the runtime was stopped and plugin state may be incomplete.")

    async def _persistent_invoke(self, plugin_id, workspace, key, baseline, manifest, expected_tools,
                                 prepared, snapshot, state_dir, config, host_parameters, host,
                                 tool_name, arguments, started) -> dict[str, Any]:
        from libre_claw.core.cordis_host import CordisHost
        from libre_claw.core.cordis_worker import CordisWorker, CordisWorkerError

        worker_key = (key, plugin_id)
        async with self._worker_lock:
            if self._closed:
                raise CordisError("The Cordis manager is closed.")
            entry = self._workers.get(worker_key)
            if entry and (_runtime_record(entry["baseline"], key) != _runtime_record(baseline, key) or entry["host_config"] != self.config or not entry["worker"].running):
                await entry["worker"].aclose()
                self._workers.pop(worker_key, None)
                entry = None
            if entry is None:
                if len(self._workers) >= MAX_PERSISTENT_WORKERS:
                    self._worker_errors[worker_key] = {"baseline": baseline, "host_config": self.config,
                                                       "workspace": str(workspace), "capacity": True,
                                                       "message": "The application has reached its 16-plugin worker limit."}
                    raise CordisError("The application has reached its 16-plugin worker limit. Disable a plugin before starting another.")
                def authorize() -> dict[str, Any]:
                    if self.config is not None and not self.config.cordis.enabled:
                        raise CordisError("Cordis plugins are disabled in configuration.")
                    if _runtime_record(self._record(plugin_id), key) != _runtime_record(baseline, key):
                        raise CordisError("Plugin grants or settings changed during execution.")
                    return baseline["workspaces"][key]

                def background(method, params, *, stream, timeout):
                    if method not in {"llm.listModels", "llm.resolveModelInfo", "llm.stream"}:
                        raise PermissionError("Background plugins cannot access a task or ask questions.")
                    broker = CordisHost(plugin_id, authorize, config=self.config, engine=self.engine)
                    broker.timeout = timeout
                    return broker.stream(method, params) if stream else broker.dispatch(method, params)

                initialize = {"entry": str(snapshot / manifest["entry"]), "plugin_id": plugin_id,
                              "config": config, "state_dir": str(state_dir), **host_parameters,
                              **({"harness": manifest["harness"]} if manifest.get("format") == "deepseek-harness" else {})}
                worker = CordisWorker(prepared, initialize, background_host_handler=background)
                try:
                    authorize()
                    await asyncio.wait_for(worker.start(), timeout=max(0.01, self.tool_timeout - (time.monotonic() - started)))
                    authorize()
                except asyncio.CancelledError:
                    await worker.aclose()
                    raise
                except Exception as exc:
                    await worker.aclose()
                    self._worker_errors[worker_key] = {"baseline": baseline, "host_config": self.config,
                                                       "workspace": str(workspace), "message": "The plugin worker could not start."}
                    raise CordisError("The plugin worker could not start.") from exc
                entry = {"worker": worker, "baseline": baseline, "host_config": self.config, "workspace": str(workspace)}
                self._workers[worker_key] = entry
                self._worker_errors.pop(worker_key, None)
            worker = entry["worker"]

        def handler(method, params, *, stream, timeout):
            host.timeout = timeout
            return host.stream(method, params) if stream else host.dispatch(method, params)

        async def request(method, params=None):
            host.authorize()
            result = await worker.request(method, params, host_handler=handler,
                                          timeout=max(0.01, self.tool_timeout - (time.monotonic() - started)))
            host.authorize()
            return result

        try:
            catalog = await request("tools/list")
            actual = catalog.get("tools") if isinstance(catalog, dict) else None
            _manifest(_json({**manifest, "tools": actual}).encode())
            if _json(sorted(actual, key=lambda tool: tool["name"])) != _json(sorted(expected_tools, key=lambda tool: tool["name"])):
                await worker.aclose()
                raise CordisError("Plugin runtime tools differ from the approved catalog; no tool was executed.")
            inspection = await request("inspect")
            if not isinstance(inspection, dict) or inspection.get("state") != "ACTIVE" or inspection.get("plugin_id") != plugin_id:
                raise CordisError("The persistent plugin did not activate.")
            components = inspection.get("harness", {}).get("components", [])
            if any(row.get("state") not in {"ACTIVE", "DISABLED"} or row.get("missing_services") for row in components):
                raise CordisError("Harness component requires a host service that is unavailable or not granted.")
            if tool_name is None:
                return {**self._describe(plugin_id, baseline, key), "state": "ACTIVE", "runtime_lifetime": "persistent",
                        "runtime_version": inspection.get("runtime_version"), "isolation": worker.isolation,
                        "process_id": worker.pid, "tool_definitions": actual, "runtime_components": components}
            result = await request("tools/call", {"name": tool_name, "arguments": arguments or {}, "context": host.execution})
            if not isinstance(result, dict) or not isinstance(result.get("content"), str) or type(result.get("error")) is not bool:
                raise CordisError("Plugin runtime returned an invalid tool result.")
            for field in ("meta", "additional_contexts", "concludes_turn"):
                if field in result:
                    bounded_json(result[field], label="Plugin result metadata")
            if (("additional_contexts" in result and not isinstance(result["additional_contexts"], list))
                    or ("concludes_turn" in result and type(result["concludes_turn"]) is not bool)):
                raise CordisError("Plugin runtime returned invalid result control fields.")
            return result
        except CordisWorkerError as exc:
            raise CordisError(str(exc)) from exc
        except CordisConfigError as exc:
            raise CordisError(str(exc)) from exc
        except (CordisError, ValueError):
            await worker.aclose()
            raise

    async def reconcile_workers(self, workspace: str | Path | None = None) -> None:
        """Revoke changed workers and restore enabled services under saved grants.

        Without a workspace, only already warm workspaces are considered. Failed
        starts are retried after a settings revision or an explicit inspection.
        """
        if not self.persistent or self._closed:
            return
        if self.config is not None and not self.config.cordis.enabled:
            async with self._worker_lock:
                identifiers = {key[1] for key in self._workers}
                entries = list(self._workers.values())
                self._workers.clear()
                self._worker_errors.clear()
                await asyncio.gather(*(entry["worker"].aclose() for entry in entries), return_exceptions=True)
                for plugin_id in identifiers:
                    await asyncio.to_thread(self._cleanup_removed, plugin_id)
            return
        warm = {entry["workspace"] for entry in [*self._workers.values(), *self._worker_errors.values()]}
        if workspace is not None:
            warm.add(str(_workspace(workspace)[0]))
        async with self._worker_lock:
            for worker_key, entry in list(self._workers.items()):
                key, plugin_id = worker_key
                try:
                    current = self._record(plugin_id)
                    self._verify(plugin_id, current)
                    keep = (_runtime_record(current, key) == _runtime_record(entry["baseline"], key) and entry["host_config"] == self.config
                            and key in current["workspaces"] and entry["worker"].running)
                except (CordisError, OSError):
                    keep = False
                if not keep:
                    await entry["worker"].aclose()
                    self._workers.pop(worker_key, None)
                    await asyncio.to_thread(self._cleanup_removed, plugin_id)
        for directory in warm:
            try:
                _, key = _workspace(directory)
                plugins = self.list_plugins(directory)
            except (CordisError, OSError):
                continue
            for plugin in plugins:
                if not plugin["enabled"] or plugin["integrity"] != "valid" or plugin.get("adapter") or plugin.get("format") != "deepseek-harness":
                    continue
                worker_key = (key, plugin["id"])
                if worker_key in self._workers:
                    continue
                record = self._record(plugin["id"])
                failure = self._worker_errors.get(worker_key)
                if (failure and _runtime_record(failure["baseline"], key) == _runtime_record(record, key) and failure["host_config"] == self.config
                        and not (failure.get("capacity") and len(self._workers) < MAX_PERSISTENT_WORKERS)):
                    continue
                try:
                    await self.inspect(plugin["id"], directory)
                except (CordisError, OSError):
                    entry = self._workers.pop(worker_key, None)
                    if entry:
                        await entry["worker"].aclose()
                    failure = self._worker_errors.get(worker_key)
                    if failure is None or _runtime_record(failure["baseline"], key) != _runtime_record(record, key) or failure["host_config"] != self.config:
                        self._worker_errors[worker_key] = {"baseline": record, "host_config": self.config,
                                                           "workspace": directory, "message": "The plugin worker could not start."}

    async def aclose(self) -> None:
        """Dispose every owned plugin fiber and stop its isolated process."""
        self._closed = True
        async with self._worker_lock:
            identifiers = {key[1] for key in self._workers}
            entries = list(self._workers.values())
            self._workers.clear()
            self._worker_errors.clear()
            await asyncio.gather(*(entry["worker"].aclose() for entry in entries), return_exceptions=True)
            for plugin_id in identifiers:
                await asyncio.to_thread(self._cleanup_removed, plugin_id)


class CordisTool(BaseTool):
    """Plugin calls always pass through the normal explicit tool approval flow."""

    read_only = False

    def __init__(self, context: ToolContext, manager: CordisManager, plugin_id: str, definition: dict[str, Any], digest: str) -> None:
        super().__init__(context)
        self.manager = manager
        self.plugin_id = plugin_id
        self._digest = digest
        self.tool_name = definition["name"]
        self.name = f"cordis__{plugin_id}__{self.tool_name}"
        self.description = definition["description"]
        self.parameters = definition["input_schema"].get("properties", {})
        self.required = tuple(definition["input_schema"].get("required", []))
        self._definition = _parse(_json(definition))

    def schema(self) -> dict[str, Any]:
        return {**_parse(_json(self._definition)), "name": self.name}

    def _orchestration_controller(self) -> Any:
        if self.plugin_id != "orchestration":
            return None
        try:
            from libre_claw.core.cordis_host import active_orchestration_controller
            profile = self.manager.orchestration_profile(self.context.working_directory)
            if profile["digest"] != self._digest or profile["authorize"]() is not True:
                return None
            return active_orchestration_controller(self.plugin_id, self.context)
        except (CordisError, ValueError, PermissionError):
            return None

    @property
    def permission_level(self) -> PermissionLevel:
        # Per-task profile selection grants bounded delegation, matching the
        # native subagent tools. Every worker retains ordinary tool approvals.
        return "allow" if self._orchestration_controller() is not None else "ask"

    def is_read_only(self, arguments: Mapping[str, Any]) -> bool:
        controller = self._orchestration_controller()
        if controller is None:
            return False
        if self.tool_name in {"status", "wait", "cancel"}:
            return True
        if self.tool_name == "delegate":
            try:
                return controller.tasks_read_only(arguments.get("tasks")) is True
            except (ValueError, PermissionError):
                return False
        return False

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            from libre_claw.core.cordis_host import tool_attachments
            result = await self.manager._invoke(self.plugin_id, self.context.working_directory, self.tool_name, kwargs, expected_digest=self._digest, context=self.context)
            metadata = {key: result[key] for key in ("meta", "additional_contexts", "concludes_turn") if key in result}
            attachments = tool_attachments(result)
            return ToolResult(error=result["content"], metadata=metadata, attachments=attachments) if result["error"] else ToolResult(content=result["content"], metadata=metadata, attachments=attachments)
        except (CordisError, ValueError, PermissionError) as exc:
            return ToolResult(error=str(exc))
