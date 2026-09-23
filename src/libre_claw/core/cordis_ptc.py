# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Task-owned PTC programs in separate, credential-free restricted Node processes."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from libre_claw.core.cordis_config import bounded_json
from libre_claw.core.cordis_process import terminate_cordis_process
from libre_claw.core.cordis_security import prepare_cordis_process
from libre_claw.core.runs import settle_finalization

MAX_FRAME = 256 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_RESERVED = {"console", "__dsh_main__", "__builtins__", "__name__", "__debug__", "eval", "arguments", "await", "yield", "return", "class", "new", "throw", "if", "else", "this", "super", "var", "let", "const", "function", "import", "export", "extends", "default", "delete", "in", "instanceof", "void", "typeof", "while", "do", "for", "try", "catch", "finally", "break", "continue", "switch", "case", "debugger", "with", "true", "false", "null", "implements", "interface", "package", "private", "protected", "public", "static", "enum", "False", "None", "True", "and", "as", "assert", "async", "def", "del", "elif", "except", "from", "global", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "match", "type", "_"}
_RUNNER = Path(__file__).resolve().parents[1] / "cordis_runtime" / "ptc-runner.mjs"


def _confined_command(command: tuple[str, ...], private: Path, cwd: Path, reads: tuple[Path, ...], writes: tuple[Path, ...]) -> tuple[str, ...]:
    # PTC executes model-authored programs, so add OS confinement in addition
    # to Node permissions. Ordinary reviewed Cordis extensions are different.
    argv = command[3:] if command[:1] == ("/usr/bin/sandbox-exec",) else command
    executable = Path(argv[0]).resolve()
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        paths = lambda items: " ".join(f"(subpath {json.dumps(str(item))})" for item in items)
        profile = f'''(version 1)(deny default)
        (allow process-exec (literal {json.dumps(str(executable))}))
        (allow process-info*)(allow signal (target self))(allow sysctl-read)(allow mach-lookup)(allow file-read-metadata)
        (allow file-read* {paths(['/usr/lib', '/System', '/Library/Apple', _RUNNER.parent, private, *reads])}
          (literal {json.dumps(str(executable))})(literal "/")(literal "/dev/null")(literal "/dev/urandom"))
        (allow file-write* {paths([private, *writes])}(literal "/dev/null"))'''
        return ("/usr/bin/sandbox-exec", "-p", profile, *argv)
    if sys.platform == "linux" and (bwrap := shutil.which("bwrap")):
        flags = [bwrap, "--die-with-parent", "--new-session", "--unshare-all"]
        for directory in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(directory).exists():
                flags.extend(("--ro-bind", directory, directory))
        flags.extend(("--proc", "/proc", "--dev", "/dev", "--bind", str(private), str(private),
                      "--dir", str(cwd), "--ro-bind", str(_RUNNER.parent), str(_RUNNER.parent)))
        if not any(executable.is_relative_to(directory) for directory in ("/usr", "/bin", "/lib", "/lib64")):
            flags.extend(("--ro-bind", str(executable), str(executable)))
        for directory in dict.fromkeys(reads):
            flags.extend(("--ro-bind", str(directory), str(directory)))
        for directory in dict.fromkeys(writes):
            flags.extend(("--bind", str(directory), str(directory)))
        return (*flags, "--chdir", str(cwd), *argv)
    raise PermissionError("Confined PTC requires macOS sandbox-exec or Linux bubblewrap.")


