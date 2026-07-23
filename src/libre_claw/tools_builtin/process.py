# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import atexit
import codecs
import os
import secrets
import signal
import time
from dataclasses import dataclass, field

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


DEFAULT_MAX_OUTPUT_CHARS = 20_000
MAX_OUTPUT_CHARS = 100_000
MAX_BUFFER_CHARS = 200_000
MAX_INPUT_CHARS = 100_000
MAX_PROCESS_SESSIONS = 16
MAX_PROCESS_TIMEOUT_SECONDS = 86_400
MAX_WAIT_MS = 10_000
STREAM_READ_CHUNK_SIZE = 8192
READER_DRAIN_TIMEOUT_SECONDS = 1.0
STOP_GRACE_SECONDS = 2.0
_PROCESS_MANAGER_STATE_KEY = "process_manager"
_ACTIVE_PROCESSES: dict[int, asyncio.subprocess.Process] = {}
ProcessPoolKey = tuple[str, bool, bool, tuple[str, ...]]
_GLOBAL_PROCESS_MANAGERS: dict[ProcessPoolKey, "_ProcessManager"] = {}


@dataclass
class _ProcessSession:
    session_id: str
    command: str
    process: asyncio.subprocess.Process
    started_at: float
    timeout_seconds: int
    output_buffer: str = ""
    total_output_chars: int = 0
    dropped_output_chars: int = 0
    return_code: int | None = None
    timed_out: bool = False
    finished_at: float | None = None
    output_event: asyncio.Event = field(default_factory=asyncio.Event)
    reader_task: asyncio.Task[None] | None = None
    monitor_task: asyncio.Task[None] | None = None

    @property
    def status(self) -> str:
        if self.finished_at is None:
            return "running"
        if self.timed_out:
            return "timed_out"
        return "completed"

    @property
    def duration_ms(self) -> int:
        ended_at = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0, int((ended_at - self.started_at) * 1000))

    def append_output(self, text: str) -> None:
        if not text:
            return
        self.total_output_chars += len(text)
        combined = self.output_buffer + text
        if len(combined) > MAX_BUFFER_CHARS:
            dropped = len(combined) - MAX_BUFFER_CHARS
            self.output_buffer = combined[dropped:]
            self.dropped_output_chars += dropped
        else:
            self.output_buffer = combined
        self.output_event.set()

    def drain_output(self) -> tuple[str, int]:
        output = self.output_buffer
        dropped = self.dropped_output_chars
        self.output_buffer = ""
        self.dropped_output_chars = 0
        self.output_event.clear()
        return output, dropped

    def mark_finished(self, *, timed_out: bool = False) -> None:
        self.return_code = self.process.returncode
        self.timed_out = timed_out
        self.finished_at = time.monotonic()
        self.output_event.set()


class _ProcessManager:
    def __init__(self) -> None:
        self.sessions: dict[str, _ProcessSession] = {}

    async def start(self, command: str, timeout_seconds: int, working_directory: str) -> _ProcessSession:
        self._prune_completed()
        if len(self.sessions) >= MAX_PROCESS_SESSIONS:
            raise RuntimeError(
                f"At most {MAX_PROCESS_SESSIONS} process sessions may be retained; "
                "stop a running session or poll completed sessions before starting another"
            )

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=working_directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        session_id = self._new_session_id()
        session = _ProcessSession(
            session_id=session_id,
            command=command,
            process=process,
            started_at=time.monotonic(),
            timeout_seconds=timeout_seconds,
        )
        self.sessions[session_id] = session
        _ACTIVE_PROCESSES[process.pid] = process
        session.reader_task = asyncio.create_task(
            _read_process_output(session),
            name=f"libre-claw-process-reader-{session_id}",
        )
        session.monitor_task = asyncio.create_task(
            _monitor_process(session),
            name=f"libre-claw-process-monitor-{session_id}",
        )
        return session

    def get(self, session_id: str) -> _ProcessSession | None:
        return self.sessions.get(session_id)

    def list(self) -> list[_ProcessSession]:
        return sorted(self.sessions.values(), key=lambda item: item.started_at)

    def _new_session_id(self) -> str:
        while True:
            session_id = f"proc_{secrets.token_hex(4)}"
            if session_id not in self.sessions:
                return session_id

    def _prune_completed(self) -> None:
        completed = [session for session in self.sessions.values() if session.finished_at is not None]
        completed.sort(key=lambda item: item.finished_at or 0.0)
        while len(self.sessions) >= MAX_PROCESS_SESSIONS and completed:
            session = completed.pop(0)
            self.sessions.pop(session.session_id, None)


