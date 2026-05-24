# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from libre_claw.core.session import ChatMessage
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
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover - exercised only when dependencies are absent.
    AsyncAnthropic = None  # type: ignore[assignment]


@dataclass
class _ToolAccumulator:
    tool_call_id: str
    name: str
    initial_input: dict[str, Any] = field(default_factory=dict)
    partial_json: list[str] = field(default_factory=list)

    def parse_input(self) -> dict[str, Any]:
        raw = "".join(self.partial_json)
        if not raw:
            return self.initial_input
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            msg = f"Tool input for {self.name} must be a JSON object"
            raise ValueError(msg)
        return parsed


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider with async streaming."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        if client is not None:
            self._client = client
        elif AsyncAnthropic is None:
            msg = "The anthropic package is not installed."
            raise RuntimeError(msg)
        else:
            self._client = AsyncAnthropic(api_key=api_key)
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
            "messages": self._format_messages(messages),
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = list(tools)

        usage: Usage | None = None
        stop_reason: str | None = None
        tool_accumulators: dict[int, _ToolAccumulator] = {}

        try:
            async with self._client.messages.stream(**request) as response_stream:
                async for event in response_stream:
                    event_type = getattr(event, "type", None)

                    if event_type == "message_start":
                        usage = _usage_from(getattr(getattr(event, "message", None), "usage", None), usage)
                        continue

                    if event_type == "content_block_start":
                        yielded = self._handle_content_block_start(event, tool_accumulators)
                        if yielded is not None:
                            yield yielded
                        continue

                    if event_type == "content_block_delta":
                        async for normalized in self._handle_content_block_delta(event, tool_accumulators):
                            yield normalized
                        continue

                    if event_type == "content_block_stop":
                        yielded = self._handle_content_block_stop(event, tool_accumulators)
                        if yielded is not None:
                            yield yielded
                        continue

                    if event_type == "message_delta":
                        usage = _usage_from(getattr(event, "usage", None), usage)
                        delta = getattr(event, "delta", None)
                        stop_reason = getattr(delta, "stop_reason", stop_reason)
                        continue

                    if event_type == "message_stop":
                        message = getattr(event, "message", None)
                        usage = _usage_from(getattr(message, "usage", None), usage)
                        stop_reason = getattr(message, "stop_reason", stop_reason)
                        continue

                    if event_type == "ping":
                        continue

                    if event_type == "error":
                        error = getattr(event, "error", None)
                        message = getattr(error, "message", "Anthropic stream returned an error.")
                        yield ProviderError(str(message))
                        return

                    self._logger.debug("anthropic_unknown_stream_event", event_type=event_type)

                final_message = await _maybe_final_message(response_stream)
                if final_message is not None:
                    usage = _usage_from(getattr(final_message, "usage", None), usage)
                    stop_reason = getattr(final_message, "stop_reason", stop_reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning("anthropic_stream_failed", error=str(exc))
            yield ProviderError(f"Anthropic request failed: {exc}")
            return

        yield Done(usage=usage, stop_reason=stop_reason)

    def _format_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        return [message.as_provider_dict() for message in messages]

    def _handle_content_block_start(
        self,
        event: Any,
        tool_accumulators: dict[int, _ToolAccumulator],
    ) -> ToolCallStart | None:
        content_block = getattr(event, "content_block", None)
        if getattr(content_block, "type", None) != "tool_use":
            return None

        index = getattr(event, "index", None)
        if not isinstance(index, int):
            return None

        tool_call_id = str(getattr(content_block, "id", ""))
        name = str(getattr(content_block, "name", ""))
        initial_input = getattr(content_block, "input", None)
        if not isinstance(initial_input, dict):
            initial_input = {}

        tool_accumulators[index] = _ToolAccumulator(
            tool_call_id=tool_call_id,
            name=name,
            initial_input=initial_input,
        )
        return ToolCallStart(tool_call_id=tool_call_id, name=name)

    async def _handle_content_block_delta(
        self,
        event: Any,
        tool_accumulators: dict[int, _ToolAccumulator],
    ) -> AsyncIterator[StreamEvent]:
        delta = getattr(event, "delta", None)
        delta_type = getattr(delta, "type", None)

        if delta_type == "text_delta":
            text = getattr(delta, "text", "")
            if text:
                yield TextDelta(text)
            return

        if delta_type == "input_json_delta":
            index = getattr(event, "index", None)
            accumulator = tool_accumulators.get(index)
            if accumulator is None:
                return
            partial_json = getattr(delta, "partial_json", "")
            accumulator.partial_json.append(partial_json)
            yield ToolCallDelta(
                tool_call_id=accumulator.tool_call_id,
                name=accumulator.name,
                partial_json=partial_json,
            )

    def _handle_content_block_stop(
        self,
        event: Any,
        tool_accumulators: dict[int, _ToolAccumulator],
    ) -> ToolCallReady | ProviderError | None:
        index = getattr(event, "index", None)
        accumulator = tool_accumulators.pop(index, None)
        if accumulator is None:
            return None

        try:
            input_data = accumulator.parse_input()
        except json.JSONDecodeError as exc:
            return ProviderError(f"Could not parse tool input for {accumulator.name}: {exc}")
        except ValueError as exc:
            return ProviderError(str(exc))

        return ToolCallReady(
            tool_call_id=accumulator.tool_call_id,
            name=accumulator.name,
            input=input_data,
        )


def _usage_from(raw_usage: Any, previous: Usage | None) -> Usage | None:
    if raw_usage is None:
        return previous

    input_tokens = getattr(raw_usage, "input_tokens", None)
    output_tokens = getattr(raw_usage, "output_tokens", None)

    return Usage(
        input_tokens=_token_value(input_tokens, previous.input_tokens if previous else 0),
        output_tokens=_token_value(output_tokens, previous.output_tokens if previous else 0),
    )


def _token_value(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    return fallback


async def _maybe_final_message(response_stream: Any) -> Any | None:
    get_final_message = getattr(response_stream, "get_final_message", None)
    if get_final_message is None:
        return None
    return await get_final_message()
