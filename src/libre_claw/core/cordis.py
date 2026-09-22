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
from pathlib import Path, PurePosixPath
from typing import Any

from libre_claw.core.tools import BaseTool, ToolContext, ToolResult


MANIFEST_NAME = "libre-claw-plugin.json"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 256
MAX_RPC_LINE = 1024 * 1024
MAX_RPC_BYTES = 4 * 1024 * 1024
CLEANUP_GRACE_SECONDS = 3.0
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_EXCLUDED = {"node_modules", "__pycache__"}
_GRANT_KEYS = {"permissions", "grants", "allow_network", "read_paths", "write_paths"}


class CordisError(RuntimeError):
    """A plugin cannot be safely installed, enabled, or executed."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _parse(data: bytes | str) -> Any:
    try:
        return json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as exc:
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
    for current, directories, filenames in os.walk(directory, followlinks=False):
        parent = Path(current)
        retained = []
        for name in sorted(directories):
            child = parent / name
            if exclude and (name.startswith(".") or name in _EXCLUDED):
                continue
            if child.is_symlink():
                raise CordisError("Plugin symlinks are not allowed.")
            retained.append(name)
        directories[:] = retained
        for name in sorted(filenames):
            if exclude and (name.startswith(".") or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
                continue
            path = parent / name
            if path.is_symlink():
                raise CordisError("Plugin symlinks are not allowed.")
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
    plugin_id = _name(value.get("id"), "plugin id")
    if "__" in plugin_id:
        raise CordisError("Plugin ids cannot contain double underscores, which delimit tool namespaces.")
    for field in ("name", "version"):
        if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > 160:
            raise CordisError(f"Plugin manifest requires a valid {field}.")
    entry = value.get("entry")
    if not isinstance(entry, str) or not entry or "\\" in entry:
        raise CordisError("Plugin entry must be a relative JavaScript file.")
    entry_path = PurePosixPath(entry)
    if entry_path.is_absolute() or any(part in {".", ".."} or part.startswith(".") for part in entry_path.parts):
        raise CordisError("Plugin entry must remain inside its snapshot.")
    if entry_path.suffix not in {".js", ".mjs", ".cjs"}:
        raise CordisError("Plugin entry must be a JavaScript file.")
    if not isinstance(value.get("config", {}), dict):
        raise CordisError("Plugin config must be a JSON object.")
    if len(_json(value.get("config", {})).encode()) > 64 * 1024:
        raise CordisError("Plugin config exceeds 64 KiB.")
    tools = value.get("tools")
    if not isinstance(tools, list) or not 1 <= len(tools) <= 64:
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

    def __init__(self, root: Path | str | None = None, node_executable: str = "node", tool_timeout: float = 30) -> None:
        root_path = Path(root if root is not None else Path.home() / ".libre-claw" / "cordis").expanduser().absolute()
        if root_path.is_symlink():
            raise CordisError("Cordis registry root cannot be a symlink.")
        # Node permissions use canonical paths, including macOS /private/var.
        # Use those same paths for snapshots and initialization RPC arguments.
        self.root = root_path.resolve()
        self.node_executable = node_executable
        self.tool_timeout = float(tool_timeout)
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
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(_json(registry) + "\n")
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
        if manifest["id"] != plugin_id or manifest["entry"] not in files:
            raise CordisError("Plugin identity or entry does not match its snapshot.")
        return snapshot, manifest

    @_registry_mutation
    def install(self, source: str | Path) -> dict[str, Any]:
        source = Path(source).expanduser().absolute()
        files = _scan(source, exclude=True)
        if MANIFEST_NAME not in files:
            raise CordisError(f"Plugin requires {MANIFEST_NAME}.")
        manifest = _manifest(files[MANIFEST_NAME])
        if manifest["entry"] not in files:
            raise CordisError("Plugin entry is missing or excluded from installation.")
        plugin_id = manifest["id"]
        checksums = _checksums(files)
        digest = hashlib.sha256(_json(checksums).encode()).hexdigest()
        registry = self._read_registry()
        previous = registry["plugins"].get(plugin_id, {})
        record = {
            "digest": digest, "files": checksums,
            "name": manifest["name"], "version": manifest["version"],
            "workspaces": previous.get("workspaces", {}) if previous.get("digest") == digest else {},
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
    def enable(self, plugin_id: str, workspace: str | Path, allow_network: bool = False, read_paths=(), write_paths=()) -> dict[str, Any]:
        _, key = _workspace(workspace)
        if not isinstance(allow_network, bool):
            raise CordisError("Network permission must be explicitly true or false.")
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        self._verify(plugin_id, record)
        record["workspaces"][key] = {
            "allow_network": allow_network,
            "read_paths": _grant_paths(read_paths),
            "write_paths": _grant_paths(write_paths),
        }
        self._write_registry(registry)
        return self._describe(plugin_id, record, key)

    @_registry_mutation
    def disable(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        _, key = _workspace(workspace)
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        record["workspaces"].pop(key, None)
        self._write_registry(registry)
        return self._describe(plugin_id, record, key)

    @_registry_mutation
    def remove(self, plugin_id: str) -> dict[str, Any]:
        registry = self._read_registry()
        record = self._record(plugin_id, registry)
        self._snapshot(plugin_id, record)
        del registry["plugins"][plugin_id]
        self._write_registry(registry)
        directory = self.root / "plugins" / plugin_id
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
        return {"id": plugin_id, "removed": True}

    def _describe(self, plugin_id: str, record: dict[str, Any], workspace_key: str | None) -> dict[str, Any]:
        error = None
        tools: list[str] = []
        try:
            _, manifest = self._verify(plugin_id, record)
            tools = [tool["name"] for tool in manifest["tools"]]
        except (CordisError, OSError) as exc:
            error = str(exc)
        grants = record.get("workspaces", {}).get(workspace_key) if workspace_key else None
        result = {
            "id": plugin_id, "name": record.get("name", plugin_id), "version": record.get("version", ""),
            "enabled": grants is not None,
            "integrity": "changed" if error else "valid",
            "tools": tools, "tool_count": len(tools),
            "grants": grants or {"allow_network": False, "read_paths": [], "write_paths": []},
        }
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
                tools.extend(CordisTool(context, self, plugin["id"], item, record["digest"]) for item in manifest["tools"])
            except (CordisError, OSError):
                continue
        return tools

    async def inspect(self, plugin_id: str, workspace: str | Path) -> dict[str, Any]:
        return await self._invoke(plugin_id, workspace)

    async def _invoke(self, plugin_id: str, workspace: str | Path, tool_name: str | None = None, arguments: dict[str, Any] | None = None, *, expected_digest: str | None = None) -> dict[str, Any]:
        from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process

        started = time.monotonic()
        _, key = _workspace(workspace)
        record = self._record(plugin_id)
        if expected_digest is not None and record["digest"] != expected_digest:
            raise CordisError("Plugin changed since this task loaded its tools. Start a new task to use the updated plugin.")
        grants = record["workspaces"].get(key)
        if not isinstance(grants, dict):
            raise CordisError("Plugin is not enabled for this workspace.")
        if not isinstance(grants.get("allow_network"), bool) or not isinstance(grants.get("read_paths"), list) or not isinstance(grants.get("write_paths"), list):
            raise CordisError("Invalid plugin grants; enable the plugin again.")
        snapshot, manifest = self._verify(plugin_id, record)
        if tool_name is not None and tool_name not in {tool["name"] for tool in manifest["tools"]}:
            raise CordisError("Plugin tool is not declared in its manifest.")
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
        process = None
        stderr_task = None
        request_id = 0
        response_bytes = 0

        async def rpc(method: str, params: dict[str, Any] | None = None):
            nonlocal request_id, response_bytes
            request_id += 1
            data = (_json({"id": request_id, "method": method, "params": params or {}}) + "\n").encode()
            if len(data) > MAX_RPC_LINE:
                raise CordisError("Plugin request exceeds the allowed size.")
            try:
                process.stdin.write(data)
                await process.stdin.drain()
                line = await process.stdout.readline()
            except (BrokenPipeError, ConnectionError, ValueError) as exc:
                raise CordisError("Plugin runtime closed or exceeded the output limit.") from exc
            response_bytes += len(line)
            if not line or len(line) > MAX_RPC_LINE or response_bytes > MAX_RPC_BYTES:
                raise CordisError("Plugin runtime closed or exceeded the output limit.")
            reply = _parse(line)
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
            await rpc("initialize", {"entry": str(snapshot / manifest["entry"]), "plugin_id": plugin_id, "config": manifest["config"], "state_dir": str(state_dir)})
            catalog = await rpc("tools/list")
            actual = catalog.get("tools") if isinstance(catalog, dict) else None
            if not isinstance(actual, list) or not all(isinstance(tool, dict) and isinstance(tool.get("name"), str) for tool in actual):
                raise CordisError("Plugin runtime returned an invalid tool catalog.")
            if _json(sorted(actual, key=lambda tool: tool["name"])) != _json(sorted(manifest["tools"], key=lambda tool: tool["name"])):
                raise CordisError("Plugin runtime tools differ from the installed manifest; no tool was executed.")
            if tool_name is not None:
                result = await rpc("tools/call", {"name": tool_name, "arguments": arguments or {}})
                if not isinstance(result, dict) or not isinstance(result.get("content"), str) or not isinstance(result.get("error"), bool):
                    raise CordisError("Plugin runtime returned an invalid tool result.")
                return result
            result = await rpc("inspect")
            if not isinstance(result, dict) or result.get("plugin_id") != plugin_id or result.get("state") != "ACTIVE":
                raise CordisError("Plugin runtime did not activate the requested plugin.")
            return {**self._describe(plugin_id, record, key), "state": result["state"], "runtime_version": result.get("runtime_version"), "isolation": prepared.isolation}

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
            result = await asyncio.wait_for(run(), timeout=max(0.0, self.tool_timeout - (time.monotonic() - started)))
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


class CordisTool(BaseTool):
    """Plugin calls always pass through the normal explicit tool approval flow."""

    permission_level = "ask"
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

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await self.manager._invoke(self.plugin_id, self.context.working_directory, self.tool_name, kwargs, expected_digest=self._digest)
            return ToolResult(error=result["content"]) if result["error"] else ToolResult(content=result["content"])
        except CordisError as exc:
            return ToolResult(error=str(exc))