@register_tool
class ProcessTool(BaseTool):
    name = "process"
    description = (
        "Start, poll, write to, stop, or list managed long-running shell processes. "
        "Use this instead of bash for servers, builds, emulators, training, or interactive jobs."
    )
    parameters = {
        "action": {
            "type": "string",
            "enum": ["start", "poll", "write", "stop", "list"],
            "description": "Process operation",
        },
        "command": {"type": "string", "description": "Shell command for action=start"},
        "session_id": {
            "type": "string",
            "description": "Session ID returned by start; required for poll, write, and stop",
        },
        "input": {"type": "string", "description": "Text to send to stdin for action=write"},
        "close_stdin": {
            "type": "boolean",
            "description": "Close stdin after writing for action=write",
            "default": False,
        },
        "timeout": {
            "type": "integer",
            "description": (
                "Maximum total runtime in seconds for action=start; defaults to the configured "
                f"command timeout and is capped at {MAX_PROCESS_TIMEOUT_SECONDS}"
            ),
            "default": None,
        },
        "wait_ms": {
            "type": "integer",
            "description": f"Wait up to this many milliseconds for output or completion, capped at {MAX_WAIT_MS}",
            "default": 500,
        },
        "max_output_chars": {
            "type": "integer",
            "description": f"Maximum new output returned by this call, capped at {MAX_OUTPUT_CHARS}",
            "default": DEFAULT_MAX_OUTPUT_CHARS,
        },
    }
    required = ("action",)
    permission_level = "ask"

    async def execute(
        self,
        *,
        action: str,
        command: str = "",
        session_id: str = "",
        input: str = "",
        close_stdin: bool = False,
        timeout: int | None = None,
        wait_ms: int = 500,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ToolResult:
        if action not in {"start", "poll", "write", "stop", "list"}:
            return ToolResult(error="action must be start, poll, write, stop, or list")
        validation_error = _validate_limits(wait_ms, max_output_chars)
        if validation_error:
            return ToolResult(error=validation_error)

        manager = _process_manager(self)
        if action == "list":
            return _list_result(manager)
        if action == "start":
            return await self._start(
                manager,
                command=command,
                timeout=timeout,
                wait_ms=wait_ms,
                max_output_chars=max_output_chars,
            )

        if not session_id:
            return ToolResult(error=f"session_id is required for action={action}")
        session = manager.get(session_id)
        if session is None:
            return ToolResult(error=f"Unknown process session: {session_id}")

        if action == "poll":
            await _wait_for_activity(session, wait_ms)
            return _session_result(session, max_output_chars=max_output_chars)
        if action == "write":
            return await _write_result(
                session,
                input_text=input,
                close_stdin=close_stdin,
                wait_ms=wait_ms,
                max_output_chars=max_output_chars,
            )

        await _stop_process(session)
        return _session_result(session, max_output_chars=max_output_chars)

    async def _start(
        self,
        manager: _ProcessManager,
        *,
        command: str,
        timeout: int | None,
        wait_ms: int,
        max_output_chars: int,
    ) -> ToolResult:
        if not command.strip():
            return ToolResult(error="command is required for action=start")
        try:
            self.context.sandbox_policy().validate_command(command)
        except SandboxViolation as exc:
            return ToolResult(error=str(exc))

        timeout_seconds = self.context.command_timeout if timeout is None else timeout
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            return ToolResult(error="timeout must be an integer")
        if timeout_seconds < 1:
            return ToolResult(error="timeout must be >= 1")
        if timeout_seconds > MAX_PROCESS_TIMEOUT_SECONDS:
            return ToolResult(error=f"timeout must be <= {MAX_PROCESS_TIMEOUT_SECONDS}")

        try:
            session = await manager.start(
                command,
                timeout_seconds,
                str(self.context.working_directory),
            )
        except (OSError, RuntimeError) as exc:
            return ToolResult(error=str(exc))
        await _wait_for_activity(session, wait_ms)
        return _session_result(session, max_output_chars=max_output_chars)


def _process_manager(tool: BaseTool) -> _ProcessManager:
    existing = tool.context.shared_state.get(_PROCESS_MANAGER_STATE_KEY)
    if isinstance(existing, _ProcessManager):
        return existing
    pool_key: ProcessPoolKey = (
        str(tool.context.working_directory.resolve()),
        tool.context.restrict_to_working_dir,
        tool.context.allow_sudo,
        tool.context.blocked_patterns,
    )
    manager = _GLOBAL_PROCESS_MANAGERS.get(pool_key)
    if manager is None:
        manager = _ProcessManager()
        _GLOBAL_PROCESS_MANAGERS[pool_key] = manager
    tool.context.shared_state[_PROCESS_MANAGER_STATE_KEY] = manager
    return manager


def _validate_limits(wait_ms: int, max_output_chars: int) -> str | None:
    if not isinstance(wait_ms, int) or isinstance(wait_ms, bool):
        return "wait_ms must be an integer"
    if wait_ms < 0:
        return "wait_ms must be >= 0"
    if wait_ms > MAX_WAIT_MS:
        return f"wait_ms must be <= {MAX_WAIT_MS}"
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        return "max_output_chars must be an integer"
    if max_output_chars < 1:
        return "max_output_chars must be >= 1"
    if max_output_chars > MAX_OUTPUT_CHARS:
        return f"max_output_chars must be <= {MAX_OUTPUT_CHARS}"
    return None


async def _read_process_output(session: _ProcessSession) -> None:
    stream = session.process.stdout
    if stream is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            chunk = await stream.read(STREAM_READ_CHUNK_SIZE)
            if not chunk:
                break
            session.append_output(decoder.decode(chunk, final=False))
        session.append_output(decoder.decode(b"", final=True))
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError) as exc:
        session.append_output(f"\n[output reader error: {exc}]\n")


