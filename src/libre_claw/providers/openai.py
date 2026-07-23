# Copyright 2026 Kroonen AI (https://kroonen.ai)
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
    ReasoningDelta,
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
        default_headers: Mapping[str, str] | None = None,
        display_name: str = "OpenAI",
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.default_headers = dict(default_headers or {})
        self.display_name = display_name
        if client is not None:
            self._client = client
        elif AsyncOpenAI is None:
            msg = "The openai package is not installed."
            raise RuntimeError(msg)
        else:
            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            if self.default_headers:
                kwargs["default_headers"] = self.default_headers
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
            self._max_tokens_field(): max_tokens or self.max_tokens,
        }
        stream_options = self._stream_options()
        if stream_options is not None:
            request["stream_options"] = stream_options
        extra_body = self._extra_body()
        if extra_body:
            request["extra_body"] = extra_body
        request.update(self._extra_request_parameters())
        if tools:
            request["tools"] = [self._format_tool_schema(tool) for tool in tools]
            request["tool_choice"] = "auto"
        if self._supports_temperature():
            request["temperature"] = temperature

        accumulators: dict[int, _OpenAIToolAccumulator] = {}
        usage: Usage | None = None
        stop_reason: str | None = None
        finalized_tools = False

        try:
            stream_response = await self._client.chat.completions.create(**request)
            async for chunk in stream_response:
                usage = _usage_from(_object_field(chunk, "usage"), usage)
                choices = _object_field(chunk, "choices") or []
                for choice in choices:
                    usage = _usage_from(_object_field(choice, "usage"), usage)
                    delta = _object_field(choice, "delta")
                    if delta is not None:
                        reasoning = self._reasoning_delta(delta)
                        if reasoning is not None:
                            yield reasoning

                        content = _object_field(delta, "content")
                        if content:
                            yield TextDelta(str(content))

                        for normalized in self._handle_tool_call_deltas(delta, accumulators):
                            yield normalized

                    finish_reason = _object_field(choice, "finish_reason")
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
            error = str(exc) or repr(exc)
            self._logger.warning("openai_stream_failed", error=error, error_type=exc.__class__.__name__)
            yield ProviderError(f"{self.display_name} request failed: {error}")
            return

        yield Done(usage=usage, stop_reason=stop_reason)

    def _format_messages(self, messages: Sequence[ChatMessage], system: str | None) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})

        for message in messages:
            if message.role == "assistant":
                formatted.append(self._format_assistant_message(message.content))
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

    def _extra_body(self) -> dict[str, Any]:
        return {}

    def _extra_request_parameters(self) -> dict[str, Any]:
        return {}

    def _format_assistant_message(self, blocks: Sequence[ContentBlock]) -> dict[str, Any]:
        return _format_assistant_message(blocks)

    def _max_tokens_field(self) -> str:
        return "max_completion_tokens"

    def _reasoning_delta(self, delta: Any) -> ReasoningDelta | None:
        del delta
        return None

    def _stream_options(self) -> dict[str, Any] | None:
        return {"include_usage": True}

    def _supports_temperature(self) -> bool:
        return _supports_temperature(self.model)

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


def _format_assistant_message(
    blocks: Sequence[ContentBlock],
    *,
    reasoning_provider: str | None = None,
) -> dict[str, Any]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        if block_type == "provider_reasoning" and block.get("provider") == reasoning_provider:
            reasoning_parts.append(str(block.get("text", "")))
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
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _format_user_or_tool_messages(blocks: Sequence[ContentBlock]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    image_parts: list[ContentBlock] = []
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
        elif block_type == "image":
            image_parts.append(block)

    text = "\n".join(part for part in text_parts if part)
    if image_parts:
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_data_url(block),
                },
            }
            for block in image_parts
        )
        messages.append({"role": "user", "content": content})
    elif text:
        messages.append({"role": "user", "content": text})
    return messages


def _image_data_url(block: ContentBlock) -> str:
    media_type = str(block.get("media_type", "image/jpeg"))
    data = str(block.get("data", ""))
    return f"data:{media_type};base64,{data}"


def _usage_from(raw_usage: Any, previous: Usage | None) -> Usage | None:
    if raw_usage is None:
        return previous

    input_tokens = _get_usage_value(raw_usage, "prompt_tokens")
    output_tokens = _get_usage_value(raw_usage, "completion_tokens")
    if input_tokens is None:
        input_tokens = _get_usage_value(raw_usage, "input_tokens")
    if output_tokens is None:
        output_tokens = _get_usage_value(raw_usage, "output_tokens")

    prompt_details = _get_usage_value(raw_usage, "prompt_tokens_details")
    completion_details = _get_usage_value(raw_usage, "completion_tokens_details")

    return Usage(
        input_tokens=_token_value(input_tokens, previous.input_tokens if previous else 0),
        output_tokens=_token_value(output_tokens, previous.output_tokens if previous else 0),
        cached_tokens=_token_value(
            _get_usage_value(prompt_details, "cached_tokens"),
            previous.cached_tokens if previous else 0,
        ),
        reasoning_tokens=_token_value(
            _get_usage_value(completion_details, "reasoning_tokens"),
            previous.reasoning_tokens if previous else 0,
        ),
        cost=_cost_value(_get_usage_value(raw_usage, "cost"), previous.cost if previous else None),
    )


def _get_usage_value(raw_usage: Any, key: str) -> Any:
    if raw_usage is None:
        return None
    if isinstance(raw_usage, Mapping):
        return raw_usage.get(key)
    return getattr(raw_usage, key, None)


def _object_field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _token_value(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    return fallback


def _cost_value(value: Any, fallback: float | None) -> float | None:
    if isinstance(value, int | float):
        return float(value)
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