class CordisPtcPool:
    """The caller approves start through its task tool executor before calling it.

    Bindings stay in the plugin worker. Only their declared names and JSON calls
    enter this process bridge. A program never inherits Python credentials.
    """

    def __init__(self, workspace: Path, authorize: Callable[[], dict[str, Any]], *, node_executable: str = "node", max_seconds: float = 120) -> None:
        self.workspace = workspace.resolve()
        self.authorize = authorize
        self.node_executable = node_executable
        self.max_seconds = min(float(max_seconds), 120)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._starts: set[asyncio.Task] = set()
        self.closed = False

    def _check(self) -> dict[str, Any]:
        if self.closed:
            raise PermissionError("The PTC task has ended.")
        return self.authorize()

    def _spec(self, spec: Any) -> dict[str, Any]:
        bounded_json(spec, limit=MAX_FRAME, label="PTC program")
        if not isinstance(spec, dict) or set(spec) - {"program", "bindings", "cwd", "timeoutMs", "readOnly", "sandboxPolicy"}:
            raise ValueError("Invalid PTC execution request.")
        program = spec.get("program")
        if type(spec.get("readOnly", False)) is not bool:
            raise ValueError("PTC readOnly must be boolean.")
        policy = spec.get("sandboxPolicy")
        if policy is not None:
            if (not isinstance(policy, dict) or set(policy) - {"mode", "workspaceRoot"}
                    or policy.get("mode") not in {"read-only", "workspace-write"}):
                raise ValueError("PTC supports only the active workspace's read-only or granted-write policy.")
            policy_root = policy.get("workspaceRoot", str(self.workspace))
            if not isinstance(policy_root, str) or Path(policy_root).resolve() != self.workspace:
                raise PermissionError("PTC policy must use the active task workspace.")
        if not isinstance(program, str) or len(program.encode()) > 128 * 1024:
            raise ValueError("PTC program exceeds its source budget.")
        timeout = spec.get("timeoutMs", self.max_seconds * 1000)
        if type(timeout) not in (int, float) or not 0 < timeout <= self.max_seconds * 1000:
            raise ValueError("PTC requires a finite positive timeout within the task budget.")
        cwd = Path(spec.get("cwd") or self.workspace).resolve()
        if not cwd.is_relative_to(self.workspace) or not cwd.is_dir():
            raise PermissionError("PTC working directory must remain in the active task workspace.")
        bindings = spec.get("bindings", [])
        if not isinstance(bindings, list) or len(bindings) > 16:
            raise ValueError("Invalid PTC binding namespaces.")
        names = set()
        for namespace in bindings:
            if not isinstance(namespace, dict) or set(namespace) - {"global", "members", "errorClass"}:
                raise ValueError("Invalid PTC binding namespace.")
            identity = namespace.get("global")
            if not isinstance(identity, str) or not _NAME.fullmatch(identity) or identity in _RESERVED or identity in names:
                raise ValueError("Invalid or duplicate PTC global name.")
            names.add(identity)
            members = namespace.get("members")
            if (not isinstance(members, list) or len(members) > 128
                    or not all(isinstance(member, str) and len(member.encode()) <= 256 for member in members)
                    or len(set(members)) != len(members)):
                raise ValueError("Invalid PTC binding members.")
            error = namespace.get("errorClass")
            if error is not None:
                if not isinstance(error, dict) or set(error) != {"name", "memberNameProperty"}:
                    raise ValueError("Invalid PTC binding error class.")
                name, prop = error["name"], error["memberNameProperty"]
                if (not isinstance(name, str) or not _NAME.fullmatch(name) or name in _RESERVED or name in names
                        or not isinstance(prop, str) or not prop or len(prop) > 128
                        or prop in {"name", "message", "stack", "args", "with_traceback", "add_note"}
                        or re.fullmatch(r"__.+__", prop)):
                    raise ValueError("Invalid PTC binding error class fields.")
                names.add(name)
        return {"program": program, "bindings": bindings, "cwd": str(cwd), "timeoutMs": timeout,
                "readOnly": spec.get("readOnly", False) or policy is not None and policy["mode"] == "read-only"}

    async def start(self, spec: dict[str, Any]) -> dict[str, Any]:
        self._check()
        if len(self.jobs) + len(self._starts) >= 8:
            raise ValueError("PTC has reached its eight-program task limit.")
        owner = asyncio.current_task()
        self._starts.add(owner)
        try:
            return await self._start(spec)
        finally:
            self._starts.discard(owner)

    async def _start(self, spec: dict[str, Any]) -> dict[str, Any]:
        grants = copy.deepcopy(self._check())
        spec = self._spec(spec)
        temporary = tempfile.TemporaryDirectory(prefix="libre-claw-ptc-")
        process = None
        try:
            reads = tuple(Path(item).resolve() for item in grants.get("read_paths", []))
            granted_writes = tuple(Path(item).resolve() for item in grants.get("write_paths", []))
            writes = () if spec["readOnly"] else granted_writes
            if any(not path.is_relative_to(self.workspace) for path in (*reads, *granted_writes)):
                raise PermissionError("PTC grants must stay inside the active task workspace.")
            prepared, cancelled = await settle_finalization(asyncio.create_task(asyncio.to_thread(
                prepare_cordis_process, self.node_executable, _RUNNER,
                _RUNNER.parent, Path(temporary.name), read_paths=(*reads, *granted_writes), write_paths=writes)))
            if cancelled:
                raise asyncio.CancelledError
            self._check()
            command = _confined_command(prepared.command, Path(temporary.name).resolve(), Path(spec["cwd"]), (*reads, *granted_writes), writes)
            process, cancelled = await settle_finalization(asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=spec["cwd"], env=prepared.env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=MAX_FRAME + 1, close_fds=True)))
            if cancelled:
                raise asyncio.CancelledError
            self._check()
        except BaseException:
            if process is not None:
                await settle_finalization(asyncio.create_task(terminate_cordis_process(process)))
            temporary.cleanup()
            raise
        identity = uuid.uuid4().hex
        job = {"process": process, "temporary": temporary, "events": asyncio.Queue(maxsize=34),
               "pending": set(), "allowed": {(row["global"], member) for row in spec["bindings"] for member in row["members"]},
               "deadline": time.monotonic() + spec["timeoutMs"] / 1000, "result": None,
               "grants": grants, "lock": asyncio.Lock(), "done": False, "calls": 0}
        self.jobs[identity] = job
        job["reader"] = asyncio.create_task(self._read(job))
        job["stderr"] = asyncio.create_task(self._stderr(job))
        job["watcher"] = asyncio.create_task(self._watch(job))
        try:
            await self._send(job, spec)
        except BaseException:
            await self._stop(job, "abort", "PTC startup was cancelled.")
            raise
        return {"id": identity}

    async def _send(self, job: dict[str, Any], frame: dict[str, Any]) -> None:
        bounded_json(frame, limit=MAX_FRAME, label="PTC binding response")
        async with job["lock"]:
            stream = job["process"].stdin
            if stream is None or job["done"]:
                raise ValueError("PTC program has stopped.")
            stream.write(json.dumps(frame, allow_nan=False).encode() + b"\n")
            await stream.drain()

    async def _read(self, job: dict[str, Any]) -> None:
        total = 0
        try:
            while line := await job["process"].stdout.readline():
                total += len(line)
                if len(line) > MAX_FRAME or total > 2 * 1024 * 1024:
                    raise ValueError("PTC output exceeded its transport budget.")
                frame = json.loads(line)
                bounded_json(frame, limit=MAX_FRAME, label="PTC output")
                if frame.get("type") == "done":
                    if set(frame) != {"type", "result"}:
                        raise ValueError("Invalid PTC completion frame.")
                    result = frame.get("result")
                    if not isinstance(result, dict) or set(result) - {"logs", "value", "error"} or not isinstance(result.get("logs"), list) or not all(isinstance(item, str) for item in result["logs"]):
                        raise ValueError("Invalid PTC completion.")
                    if "error" in result and (not isinstance(result["error"], dict) or set(result["error"]) != {"kind", "message"}
                            or result["error"]["kind"] not in {"exception", "timeout", "abort", "worker-exit", "invalid-output", "output-limit", "protocol", "sandbox-unavailable"}
                            or not isinstance(result["error"]["message"], str) or len(result["error"]["message"]) > 8192):
                        raise ValueError("Invalid PTC error.")
                    await self._stop(job, result=result)
                    return
                call_id = frame.get("id")
                if (set(frame) != {"type", "id", "namespace", "member", "args"}
                        or frame.get("type") != "call" or type(call_id) is not int or not 0 < call_id < 2**53
                        or call_id in job["pending"] or (frame.get("namespace"), frame.get("member")) not in job["allowed"]
                        or not isinstance(frame.get("args"), list) or len(frame["args"]) != 1
                        or len(job["pending"]) >= 32 or job["calls"] >= 256):
                    raise ValueError("Invalid or excessive PTC binding request.")
                job["pending"].add(call_id)
                job["calls"] += 1
                job["events"].put_nowait(frame)
            if not job["done"]:
                await self._stop(job, "worker-exit", "PTC process exited before completion.")
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._stop(job, "protocol", "PTC returned invalid or excessive protocol traffic.")

    async def _stderr(self, job: dict[str, Any]) -> None:
        size = 0
        while chunk := await job["process"].stderr.read(4096):
            size += len(chunk)
            if size > 64 * 1024:
                await self._stop(job, "output-limit", "PTC diagnostic output exceeded its budget.")
                return

    async def _watch(self, job: dict[str, Any]) -> None:
        while not job["done"]:
            if time.monotonic() >= job["deadline"]:
                await self._stop(job, "timeout", "PTC program exceeded its elapsed-time budget.")
                return
            try:
                if self._check() != job["grants"]:
                    raise PermissionError
            except Exception:
                await self._stop(job, "abort", "PTC task access was revoked.")
                return
            await asyncio.sleep(min(0.1, max(0, job["deadline"] - time.monotonic())))

    async def _stop(self, job: dict[str, Any], kind: str = "abort", message: str = "PTC program cancelled.", *, result: dict[str, Any] | None = None) -> None:
        if "cleanup" not in job:
            job["done"] = True
            job["result"] = result if result is not None else {"logs": [], "error": {"kind": kind, "message": message}}
            job["cleanup"] = asyncio.create_task(self._cleanup(job, asyncio.current_task()))
        await asyncio.shield(job["cleanup"])

    async def _cleanup(self, job: dict[str, Any], initiator: asyncio.Task | None) -> None:
        tasks = [job[name] for name in ("reader", "stderr", "watcher") if job.get(name) is not None and job[name] is not initiator]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await terminate_cordis_process(job["process"])
        job["temporary"].cleanup()
        while not job["events"].empty():
            job["events"].get_nowait()
        job["events"].put_nowait({"type": "done", "result": job["result"]})

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        self._check()
        if method == "start":
            return await self.start(params)
        identity = params.get("id")
        job = self.jobs.get(identity) if isinstance(identity, str) else None
        if job is None:
            raise ValueError("Unknown PTC execution for this task.")
        if method == "cancel":
            await self._stop(job)
            return {"cancelled": True}
        if method == "read":
            if job["done"]:
                await asyncio.shield(job["cleanup"])
                return {"type": "done", "result": job["result"]}
            try:
                return await asyncio.wait_for(job["events"].get(), timeout=1)
            except TimeoutError:
                return {"type": "pending"}
        if method == "respond":
            call_id = params.get("call_id")
            if type(call_id) is not int or call_id not in job["pending"]:
                raise ValueError("Unknown PTC binding response.")
            job["pending"].remove(call_id)
            if "error" in params:
                if not isinstance(params["error"], str) or len(params["error"]) > 8192:
                    raise ValueError("Invalid PTC binding error.")
                frame = {"id": call_id, "error": params["error"]}
            elif "value" in params:
                frame = {"id": call_id, "value": params["value"]}
            else:
                raise ValueError("PTC binding response requires a value or error.")
            await self._send(job, frame)
            return {"ok": True}
        raise ValueError("Unknown PTC operation.")

    async def aclose(self) -> None:
        self.closed = True
        pending = [task for task in self._starts if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(self._stop(job) for job in self.jobs.values()))
        self.jobs.clear()
