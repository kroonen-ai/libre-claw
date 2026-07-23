# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


DEFAULT_MAX_OUTPUT_CHARS = 20000
MAX_OUTPUT_CHARS = 100000
STREAM_READ_CHUNK_SIZE = 8192
READER_DRAIN_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class CapturedOutput:
    text: str
    head_chars: int
    total_chars: int
    total_bytes: int
    truncated: bool


@dataclass
class _CaptureBuffer:
    max_chars: int
    head_parts: list[str] = field(default_factory=list)
    tail: str = ""
    head_chars: int = 0
    total_chars: int = 0
    total_bytes: int = 0

    @property
    def head_limit(self) -> int:
        return (self.max_chars + 1) // 2

    @property
    def tail_limit(self) -> int:
        return self.max_chars - self.head_limit

    def append_bytes(self, byte_count: int) -> None:
        self.total_bytes += byte_count

    def append_text(self, text: str) -> None:
        if not text:
            return
        self.total_chars += len(text)

        remaining = text
        if self.head_chars < self.head_limit:
            head_chunk = remaining[: self.head_limit - self.head_chars]
            self.head_parts.append(head_chunk)
            self.head_chars += len(head_chunk)
            remaining = remaining[len(head_chunk) :]

        if remaining and self.tail_limit:
            self.tail = (self.tail + remaining)[-self.tail_limit :]

    def captured(self) -> CapturedOutput:
        text = "".join(self.head_parts) + self.tail
        return CapturedOutput(
            text=text,
            head_chars=self.head_chars,
            total_chars=self.total_chars,
            total_bytes=self.total_bytes,
            truncated=self.total_chars > len(text),
        )