async def _monitor_process(session: _ProcessSession) -> None:
    timed_out = False
    try:
        try:
            await asyncio.wait_for(
                _wait_for_returncode(session.process),
                timeout=session.timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            _kill_process_group(session.process)
            try:
                await asyncio.wait_for(
                    _wait_for_returncode(session.process),
                    timeout=READER_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # The group is already killed; reader cleanup below is bounded separately.
                pass

        reader_task = session.reader_task
        if reader_task is not None:
            _, pending = await asyncio.wait(
                {reader_task},
                timeout=READER_DRAIN_TIMEOUT_SECONDS,
            )
            if pending:
                # A background descendant may still own the merged output pipe
                # after its shell exits. End the group and finish deterministically.
                _kill_process_group(session.process)
                _, pending = await asyncio.wait(
                    pending,
                    timeout=READER_DRAIN_TIMEOUT_SECONDS,
                )
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        _kill_process_group(session.process)
        if session.process.returncode is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(_wait_for_returncode(session.process)),
                    timeout=READER_DRAIN_TIMEOUT_SECONDS,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError, OSError):
                # Cleanup is best-effort; the original cancellation must still propagate.
                pass
        reader_task = session.reader_task
        if reader_task is not None and not reader_task.done():
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        raise
    finally:
        _ACTIVE_PROCESSES.pop(session.process.pid, None)
        session.mark_finished(timed_out=timed_out)


async def _wait_for_returncode(process: asyncio.subprocess.Process) -> int:
    while process.returncode is None:
        await asyncio.sleep(0.01)
    return process.returncode


async def _wait_for_activity(session: _ProcessSession, wait_ms: int) -> None:
    if wait_ms <= 0 or session.output_buffer or session.finished_at is not None:
        return
    try:
        await asyncio.wait_for(session.output_event.wait(), timeout=wait_ms / 1000)
    except asyncio.TimeoutError:
        return


async def _write_result(
    session: _ProcessSession,
    *,
    input_text: str,
    close_stdin: bool,
    wait_ms: int,
    max_output_chars: int,
) -> ToolResult:
    if session.finished_at is not None or session.process.returncode is not None:
        return ToolResult(error=f"Process session {session.session_id} is not running")
    if not input_text and not close_stdin:
        return ToolResult(error="input must not be empty unless close_stdin=true")
    if len(input_text) > MAX_INPUT_CHARS:
        return ToolResult(error=f"input must be <= {MAX_INPUT_CHARS} characters")

    stdin = session.process.stdin
    if stdin is None or stdin.is_closing():
        return ToolResult(error=f"Process session {session.session_id} stdin is closed")
    try:
        if input_text:
            stdin.write(input_text.encode("utf-8"))
            await stdin.drain()
        if close_stdin:
            stdin.close()
            await stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        return ToolResult(error=f"Could not write to process {session.session_id}: {exc}")

    await _wait_for_activity(session, wait_ms)
    return _session_result(session, max_output_chars=max_output_chars)


async def _stop_process(session: _ProcessSession) -> None:
    if session.finished_at is not None:
        return
    _signal_process_group(session.process, signal.SIGTERM)
    monitor_task = session.monitor_task
    if monitor_task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(monitor_task), timeout=STOP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        _kill_process_group(session.process)
        try:
            await asyncio.wait_for(asyncio.shield(monitor_task), timeout=READER_DRAIN_TIMEOUT_SECONDS + 1.0)
        except asyncio.TimeoutError:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)


