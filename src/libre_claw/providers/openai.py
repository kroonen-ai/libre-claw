# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from libre_claw.core.session import ChatMessage, ContentBlock
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    ProviderError,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallReady,
    ToolCallStart,
    ToolSchema,
    Usage,
)

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised only when dependencies are absent.
    AsyncOpenAI = None  # type: ignore[assignment]


@dataclass
class _OpenAIToolAccumulator:
    index: int
    tool_call_id: str = ""
    name: str = ""
    argument_chunks: list[str] = field(default_factory=list)
    started: bool = False

    def append_arguments(self, chunk: str) -> None:
        self.argument_chunks.append(chunk)

    def parse_arguments(self) -> dict[str, Any]:
        raw = "".join(self.argument_chunks)
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            msg = f"Tool input for {self.name} must be a JSON object"
            raise ValueError(msg)
        return parsed


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions provider with async streaming."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url
        if client is not None:
            self._client = client
        elif AsyncOpenAI is None:
            msg = "The openai package is not installed."
            raise RuntimeError(msg)
        else:
            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = AsyncOpenAI(**kwargs)
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
        del stream
        request: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            request["tools"] = [self._format_tool_schema(tool) for tool in tools]
            request["tool_choice"] = "auto"
        if _supports_temperature(self.model):
            request["temperature"] = temperature

        accumulators: dict[int, _OpenAIToolAccumulator] = {}
        usage: Usage | None = None
        stop_reason: str | None = None
        finalized_tools = False

        try:
            stream_response = await self._client.chat.completions.create(**request)
            async for chunk in stream_response:
                usage = _usage_from(getattr(chunk, "usage", None), usage)
                choices = getattr(chunk, "choices", None) or []
                for choice in choices:
                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        content = getattr(delta, "content", None)
                        if content:
                            yield TextDelta(str(content))

                        for normalized in self._handle_tool_call_deltas(delta, accumulators):
                            yield normalized

                    finish_reason = getattr(choice, "finish_reason", None)
                    if finish_reason:
                        stop_reason = str(finish_reason)
                    if finish_reason == "tool_calls" and not finalized_tools:
                        for normalized in self._finalize_tool_calls(accumulators):
                            yield normalized
                        finalized_tools = True

            if accumulators and not finalized_tools:
                for normalized in self._finalize_tool_calls(accumulators):
                    yield normalized
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning("openai_stream_failed", error=str(exc))
            yield ProviderError(f"OpenAI request failed: {exc}")
            return

        yield Done(usage=usage, stop_reason=stop_reason)

    def _format_messages(self, messages: Sequence[ChatMessage], system: str | None) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})

        for message in messages:
            if message.role == "assistant":
                formatted.append(_format_assistant_message(message.content))
                continue

            formatted.extend(_format_user_or_tool_messages(message.content))

        return formatted

    def _format_tool_schema(self, schema: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("input_schema", {"type": "object", "properties": {}}),
            },
        }

    def _handle_tool_call_deltas(
        self,
        delta: Any,
        accumulators: dict[int, _OpenAIToolAccumulator],
    ) -> list[StreamEvent]:
        normalized: list[StreamEvent] = []
        for tool_call_delta in getattr(delta, "tool_calls", None) or []:
            index = getattr(tool_call_delta, "index", None)
            if not isinstance(index, int):
                continue

            accumulator = accumulators.setdefault(index, _OpenAIToolAccumulator(index=index))
            tool_call_id = getattr(tool_call_delta, "id", None)
            if tool_call_id:
                accumulator.tool_call_id = str(tool_call_id)

            function_delta = getattr(tool_call_delta, "function", None)
            name = getattr(function_delta, "name", None)
            if name:
                accumulator.name = str(name)

            if accumulator.tool_call_id and accumulator.name and not accumulator.started:
                accumulator.started = True
                normalized.append(ToolCallStart(tool_call_id=accumulator.tool_call_id, name=accumulator.name))

            arguments = getattr(function_delta, "arguments", None)
            if arguments:
                accumulator.append_arguments(str(arguments))
                normalized.append(
                    ToolCallDelta(
                        tool_call_id=accumulator.tool_call_id,
                        name=accumulator.name,
                        partial_json=str(arguments),
                    )
                )

        return normalized

    def _finalize_tool_calls(self, accumulators: dict[int, _OpenAIToolAccumulator]) -> list[StreamEvent]:
        normalized: list[StreamEvent] = []
        for index in sorted(accumulators):
            accumulator = accumulators[index]
            try:
                arguments = accumulator.parse_arguments()
            except json.JSONDecodeError as exc:
                normalized.append(ProviderError(f"Could not parse tool input for {accumulator.name}: {exc}"))
                continue
            except ValueError as exc:
                normalized.append(ProviderError(str(exc)))
                continue

            normalized.append(
                ToolCallReady(
                    tool_call_id=accumulator.tool_call_id,
                    name=accumulator.name,
                    input=arguments,
                )
            )
        accumulators.clear()
        return normalized


def _format_assistant_message(blocks: Sequence[ContentBlock]) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        if block_type == "tool_use":
            tool_calls.append(
                {
                    "id": str(block.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name", "")),
                        "arguments": json.dumps(block.get("input", {}), sort_keys=True),
                    },
                }
            )

    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(part for part in text_parts if part) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _format_user_or_tool_messages(blocks: Sequence[ContentBlock]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id", "")),
                    "content": str(block.get("content", "")),
                }
            )
        elif block_type == "text":
            text_parts.append(str(block.get("text", "")))

    if text_parts:
        messages.append({"role": "user", "content": "\n".join(part for part in text_parts if part)})
    return messages


def _usage_from(raw_usage: Any, previous: Usage | None) -> Usage | None:
    if raw_usage is None:
        return previous

    input_tokens = getattr(raw_usage, "prompt_tokens", None)
    output_tokens = getattr(raw_usage, "completion_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(raw_usage, "input_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(raw_usage, "output_tokens", None)

    return Usage(
        input_tokens=_token_value(input_tokens, previous.input_tokens if previous else 0),
        output_tokens=_token_value(output_tokens, previous.output_tokens if previous else 0),
    )


def _token_value(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    return fallback


def _supports_temperature(model: str) -> bool:
    normalized = model.lower()
    return not (
        normalized.startswith("o1")
        or normalized.startswith("o3")
        or normalized.startswith("o4")
        or normalized.startswith("o5")
        or "codex" in normalized
    )