@register_tool
class BashTool(BaseTool):
    name = "bash"
    description = "Execute a shell command with timeout, sandbox checks, and bounded captured output."
    parameters = {
        "command": {"type": "string", "description": "Shell command to execute"},
        "timeout": {"type": "integer", "description": "Timeout in seconds", "default": None},
        "max_output_chars": {
            "type": "integer",
            "description": f"Maximum stdout/stderr characters to return per stream, capped at {MAX_OUTPUT_CHARS}",
            "default": DEFAULT_MAX_OUTPUT_CHARS,
        },
    }
    required = ("command",)
    permission_level = "ask"

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ToolResult:
        try:
            self.context.sandbox_policy().validate_command(command)
        except SandboxViolation as exc:
            return ToolResult(error=str(exc))

        timeout_value = self.context.command_timeout if timeout is None else timeout
        if timeout_value < 1:
            return ToolResult(error="timeout must be >= 1")
        if max_output_chars < 1:
            return ToolResult(error="max_output_chars must be >= 1")
        if max_output_chars > MAX_OUTPUT_CHARS:
            return ToolResult(error=f"max_output_chars must be <= {MAX_OUTPUT_CHARS}")

        process: asyncio.subprocess.Process | None = None
        started_at = time.monotonic()

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.context.working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
            stdout_buffer = _CaptureBuffer(max_output_chars)
            stderr_buffer = _CaptureBuffer(max_output_chars)
            stdout_task = asyncio.create_task(_read_stream(process.stdout, stdout_buffer))
            stderr_task = asyncio.create_task(_read_stream(process.stderr, stderr_buffer))
            try:
                await asyncio.wait_for(_wait_for_returncode(process), timeout=timeout_value)
            except asyncio.TimeoutError:
                _terminate_process_group(process)
                try:
                    await asyncio.wait_for(
                        _wait_for_returncode(process),
                        timeout=READER_DRAIN_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # The group is terminated; reader-task cleanup below is bounded separately.
                    pass
                await _finish_reader_tasks(process, stdout_task, stderr_task)
                return _timeout_result(
                    process,
                    timeout_value=timeout_value,
                    started_at=started_at,
                    stdout_capture=stdout_buffer.captured(),
                    stderr_capture=stderr_buffer.captured(),
                )
            await _finish_reader_tasks(process, stdout_task, stderr_task)
            stdout_capture = stdout_buffer.captured()
            stderr_capture = stderr_buffer.captured()
        except asyncio.TimeoutError:
            if process is not None:
                _terminate_process_group(process)
            return ToolResult(error=f"Command timed out after {timeout_value} seconds")
        except asyncio.CancelledError:
            if process is not None:
                _terminate_process_group(process)
            if "stdout_task" in locals() and "stderr_task" in locals():
                await _cancel_reader_tasks(stdout_task, stderr_task)
            raise
        except OSError as exc:
            return ToolResult(error=str(exc))

        stdout_display = _display_output(stdout_capture)
        stderr_display = _display_output(stderr_capture)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        content = _format_command_output(process.returncode or 0, stdout_display, stderr_display, duration_ms)
        return ToolResult(
            content=content,
            metadata={
                "exit_code": process.returncode,
                "stdout": stdout_display,
                "stderr": stderr_display,
                "stdout_truncated": stdout_capture.truncated,
                "stderr_truncated": stderr_capture.truncated,
                "stdout_chars": stdout_capture.total_chars,
                "stderr_chars": stderr_capture.total_chars,
                "stdout_bytes": stdout_capture.total_bytes,
                "stderr_bytes": stderr_capture.total_bytes,
                "duration_ms": duration_ms,
            },
        )


def _format_command_output(exit_code: int, stdout: str, stderr: str, duration_ms: int) -> str:
    sections = [f"exit_code: {exit_code}", f"duration_ms: {duration_ms}"]
    if stdout:
        sections.append("stdout:\n" + stdout.rstrip())
    if stderr:
        sections.append("stderr:\n" + stderr.rstrip())
    return "\n".join(sections)


def _timeout_result(
    process: asyncio.subprocess.Process,
    *,
    timeout_value: int,
    started_at: float,
    stdout_capture: CapturedOutput,
    stderr_capture: CapturedOutput,
) -> ToolResult:
    stdout_display = _display_output(stdout_capture)
    stderr_display = _display_output(stderr_capture)
    error = f"Command timed out after {timeout_value} seconds"
    if stdout_display:
        error += "\npartial stdout:\n" + stdout_display.rstrip()
    if stderr_display:
        error += "\npartial stderr:\n" + stderr_display.rstrip()
    duration_ms = int((time.monotonic() - started_at) * 1000)
    return ToolResult(
        error=error,
        metadata={
            "exit_code": process.returncode,
            "timed_out": True,
            "stdout": stdout_display,
            "stderr": stderr_display,
            "stdout_truncated": stdout_capture.truncated,
            "stderr_truncated": stderr_capture.truncated,
            "stdout_chars": stdout_capture.total_chars,
            "stderr_chars": stderr_capture.total_chars,
            "stdout_bytes": stdout_capture.total_bytes,
            "stderr_bytes": stderr_capture.total_bytes,
            "duration_ms": duration_ms,
        },
    )


async def _read_stream(
    stream: asyncio.StreamReader | None,
    capture: _CaptureBuffer,
) -> None:
    if stream is None:
        return

    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    while True:
        chunk = await stream.read(STREAM_READ_CHUNK_SIZE)
        if not chunk:
            break
        capture.append_bytes(len(chunk))
        capture.append_text(decoder.decode(chunk, final=False))

    tail = decoder.decode(b"", final=True)
    capture.append_text(tail)


async def _wait_for_returncode(process: asyncio.subprocess.Process) -> int:
    # On Python 3.14, Process.wait() may wait for inherited output pipes even
    # after the direct child has exited. The return code is updated as soon as
    # the child watcher reaps that process, so use it as the completion signal.
    while process.returncode is None:
        await asyncio.sleep(0.01)
    return process.returncode


def _display_output(output: CapturedOutput) -> str:
    if not output.truncated:
        return output.text
    omitted = output.total_chars - len(output.text)
    head = output.text[: output.head_chars]
    tail = output.text[output.head_chars :]
    marker = (
        f"\n... truncated {omitted} characters; "
        f"showing first {len(head)} and last {len(tail)} ...\n"
    )
    return head + marker + tail


async def _finish_reader_tasks(
    process: asyncio.subprocess.Process,
    *tasks: asyncio.Task[None],
) -> None:
    _, pending = await asyncio.wait(tasks, timeout=READER_DRAIN_TIMEOUT_SECONDS)
    if pending:
        # A shell can exit while a background descendant still owns its stdout or
        # stderr pipe. Terminate that process group so a nominally completed
        # command cannot hang the agent forever while reader tasks wait for EOF.
        _terminate_process_group(process)
        _, pending = await asyncio.wait(pending, timeout=READER_DRAIN_TIMEOUT_SECONDS)
    if pending:
        await _cancel_reader_tasks(*pending)


async def _cancel_reader_tasks(*tasks: asyncio.Task[None]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
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
