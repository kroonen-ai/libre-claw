# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import signal
import shutil
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal


class CodexCliError(RuntimeError):
    """Raised when the Codex CLI cannot be launched."""


@dataclass(frozen=True)
class CodexCommandResult:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)


@dataclass(frozen=True)
class CodexCommandEvent:
    stream: Literal["stdout", "stderr"]
    text: str


@dataclass(frozen=True)
class CodexStatus:
    available: bool
    logged_in: bool
    detail: str


def configured_codex_executable(settings: Mapping[str, object]) -> str:
    """Use the same CLI installation for authentication and provider requests."""
    executable = settings.get("executable", "codex")
    return executable if isinstance(executable, str) else "codex"


def codex_available(executable: str = "codex") -> bool:
    """Return whether a Codex CLI executable is on PATH."""
    return shutil.which(executable) is not None


async def codex_status(executable: str = "codex") -> CodexStatus:
    """Check Codex CLI login status without reading private credential files."""
    if not codex_available(executable):
        return CodexStatus(
            available=False,
            logged_in=False,
            detail="Codex CLI is not installed or is not on PATH.",
        )

    result = await run_codex_command([executable, "login", "status"], timeout=10)
    output = result.output or f"codex login status exited with {result.exit_code}"
    logged_in = result.exit_code == 0 and "logged in" in output.lower()
    return CodexStatus(available=True, logged_in=logged_in, detail=output)


async def codex_login(executable: str = "codex", device_auth: bool = True) -> CodexCommandResult:
    """Start the supported Codex login flow."""
    args = [executable, "login"]
    if device_auth:
        args.append("--device-auth")
    return await run_codex_command(args, timeout=None)


async def codex_logout(executable: str = "codex") -> CodexCommandResult:
    """Remove Codex CLI credentials through the supported Codex command."""
    return await run_codex_command([executable, "logout"], timeout=30)


async def stream_codex_command(
    args: Sequence[str],
    input_text: str | None = None,
) -> AsyncIterator[CodexCommandEvent | CodexCommandResult]:
    """Run a Codex CLI command and stream stdout/stderr events."""
    if not args:
        raise CodexCliError("No Codex command was provided.")

    executable = args[0]
    if not codex_available(executable):
        raise CodexCliError(f"Codex CLI executable not found: {executable}")

    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    queue: asyncio.Queue[CodexCommandEvent | None] = asyncio.Queue()

    async def read_stream(reader: asyncio.StreamReader | None, stream: Literal["stdout", "stderr"]) -> None:
        if reader is None:
            await queue.put(None)
            return
        try:
            while True:
                chunk = await reader.readline()
                if not chunk:
                    return
                text = chunk.decode("utf-8", "replace")
                if stream == "stdout":
                    stdout_chunks.append(text)
                else:
                    stderr_chunks.append(text)
                await queue.put(CodexCommandEvent(stream=stream, text=text))
        finally:
            await queue.put(None)

    readers = [
        asyncio.create_task(read_stream(process.stdout, "stdout")),
        asyncio.create_task(read_stream(process.stderr, "stderr")),
    ]
    finished = False
    try:
        if input_text is not None and process.stdin is not None:
            process.stdin.write(input_text.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

        completed_readers = 0
        while completed_readers < len(readers):
            event = await queue.get()
            if event is None:
                completed_readers += 1
                continue
            yield event

        await asyncio.gather(*readers)
        exit_code = await process.wait()
        finished = True
        yield CodexCommandResult(
            args=tuple(args),
            exit_code=exit_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )
    finally:
        if not finished:
            await _stop_codex_process(process)
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def _stop_codex_process(process: asyncio.subprocess.Process) -> None:
    # Each invocation owns its process group, including shell commands started by the CLI.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        # The process may exit between the return-code check and the kill request.
        pass
    await process.wait()


async def run_codex_command(
    args: Sequence[str],
    input_text: str | None = None,
    timeout: float | None = None,
) -> CodexCommandResult:
    """Run a Codex CLI command and capture output for the TUI/CLI."""
    if not args:
        raise CodexCliError("No Codex command was provided.")

    executable = args[0]
    if not codex_available(executable):
        raise CodexCliError(f"Codex CLI executable not found: {executable}")

    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    completed = False
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input_text.encode("utf-8") if input_text is not None else None),
            timeout=timeout,
        )
        completed = True
    except TimeoutError:
        return CodexCommandResult(
            args=tuple(args),
            exit_code=124,
            stdout="",
            stderr=f"Codex command timed out after {timeout} seconds.",
        )
    finally:
        if not completed:
            await _stop_codex_process(process)

    return CodexCommandResult(
        args=tuple(args),
        exit_code=process.returncode or 0,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )
