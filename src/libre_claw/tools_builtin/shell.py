# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


DEFAULT_MAX_OUTPUT_CHARS = 20000
MAX_OUTPUT_CHARS = 100000


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
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_value)
        except asyncio.TimeoutError:
            if process is not None:
                _terminate_process(process)
                await process.wait()
            return ToolResult(error=f"Command timed out after {timeout_value} seconds")
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                _terminate_process(process)
                await process.wait()
            raise
        except OSError as exc:
            return ToolResult(error=str(exc))

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stdout_display, stdout_truncated = _truncate_output(stdout, max_output_chars)
        stderr_display, stderr_truncated = _truncate_output(stderr, max_output_chars)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        content = _format_command_output(process.returncode or 0, stdout_display, stderr_display, duration_ms)
        return ToolResult(
            content=content,
            metadata={
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
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


def _truncate_output(output: str, max_chars: int) -> tuple[str, bool]:
    if len(output) <= max_chars:
        return output, False
    omitted = len(output) - max_chars
    suffix = f"\n... truncated {omitted} characters ..."
    return output[:max_chars] + suffix, True


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
