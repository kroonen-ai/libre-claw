# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Task-owned filesystem and confined process effects for imported Harness tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import stat
import tempfile
from pathlib import Path
from typing import Any

from libre_claw.core.cordis_config import bounded_json
from libre_claw.core.runs import settle_finalization
from libre_claw.core.tools import BaseTool, ToolContext, ToolResult


MAX_FILE = 256 * 1024
MAX_OUTPUT = 64 * 1024


def _text(value: Any, name: str, maximum: int = MAX_FILE) -> str:
    if not isinstance(value, str) or "\0" in value or len(value.encode()) > maximum:
        raise ValueError(f"Invalid {name}.")
    return value


def _integer(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Invalid {name}.")
    return value


def _version(info: os.stat_result) -> str:
    return hashlib.sha256(f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}".encode()).hexdigest()


def _info(info: os.stat_result) -> dict[str, Any]:
    kind = "file" if stat.S_ISREG(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else "other"
    return {"version": _version(info), "type": kind, "size": info.st_size}


class _HostEffect(BaseTool):
    description = "Approved Harness host operation"
    parameters = {}
    permission_level = "ask"

    def __init__(self, context: ToolContext, name: str, read_only: bool, execute: Any) -> None:
        self.name, self.read_only, self.operation = name, read_only, execute
        super().__init__(context)

    async def execute(self, **arguments: Any) -> ToolResult:
        value = await self.operation()
        bounded_json(value, limit=768 * 1024, label="Harness host result")
        return ToolResult(content=json.dumps(value), metadata={"harness_result": value})


class HarnessHostServices:
    """Resources belong to one plugin and one exact active Python Session."""

    def __init__(self, plugin_id: str, context: ToolContext, authorize: Any, *, config: Any = None) -> None:
        self.plugin_id, self.context, self.authorize = plugin_id, context, authorize
        self.session = context.shared_state.get("agent_session")
        self.root = context.working_directory.resolve()
        self.lock = asyncio.Lock()
        self.processes: dict[str, dict[str, Any]] = {}
        self.children: set[str] = set()
        self.monitors: set[asyncio.Task[Any]] = set()
        self.ptc = None
        self.lsp = None
        self.config = config
        self.actor_token = ""
        self.notices = 0
        self.closed = False
        self._counter = 0

    def _check(self) -> dict[str, Any]:
        if self.closed or self.context.shared_state.get("agent_session") is not self.session:
            raise PermissionError("The plugin task has ended or changed.")
        grants = self.authorize()
        if self.context.working_directory.resolve() != self.root:
            raise PermissionError("The task workspace changed on disk.")
        return grants

    def _path(self, value: Any, *, cwd: Any = None, write: bool = False, follow: bool = True) -> Path:
        grants = self._check()
        raw = Path(_text(value, "path", 8192))
        base = self.root if cwd is None else Path(_text(cwd, "working directory", 8192)).resolve()
        candidate = raw if raw.is_absolute() else base / raw
        candidate = candidate.resolve() if follow else candidate.parent.resolve() / candidate.name
        allowed = [Path(item) for item in grants.get("write_paths" if write else "read_paths", [])]
        if not write:
            allowed += [Path(item) for item in grants.get("write_paths", [])]
        if not candidate.is_relative_to(self.root) or not any(candidate.is_relative_to(path) for path in allowed):
            raise PermissionError("The path is outside the task's explicit plugin grants.")
        return candidate

    async def _effect(self, name: str, arguments: dict[str, Any], operation: Any, *, read_only: bool) -> Any:
        self._check()
        executor = self.context.shared_state.get("harness_tool_executor")
        if not callable(executor):
            raise PermissionError("This task cannot approve plugin host operations.")
        result = await executor(_HostEffect(self.context, name, read_only, operation), arguments)
        if result.error:
            raise PermissionError(result.error)
        self._check()
        return result.metadata["harness_result"]

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        self._check()
        if method.startswith("fs."):
            return await self._fs(method[3:], params)
        if method.startswith("shell."):
            return await self._shell(method[6:], params)
        if method.startswith("agents."):
            return await self._agents(method[7:], params)
        if method == "lsp.query":
            if self.lsp is None:
                from libre_claw.core.cordis_lsp import CordisLspPool
                servers = self.config.cordis.lsp_servers if self.config is not None else {}
                self.lsp = CordisLspPool(self.root, self._check, self._effect, servers)
            return await self.lsp.dispatch("query", params)
        if method.startswith("ptc."):
            if self.ptc is None:
                from libre_claw.core.cordis_ptc import CordisPtcPool
                self.ptc = CordisPtcPool(self.root, self._check, max_seconds=self.context.command_timeout)
            operation = method[4:]
            if operation == "start":
                return await self._effect("bash", {"command": "Run isolated Harness TypeScript:\n" + str(params.get("program", ""))},
                    lambda: self.ptc.dispatch(operation, params), read_only=False)
            return await self.ptc.dispatch(operation, params)
        if method == "agent.notice":
            agent = self.context.shared_state.get("harness_agent")
            if (set(params) != {"owner", "text"} or params["owner"] != self.actor_token
                    or agent is None or agent.session is not self.session or not agent.accepting_control):
                raise PermissionError("Plugin notices require the active owning task.")
            if self.notices >= 64:
                raise ValueError("Plugin notice limit reached for this task.")
            text = _text(params["text"], "plugin notice", 4096)
            self.notices += 1
            self.session.queue_steering(f"Plugin {self.plugin_id} notification:\n{text}")
            await agent._checkpoint()
            return {"queued": True}
        raise PermissionError("Unknown Harness host service operation.")

    async def _fs(self, method: str, params: dict[str, Any]) -> Any:
        fields = {"resolve": {"path", "cwd"}, "lstat": {"path", "cwd"}, "stat": {"target"},
                  "readText": {"target"}, "readBytes": {"target", "maxBytes"},
                  "readByteRange": {"target", "offset", "length"}, "listDir": {"target"},
                  "writeText": {"target", "content", "expected"},
                  "editText": {"target", "edit", "expected"}}
        if method not in fields or set(params) - fields[method]:
            raise ValueError("Invalid filesystem operation.")
        write = method in {"writeText", "editText"}
        target = params.get("target")
        if method not in {"resolve", "lstat"}:
            if not isinstance(target, dict) or set(target) != {"targetKey", "displayPath"} or target["targetKey"] != target["displayPath"]:
                raise ValueError("Invalid filesystem target.")
        path = self._path(params.get("path") if method in {"resolve", "lstat"} else target["targetKey"],
                          cwd=params.get("cwd"), write=write, follow=method != "lstat")
        if method == "resolve":
            return {"targetKey": str(path), "displayPath": str(path)}
        name = "write_file" if method == "writeText" else "edit_file" if write else "list_directory" if method == "listDir" else "read_file"
        arguments = {"path": str(path)}
        if method == "writeText":
            arguments["content"] = _text(params.get("content"), "file content")
        if method == "editText":
            edit = params.get("edit")
            if not isinstance(edit, dict) or set(edit) != {"oldString", "newString", "replaceAll"} or type(edit["replaceAll"]) is not bool:
                raise ValueError("Invalid literal edit.")
            arguments.update(old_text=_text(edit["oldString"], "old text"), new_text=_text(edit["newString"], "new text"), replace_all=edit["replaceAll"])
        async def operation() -> Any:
            async with self.lock:
                self._path(str(path), write=write, follow=method != "lstat")
                # Join thread mutations before releasing task/file ownership.
                task = asyncio.create_task(asyncio.to_thread(self._fs_sync, method, path, params))
                result, cancelled = await settle_finalization(task)
                if cancelled:
                    raise asyncio.CancelledError
                return result
        return await self._effect(name, arguments, operation, read_only=not write)

    @staticmethod
    def _parent_fd(path: Path) -> int:
        """Walk canonical parents without following replacements by symlinks."""
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise PermissionError("Safe directory-relative filesystem access is unavailable on this platform.")
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parent.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _fs_sync(self, method: str, path: Path, params: dict[str, Any]) -> Any:
        parent = self._parent_fd(path)
        try:
            try:
                observed = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                observed = None
            if observed is not None and stat.S_ISLNK(observed.st_mode) and method != "lstat":
                raise PermissionError("Filesystem target became a symbolic link.")
            if method in {"stat", "lstat"}:
                return None if observed is None else _info(observed)
            if method == "listDir":
                fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    names = sorted(os.listdir(fd))
                    if len(names) > 2048:
                        raise ValueError("Directory exceeds the bounded entry limit.")
                    result = []
                    for name in names:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        # Do not expose a target that follows a repository symlink outside its grants.
                        kind = _info(info)
                        if kind["type"] == "symlink":
                            kind["type"] = "other"
                        result.append({**kind, "name": name, "target": {"targetKey": str(path / name), "displayPath": str(path / name)}})
                    return result
                finally:
                    os.close(fd)
            before_bytes = None
            if observed is not None:
                if not stat.S_ISREG(observed.st_mode):
                    raise ValueError("FS_NOT_REGULAR_FILE")
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if _version(os.fstat(fd)) != _version(observed):
                        raise ValueError("FS_STALE_VERSION")
                    if method == "readByteRange":
                        offset = _integer(params.get("offset"), "offset", 2**53 - 1)
                        size = _integer(params.get("length"), "length", MAX_FILE)
                        os.lseek(fd, offset, os.SEEK_SET)
                        return base64.b64encode(os.read(fd, size)).decode()
                    limit = _integer(params.get("maxBytes", MAX_FILE), "byte limit", MAX_FILE)
                    if observed.st_size > limit:
                        raise ValueError("FS_TOO_LARGE")
                    before_bytes = os.read(fd, limit + 1)
                    if len(before_bytes) > limit:
                        raise ValueError("FS_TOO_LARGE")
                finally:
                    os.close(fd)
            if method == "readBytes":
                if before_bytes is None:
                    raise FileNotFoundError("FS_NOT_FOUND")
                return base64.b64encode(before_bytes).decode()
            before = None if before_bytes is None else before_bytes.decode("utf-8").replace("\r\n", "\n")
            if before is not None and "\0" in before:
                raise ValueError("FS_NOT_TEXT")
            if method == "readText":
                if before is None:
                    raise FileNotFoundError("FS_NOT_FOUND")
                return before
            expected = params.get("expected")
            if expected is not None:
                if not isinstance(expected, dict):
                    raise ValueError("Invalid filesystem version guard.")
                if method == "writeText" and expected == {"kind": "createIfAbsent"}:
                    if observed is not None:
                        raise ValueError("FS_NOT_OBSERVED")
                elif set(expected) == ({"kind", "version"} if method == "writeText" else {"version"}) and (method != "writeText" or expected["kind"] == "replaceIfVersion"):
                    if observed is None or expected["version"] != _version(observed):
                        raise ValueError("FS_STALE_VERSION")
                else:
                    raise ValueError("Invalid filesystem version guard.")
            if method == "writeText":
                after = _text(params.get("content"), "file content").replace("\r\n", "\n")
            elif method == "editText":
                if before is None:
                    raise FileNotFoundError("FS_NOT_FOUND")
                edit = params["edit"]
                old = _text(edit["oldString"], "old text").replace("\r\n", "\n")
                new = _text(edit["newString"], "new text").replace("\r\n", "\n")
                count = before.count(old) if old else 0
                if count == 0:
                    raise ValueError("FS_EDIT_NOT_FOUND")
                if count != 1 and not edit["replaceAll"]:
                    raise ValueError("FS_AMBIGUOUS_EDIT")
                after = before.replace(old, new, -1 if edit["replaceAll"] else 1)
                _text(after, "edited file")
            else:
                raise ValueError("Invalid filesystem operation.")
            temporary = ".libre-harness-" + os.urandom(12).hex()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(after.encode())
                    file.flush()
                    os.fsync(file.fileno())
                    if observed is not None:
                        os.fchmod(file.fileno(), stat.S_IMODE(observed.st_mode))
                self._path(str(path), write=True)
                try:
                    current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    current = None
                if (current is None) != (observed is None) or current is not None and _version(current) != _version(observed):
                    raise ValueError("FS_STALE_VERSION")
                if current is None:
                    os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=parent)
                else:
                    os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                result = {"version": _version(info), "before": before, "after": after}
                if method == "writeText":
                    result["operation"] = "create" if before is None else "update"
                return result
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    # Successful rename or create already consumed the staging file.
                    pass
        finally:
            os.close(parent)

    async def _shell(self, method: str, params: dict[str, Any]) -> Any:
        if method not in {"run", "start", "read", "wait", "kill"}:
            raise PermissionError("Unknown shell operation.")
        if method in {"read", "wait", "kill"}:
            if set(params) - {"id", "timeoutMs"} or params.get("id") not in self.processes:
                raise PermissionError("Unknown or foreign plugin process.")
            record = self.processes[params["id"]]
            if method == "kill":
                self._kill(record)
            if method == "wait":
                timeout = _integer(params.get("timeoutMs", 1000), "wait timeout", 60_000)
                await asyncio.wait({record["task"]}, timeout=timeout / 1000)
            return self._process_value(record, consume=method == "read")
        if set(params) != {"spec"} or not isinstance(params["spec"], dict):
            raise ValueError("Invalid shell request.")
        spec = params["spec"]
        if set(spec) - {"command", "workdir", "timeoutMs", "stdoutMaxBytes", "stdin", "env", "dshEnv", "sandboxPolicy"}:
            raise ValueError("Unsupported shell request field.")
        command = _text(spec.get("command"), "shell command", 32 * 1024)
        workdir = self._path(spec.get("workdir") or str(self.root))
        self.context.sandbox_policy().validate_command(command)
        timeout = _integer(spec.get("timeoutMs", self.context.command_timeout * 1000), "shell timeout", self.context.command_timeout * 1000)
        if timeout < 1:
            raise ValueError("Shell timeout must be positive.")
        output_limit = _integer(spec.get("stdoutMaxBytes", MAX_OUTPUT), "stdout limit", MAX_OUTPUT)
        if spec.get("sandboxPolicy", {}).get("mode") == "danger-full-access":
            raise PermissionError("Plugins cannot widen their explicit grants.")
        async def operation() -> Any:
            self._check()
            if len(self.processes) >= 32:
                raise ValueError("Plugin process limit reached.")
            record = await self._start_process(command, workdir, spec, output_limit, timeout)
            if method == "start":
                return {"id": record["id"], **self._process_value(record)}
            try:
                await asyncio.shield(record["task"])
                return record["result"]
            finally:
                self._kill(record)
                _, cancelled = await settle_finalization(record["task"])
                if cancelled:
                    raise asyncio.CancelledError
        return await self._effect("bash", {"command": command, "timeout": max(1, timeout // 1000)}, operation, read_only=False)

    async def _start_process(self, command: str, cwd: Path, spec: dict[str, Any], limit: int, timeout: int) -> dict[str, Any]:
        grants = self._check()
        stdin = _text(spec.get("stdin", ""), "stdin").encode()
        temporary = tempfile.TemporaryDirectory(prefix="libre-harness-")
        private = Path(temporary.name).resolve()
        reads = [str(Path(path)) for path in [*grants.get("read_paths", []), *grants.get("write_paths", [])] if Path(path).is_relative_to(self.root)]
        writes = [str(Path(path)) for path in grants.get("write_paths", []) if Path(path).is_relative_to(self.root)]
        if spec.get("sandboxPolicy", {}).get("mode") == "read-only":
            writes = []
        try:
            from libre_claw.core.cordis_process import prepare_host_process
            argv, env = prepare_host_process(
                ["/bin/bash", "--noprofile", "--norc", "-c", command], cwd, private,
                read_paths=reads, write_paths=writes, allow_network=grants.get("allow_network") is True,
            )
            for field in ("env", "dshEnv"):
                additions = spec.get(field, {})
                if not isinstance(additions, dict) or len(additions) > 64:
                    raise ValueError("Invalid shell environment.")
                for key, value in additions.items():
                    if (not isinstance(key, str) or not key.replace("_", "").isalnum()
                            or key in {"PATH", "HOME", "TMPDIR", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"}
                            or key.startswith(("LD_", "DYLD_", "PYTHON", "NODE_"))):
                        raise PermissionError("The requested shell environment setting is not allowed.")
                    env[key] = _text(value, "environment value", 8192)
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True))
            process, cancelled = await settle_finalization(spawning)
        except BaseException:
            temporary.cleanup()
            raise
        self._counter += 1
        record = {"id": f"process-{os.urandom(16).hex()}", "process": process, "stdout": bytearray(), "stderr": bytearray(),
                  "total": {"stdout": 0, "stderr": 0}, "cursor": {"stdout": 0, "stderr": 0}, "result": None,
                  "temporary": temporary, "timeout": timeout, "killed": False}
        self.processes[record["id"]] = record
        async def drain(name: str) -> None:
            stream = getattr(process, name)
            while chunk := await stream.read(8192):
                record["total"][name] += len(chunk)
                record[name].extend(chunk)
                del record[name][:-limit or len(record[name])]
        async def run() -> None:
            readers = [asyncio.create_task(drain(name)) for name in ("stdout", "stderr")]
            async def feed() -> None:
                if process.stdin is not None:
                    try:
                        process.stdin.write(stdin)
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        # A command may exit or close stdin before consuming input;
                        # the process monitor still records and joins its outcome.
                        pass
                    finally:
                        process.stdin.close()
            writer = asyncio.create_task(feed())
            timed_out = False
            try:
                deadline = asyncio.get_running_loop().time() + timeout / 1000
                while process.returncode is None:
                    self._check()
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        timed_out = True
                        self._kill(record)
                        break
                    try:
                        await asyncio.wait_for(process.wait(), min(0.2, remaining))
                    except asyncio.TimeoutError:
                        continue
            except (asyncio.CancelledError, Exception):
                self._kill(record)
            finally:
                self._kill(record)
                await process.wait()
                if not writer.done():
                    writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
                await asyncio.gather(*readers)
                if process.stdin is not None:
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        # An exited command may reject its remaining buffered input.
                        pass
                code = process.returncode
                record["result"] = {"exitCode": code if code >= 0 else None,
                    "signal": signal.Signals(-code).name if code < 0 else None,
                    "timedOut": timed_out, "aborted": record["killed"] and not timed_out, "timeoutMs": timeout,
                    **{name: {"text": bytes(record[name]).decode("utf-8", "replace"), "truncated": record["total"][name] > len(record[name])}
                       for name in ("stdout", "stderr")}}
                temporary.cleanup()
        record["task"] = asyncio.create_task(run())
        if cancelled:
            self._kill(record)
            await settle_finalization(record["task"])
            raise asyncio.CancelledError
        return record

    @staticmethod
    def _kill(record: dict[str, Any]) -> None:
        if record.get("kill_requested"):
            return
        if record["process"].returncode is None:
            record["killed"] = True
        try:
            os.killpg(record["process"].pid, signal.SIGKILL)
            record["kill_requested"] = True
        except ProcessLookupError:
            # The owned process group has already exited and needs no signal.
            pass

    @staticmethod
    def _process_value(record: dict[str, Any], *, consume: bool = False) -> dict[str, Any]:
        result = record["result"]
        delta, lossy = [], False
        if consume:
            for name in ("stdout", "stderr"):
                start = max(0, record["cursor"][name] - (record["total"][name] - len(record[name])))
                lossy |= record["cursor"][name] < record["total"][name] - len(record[name])
                value = bytes(record[name][start:]).decode("utf-8", "replace")
                if value:
                    delta.append(("\n[stderr]\n" if name == "stderr" else "") + value)
                record["cursor"][name] = record["total"][name]
        return {"status": "running" if result is None else "killed" if record["killed"] else "completed",
                "exitCode": result["exitCode"] if result else None, "signal": result["signal"] if result else None,
                "delta": "".join(delta), "lossy": lossy}

    async def _agents(self, method: str, params: dict[str, Any]) -> Any:
        if self._check().get("allow_model") is not True:
            raise PermissionError("Plugin agent creation requires explicit model access.")
        agent = self.context.shared_state.get("harness_agent")
        if agent is None or agent.session is not self.session or not agent.accepting_control:
            raise PermissionError("Delegation requires the active owning task.")
        manager = agent.subagents
        if manager is None:
            raise PermissionError("Subagent tools are disabled for this task.")
        if method == "run":
            if set(params) != {"task", "cwd", "options"} or not isinstance(params["options"], dict):
                raise ValueError("Invalid agent creation request.")
            options = params["options"]
            if set(options) - {"provider", "model"}:
                raise ValueError("This agent driver accepts explicit provider/model routes; per-worker caps use a reviewed team profile.")
            scope = self._path(params["cwd"])
            grants = self._check()
            owners = []
            for raw in grants.get("write_paths", []):
                path = Path(raw)
                if scope.is_relative_to(path):
                    owners.append(str(scope))
                elif path.is_relative_to(scope):
                    owners.append(str(path))
            arguments = {"task": _text(params["task"], "worker task", 16000), "scope": str(scope),
                         "provider": _text(options.get("provider", self.context.default_provider), "provider", 512),
                         "model": _text(options.get("model", self.context.default_model), "model", 512),
                         "read_only": not owners, "write_paths": sorted(set(owners)), "max_tool_calls": 20, "max_seconds": 180}
            async def spawn() -> Any:
                self._check()
                state = await manager.spawn(**arguments)
                self.children.add(state["id"])
                async def watch() -> None:
                    while manager._state(state["id"]).status in {"running", "blocked"}:
                        try:
                            self._check()
                        except Exception:
                            await manager.cancel(state["id"])
                            return
                        await asyncio.sleep(0.2)
                monitor = asyncio.create_task(watch())
                self.monitors.add(monitor)
                monitor.add_done_callback(self.monitors.discard)
                return state
            return await self._effect("subagent_spawn", arguments, spawn, read_only=not owners)
        if method not in {"status", "wait", "cancel", "steer"} or set(params) - {"id", "timeoutMs", "text"} or params.get("id") not in self.children:
            raise PermissionError("Unknown or foreign plugin worker.")
        identifier = params["id"]
        if method == "cancel":
            return await manager.cancel(identifier)
        if method == "steer":
            manager._state(identifier).session.queue_steering(_text(params.get("text"), "worker guidance", 16000))
            await manager._persist()
            return manager._state(identifier).snapshot()
        if method == "wait":
            timeout = _integer(params.get("timeoutMs", 1000), "worker wait", 60_000)
            return (await manager.wait([identifier], timeout / 1000))[0]
        return manager._state(identifier).snapshot()

    async def aclose(self) -> None:
        self.closed = True
        async def close():
            if self.ptc is not None:
                await self.ptc.aclose()
            if self.lsp is not None:
                await self.lsp.aclose()
            manager = getattr(self.context.shared_state.get("harness_agent"), "subagents", None)
            if manager is not None:
                for identifier in self.children:
                    await manager.cancel(identifier)
            for record in self.processes.values():
                self._kill(record)
            await asyncio.gather(*(record["task"] for record in self.processes.values()), return_exceptions=True)
            for monitor in self.monitors:
                monitor.cancel()
            await asyncio.gather(*self.monitors, return_exceptions=True)
        _, cancelled = await settle_finalization(asyncio.create_task(close()))
        if cancelled:
            raise asyncio.CancelledError
