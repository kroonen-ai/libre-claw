# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from pathlib import Path
from typing import Any

import structlog

from libre_claw.auth.codex import CodexCommandResult, codex_status, stream_codex_command
from libre_claw.core.session import ChatMessage
from libre_claw.providers.base import Done, LLMProvider, ProviderConfigurationError, ProviderError, StreamEvent, TextDelta, ToolSchema, Usage
from libre_claw.providers.capabilities import configured_reasoning_effort, has_images, prepare_request


CODEX_REPLAY_CHUNK_SIZE = 24
CODEX_REPLAY_DELAY = 0.015


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
        replay_chunk_size: int = CODEX_REPLAY_CHUNK_SIZE,
        replay_delay: float = CODEX_REPLAY_DELAY,
    ) -> None:
        self.model = model
        self.working_directory = working_directory
        self.executable = executable
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.timeout = timeout
        self.replay_chunk_size = replay_chunk_size
        self.replay_delay = replay_delay
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
        del stream, temperature
        try:
            prepare_request(self, messages, tools, max_tokens)
            effort = configured_reasoning_effort(self)
        except ProviderConfigurationError as exc:
            yield ProviderError(str(exc))
            return

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
        if effort:
            args[-1:-1] = ["--config", f"model_reasoning_effort={json.dumps(effort)}"]
        info = self.model_info
        if info is not None and info.context_window_tokens is not None and getattr(self, "auto_context_window", True):
            args[-1:-1] = ["--config", f"model_context_window={info.context_window_tokens}"]
        image_directory: tempfile.TemporaryDirectory[str] | None = None
        try:
            if any(has_images(message.content) for message in messages):
                image_directory = tempfile.TemporaryDirectory(prefix="libre-claw-codex-images-")
                image_args = await asyncio.to_thread(_write_codex_images, messages, Path(image_directory.name))
                args[-1:-1] = image_args
            emitted = False
            usage: Usage | None = None
            result: CodexCommandResult | None = None
            async with asyncio.timeout(self.timeout), aclosing(stream_codex_command(args, input_text=prompt)) as command:
                async for event in command:
                    if isinstance(event, CodexCommandResult):
                        result = event
                        continue
                    if event.stream == "stderr":
                        continue
                    usage = _usage_from_codex_jsonl(event.text, usage)
                    for text in _extract_codex_text(event.text):
                        emitted = True
                        for chunk in _chunk_text(text, self.replay_chunk_size):
                            yield TextDelta(chunk)
                            if self.replay_delay > 0:
                                await asyncio.sleep(self.replay_delay)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            yield ProviderError(f"Codex provider timed out after {self.timeout} seconds.")
            return
        except Exception as exc:
            self._logger.warning("codex_provider_failed", error=str(exc))
            yield ProviderError(f"Codex provider request failed: {exc}")
            return

        finally:
            if image_directory is not None:
                await asyncio.to_thread(image_directory.cleanup)

        if result is None:
            yield ProviderError("Codex provider ended without an exit status.")
            return

        if result.exit_code != 0:
            yield ProviderError(f"Codex provider exited with {result.exit_code}:\n{result.output}")
            return

        if not emitted and result.stdout.strip():
            for text in _extract_codex_text(result.stdout):
                for chunk in _chunk_text(text, self.replay_chunk_size):
                    yield TextDelta(chunk)
                    if self.replay_delay > 0:
                        await asyncio.sleep(self.replay_delay)

        yield Done(usage=usage, stop_reason="codex_cli")


def _write_codex_images(messages: Sequence[ChatMessage], directory: Path) -> list[str]:
    args: list[str] = []
    suffixes = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    for message in messages:
        for block in message.content:
            if block.get("type") != "image":
                if has_images(block):
                    raise ValueError("Codex cannot forward images nested inside tool results.")
                continue
            try:
                data = base64.b64decode(str(block.get("data", "")), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid image attachment data.") from exc
            if not data:
                raise ValueError("Image attachment is empty.")
            path = directory / f"image-{len(args) // 2}{suffixes.get(str(block.get('media_type')), '.img')}"
            path.write_bytes(data)
            args.extend(("--image", str(path)))
    return args


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
        elif block_type == "image":
            parts.append("[Image attached to this message]")
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


def _usage_from_codex_jsonl(jsonl: str, previous: Usage | None = None) -> Usage | None:
    usage = previous
    for raw_line in jsonl.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed":
            continue
        raw_usage = event.get("usage")
        if not isinstance(raw_usage, Mapping):
            continue
        usage = Usage(
            input_tokens=_int_usage(raw_usage.get("input_tokens")),
            output_tokens=_int_usage(raw_usage.get("output_tokens")),
            cached_tokens=_int_usage(raw_usage.get("cached_input_tokens")),
            reasoning_tokens=_int_usage(raw_usage.get("reasoning_output_tokens")),
        )
    return usage


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


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            split_at = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            if split_at > start:
                end = split_at + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def _int_usage(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