def _session_result(session: _ProcessSession, *, max_output_chars: int) -> ToolResult:
    output, dropped_before_poll = session.drain_output()
    display, response_omitted = _bounded_output(output, max_output_chars)
    dropped = dropped_before_poll + response_omitted
    sections = [
        f"session_id: {session.session_id}",
        f"status: {session.status}",
        f"pid: {session.process.pid}",
        f"duration_ms: {session.duration_ms}",
    ]
    if session.return_code is not None:
        sections.append(f"exit_code: {session.return_code}")
    if dropped:
        sections.append(f"output_dropped_chars: {dropped}")
    if display:
        sections.append("output:\n" + display.rstrip())
    elif session.status == "running":
        sections.append("output: [no new output]")
    else:
        sections.append("output: [process finished with no new output]")

    return ToolResult(
        content="\n".join(sections),
        metadata={
            "session_id": session.session_id,
            "status": session.status,
            "pid": session.process.pid,
            "exit_code": session.return_code,
            "timed_out": session.timed_out,
            "duration_ms": session.duration_ms,
            "new_output_chars": len(output),
            "total_output_chars": session.total_output_chars,
            "output_dropped_chars": dropped,
        },
    )


def _list_result(manager: _ProcessManager) -> ToolResult:
    sessions = manager.list()
    if not sessions:
        return ToolResult(content="No process sessions.", metadata={"count": 0, "running": 0})
    lines = [
        (
            f"{session.session_id} status={session.status} pid={session.process.pid} "
            f"duration_ms={session.duration_ms} buffered_chars={len(session.output_buffer)} "
            f"command={_compact_command(session.command)}"
        )
        for session in sessions
    ]
    return ToolResult(
        content="\n".join(lines),
        metadata={
            "count": len(sessions),
            "running": sum(session.status == "running" for session in sessions),
        },
    )


def _compact_command(command: str, limit: int = 160) -> str:
    compact = " ".join(command.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _bounded_output(output: str, max_chars: int) -> tuple[str, int]:
    if len(output) <= max_chars:
        return output, 0
    head_chars = (max_chars + 1) // 2
    tail_chars = max_chars - head_chars
    omitted = len(output) - max_chars
    head = output[:head_chars]
    tail = output[-tail_chars:] if tail_chars else ""
    marker = (
        f"\n... truncated {omitted} characters; "
        f"showing first {len(head)} and last {len(tail)} ...\n"
    )
    return head + marker + tail, omitted


def _signal_process_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.terminate()
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        if process.returncode is None:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        if process.returncode is None:
            process.kill()


def _kill_active_processes_at_exit() -> None:
    for process_group, process in tuple(_ACTIVE_PROCESSES.items()):
        try:
            if os.name == "nt":
                if process.returncode is None:
                    process.kill()
            else:
                os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            # Processes can exit during interpreter teardown; atexit cleanup must not raise.
            pass


atexit.register(_kill_active_processes_at_exit)
