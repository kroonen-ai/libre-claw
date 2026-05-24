# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from typing import Any

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


@register_tool
class BashTool(BaseTool):
    name = "bash"
    description = "Execute a shell command with timeout and captured output."
    parameters = {
        "command": {"type": "string", "description": "Shell command to execute"},
        "timeout": {"type": "integer", "description": "Timeout in seconds", "default": None},
    }
    required = ("command",)
    permission_level = "ask"

    async def execute(self, command: str, timeout: int | None = None) -> ToolResult:
        try:
            self.context.sandbox_policy().validate_command(command)
        except SandboxViolation as exc:
            return ToolResult(error=str(exc))

        timeout_value = timeout or self.context.command_timeout
        process: asyncio.subprocess.Process | None = None

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.context.working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_value)
        except asyncio.TimeoutError:
            if process is not None:
                process.kill()
                await process.wait()
            return ToolResult(error=f"Command timed out after {timeout_value} seconds")
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except OSError as exc:
            return ToolResult(error=str(exc))

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        content = _format_command_output(process.returncode or 0, stdout, stderr)
        return ToolResult(
            content=content,
            metadata={"exit_code": process.returncode, "stdout": stdout, "stderr": stderr},
        )


def _format_command_output(exit_code: int, stdout: str, stderr: str) -> str:
    sections = [f"exit_code: {exit_code}"]
    if stdout:
        sections.append("stdout:\n" + stdout.rstrip())
    if stderr:
        sections.append("stderr:\n" + stderr.rstrip())
    return "\n".join(sections)
