# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog

from libre_claw.auth.codex import CodexCommandResult, CodexCommandEvent, codex_status, stream_codex_command
from libre_claw.core.session import ChatMessage
from libre_claw.providers.base import Done, LLMProvider, ProviderError, StreamEvent, TextDelta, ToolSchema


class CodexProvider(LLMProvider):
    """Provider bridge that delegates a turn to the authenticated Codex CLI."""

    def __init__(
        self,
        model: str,
        working_directory: Path,
        executable: str = "codex",
        sandbox: str = "workspace-write",
        approval_policy: str = "never",
        timeout: int = 900,
    ) -> None:
        self.model = model
        self.working_directory = working_directory
        self.executable = executable
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.timeout = timeout
        self._logger = structlog.get_logger(__name__)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del tools, stream, temperature, max_tokens

        status = await codex_status(self.executable)
        if not status.logged_in:
            yield ProviderError(
                "Codex is not logged in. Run `/codex login` inside Libre Claw, "
                "or `libre-claw auth codex-login` in a terminal."
            )
            return

        prompt = _format_codex_prompt(messages, system)
        args = [
            self.executable,
            "--ask-for-approval",
            self.approval_policy,
            "exec",
            "--json",
            "--ephemeral",
            "--model",
            self.model,
            "--sandbox",
            self.sandbox,
            "--cd",
            str(self.working_directory),
            "-",
        ]
        try:
            emitted = False
            result: CodexCommandResult | None = None
            async with asyncio.timeout(self.timeout):
                async for event in stream_codex_command(args, input_text=prompt):
                    if isinstance(event, CodexCommandResult):
                        result = event
                        continue
                    if event.stream == "stderr":
                        continue
                    for text in _extract_codex_text(event.text):
                        emitted = True
                        yield TextDelta(text)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            yield ProviderError(f"Codex provider timed out after {self.timeout} seconds.")
            return
        except Exception as exc:
            self._logger.warning("codex_provider_failed", error=str(exc))
            yield ProviderError(f"Codex provider request failed: {exc}")
            return

        if result is None:
            yield ProviderError("Codex provider ended without an exit status.")
            return

        if result.exit_code != 0:
            yield ProviderError(f"Codex provider exited with {result.exit_code}:\n{result.output}")
            return

        if not emitted and result.stdout.strip():
            yield TextDelta(result.stdout.strip())

        yield Done(stop_reason="codex_cli")


def _format_codex_prompt(messages: Sequence[ChatMessage], system: str | None) -> str:
    lines = [
        "You are being invoked by Libre Claw as a Codex-backed provider.",
        "Use Codex CLI's authenticated ChatGPT/Codex session for this turn.",
    ]
    if system:
        lines.extend(["", "Libre Claw system prompt:", system.strip()])

    lines.append("")
    lines.append("Conversation:")
    for message in messages:
        role = message.role.capitalize()
        content = _message_text(message)
        if content:
            lines.append(f"{role}: {content}")

    return "\n".join(lines).strip() + "\n"


def _message_text(message: ChatMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "tool_result":
            parts.append(f"Tool result: {block.get('content', '')}")
        elif block_type == "tool_use":
            parts.append(f"Tool request: {block.get('name', '')} {block.get('input', {})}")
    return "\n".join(part for part in parts if part)


def _extract_codex_text(stdout: str) -> list[str]:
    """Extract assistant text from Codex JSONL with a permissive fallback parser."""
    texts: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            texts.append(raw_line + "\n")
            continue
        text = _text_from_event(event)
        if text:
            texts.append(text)
    return texts


def _text_from_event(event: Mapping[str, Any]) -> str:
    candidates = [
        event.get("delta"),
        event.get("text"),
        event.get("content"),
        event.get("message"),
        event.get("last_message"),
        event.get("output_text"),
    ]
    for candidate in candidates:
        text = _coerce_text(candidate)
        if text:
            return text

    item = event.get("item")
    if isinstance(item, Mapping):
        text = _text_from_event(item)
        if text:
            return text

    return ""


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "content", "message"):
            nested = _coerce_text(value.get(key))
            if nested:
                return nested
    if isinstance(value, list):
        return "".join(_coerce_text(item) for item in value)
    return ""
