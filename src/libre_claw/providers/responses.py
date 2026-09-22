# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Responses API transport without requiring a newer OpenAI Python SDK."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

from libre_claw.core.session import ChatMessage, ContentBlock
from libre_claw.providers.base import (
    Done, LLMProvider, ProviderConfigurationError, ProviderError, ReasoningDelta,
    StreamEvent, TextDelta, ToolCallDelta, ToolCallReady, ToolCallStart, ToolSchema, Usage,
)
from libre_claw.providers.capabilities import configured_reasoning_effort, prepare_request


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
REQUEST_TIMEOUT = httpx.Timeout(300.0, connect=15.0, write=60.0, pool=15.0)


class _ProtocolError(ValueError):
    pass


class ResponsesProvider(LLMProvider):
    """Stateless Responses requests with replayable, provider-scoped output."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        default_headers: Mapping[str, str] | None = None,
        provider_scope: str = "responses",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        try:
            parsed = urlsplit(self.base_url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            parsed.port
        except ValueError as exc:
            raise ProviderConfigurationError("Responses base_url must be a valid HTTP or HTTPS URL.") from exc
        if not valid or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ProviderConfigurationError("Responses base_url must be an HTTP or HTTPS base URL without credentials or query parameters.")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ProviderConfigurationError("Responses max_tokens must be a positive integer.")
        self.default_headers = dict(default_headers or {})
        self.provider_scope = provider_scope
        self._reasoning_provider = f"{provider_scope}:responses"
        self._reasoning_scope = hashlib.sha256(
            json.dumps([provider_scope, self.base_url, model], separators=(",", ":")).encode()
        ).hexdigest()
        self._client = client

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            tools, limit = prepare_request(self, messages, tools, max_tokens)
            request: dict[str, Any] = {
                "model": self.model,
                "input": self._format_messages(messages),
                "max_output_tokens": limit or self.max_tokens,
                "stream": stream,
                "store": False,
                "include": ["reasoning.encrypted_content"],
            }
            if system:
                request["instructions"] = str(system)
            if tools:
                request["tools"] = [_format_tool(tool) for tool in tools]
            effort = configured_reasoning_effort(self)
            if effort:
                request["reasoning"] = {"effort": effort}
            # Unknown support is deliberately omitted: reasoning endpoints can
            # reject temperature even though Chat Completions accepts it.
            if getattr(self.model_info, "supports_temperature", None) is True:
                request["temperature"] = temperature
        except (ProviderConfigurationError, ValueError, TypeError) as exc:
            yield ProviderError(str(exc))
            return

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.default_headers}
        headers["Accept"] = "text/event-stream" if stream else "application/json"
        try:
            async with asyncio.timeout(900), self._client_context() as client:
                async with client.stream(
                    "POST", self.base_url + "/responses", headers=headers, json=request,
                    timeout=REQUEST_TIMEOUT, follow_redirects=False,
                ) as response:
                    if response.is_error:
                        body = await _read_bounded(response, 1024 * 1024)
                        raise _ProtocolError(_http_error(response.status_code, body))
                    if stream:
                        async for event in self._stream_response(response):
                            yield event
                    else:
                        body = await _read_bounded(response, MAX_RESPONSE_BYTES)
                        payload = json.loads(body)
                        for event in self._finish_response(payload, {}, set()):
                            yield event
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            yield ProviderError("Responses request timed out. No unfinished tool calls were executed.")
        except Exception as exc:
            yield ProviderError(f"Responses request failed: {str(exc).replace(self.api_key, '[redacted]') if self.api_key else exc}")

    @asynccontextmanager
    async def _client_context(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient() as client:
                yield client

    async def _stream_response(self, response: httpx.Response) -> AsyncIterator[StreamEvent]:
        text_parts: dict[tuple[int, int], str] = {}
        calls: dict[int, dict[str, Any]] = {}
        async for event in _sse_events(response):
            event_type = event.get("type")
            if event_type in {"response.output_text.delta", "response.refusal.delta"}:
                key = (_index(event, "output_index"), _index(event, "content_index"))
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise _ProtocolError("Invalid text delta in Responses stream.")
                text_parts[key] = text_parts.get(key, "") + delta
                if delta:
                    yield TextDelta(delta)
            elif event_type == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    index = _index(event, "output_index")
                    call_id, name = _call_identity(item)
                    if index in calls:
                        raise _ProtocolError("Repeated function-call index in Responses stream.")
                    calls[index] = dict(item)
                    yield ToolCallStart(call_id, name)
            elif event_type == "response.function_call_arguments.delta":
                index = _index(event, "output_index")
                call = calls.get(index)
                delta = event.get("delta")
                if call is None or not isinstance(delta, str):
                    raise _ProtocolError("Function arguments arrived without a valid function call.")
                if event.get("item_id") and call.get("id") != event["item_id"]:
                    raise _ProtocolError("Function argument delta refers to a different item.")
                yield ToolCallDelta(call["call_id"], call["name"], delta)
            elif event_type == "response.completed":
                for normalized in self._finish_response(
                    event.get("response"), text_parts, {item["call_id"] for item in calls.values()},
                ):
                    yield normalized
                return
            elif event_type in {"response.failed", "response.incomplete"}:
                raise _ProtocolError(_response_error(event.get("response"), event_type))
            elif event_type == "error":
                raise _ProtocolError(_response_error(event, "stream error"))
        raise _ProtocolError("Responses stream ended without a completed response. No unfinished tool calls were executed.")

    def _finish_response(
        self,
        payload: Any,
        streamed_text: dict[tuple[int, int], str],
        started_calls: set[str],
    ) -> list[StreamEvent]:
        if not isinstance(payload, dict) or payload.get("status") != "completed" or payload.get("error"):
            raise _ProtocolError(_response_error(payload, "unfinished response"))
        output = payload.get("output")
        if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
            raise _ProtocolError("Responses completion did not contain valid output items.")
        events: list[StreamEvent] = []
        tool_calls: list[ToolCallReady] = []
        seen_calls: set[str] = set()
        seen_text: set[tuple[int, int]] = set()
        for index, item in enumerate(output):
            if item.get("status") in {"in_progress", "incomplete", "failed"}:
                raise _ProtocolError("Responses completion contains an unfinished output item.")
            if item.get("type") == "function_call":
                call_id, name = _call_identity(item)
                arguments = item.get("arguments")
                if not isinstance(arguments, str):
                    raise _ProtocolError("Responses function arguments must be a JSON object string.")
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict) or call_id in seen_calls:
                    raise _ProtocolError("Responses function arguments or call IDs are invalid.")
                seen_calls.add(call_id)
                tool_calls.append(ToolCallReady(call_id, name, parsed))
            elif item.get("type") == "message":
                content = item.get("content")
                if not isinstance(content, list):
                    raise _ProtocolError("Responses message content must be a list.")
                for content_index, part in enumerate(content):
                    if not isinstance(part, dict) or part.get("type") not in {"output_text", "refusal"}:
                        continue
                    text = part.get("text" if part["type"] == "output_text" else "refusal")
                    key = (index, content_index)
                    seen_text.add(key)
                    emitted = streamed_text.get(key, "")
                    if not isinstance(text, str) or not text.startswith(emitted):
                        raise _ProtocolError("Responses completion does not match the streamed text.")
                    if remainder := text[len(emitted):]:
                        events.append(TextDelta(remainder))
        if not started_calls <= seen_calls or not streamed_text.keys() <= seen_text:
            raise _ProtocolError("Responses completion is missing streamed output items.")
        # Validate every call before exposing any executable ToolCallReady. Keep
        # original item order, IDs, phases, and opaque reasoning for the next turn.
        if output:
            envelope = {"type": "responses_output_items", "version": 1, "scope": self._reasoning_scope, "output": output}
            events.append(ReasoningDelta(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")), self._reasoning_provider))
        events.extend(tool_calls)
        events.append(Done(usage=_usage_from(payload.get("usage")), stop_reason="tool_calls" if tool_calls else "stop"))
        return events

    def _format_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "assistant" and (preserved := self._preserved_output(message.content)) is not None:
                result.extend(preserved)
                continue
            content: list[dict[str, Any]] = []

            def flush() -> None:
                if content:
                    result.append({"role": message.role, "content": list(content)})
                    content.clear()

            for block in message.content:
                kind = block.get("type")
                if kind == "provider_reasoning":
                    continue
                if kind == "text":
                    content.append({"type": "input_text", "text": str(block.get("text", ""))})
                elif kind == "image":
                    if message.role != "user":
                        raise ProviderConfigurationError("Responses image input must belong to a user message.")
                    content.append(_image_input(block))
                elif kind == "tool_use":
                    if message.role != "assistant":
                        raise ProviderConfigurationError("Responses tool calls must belong to an assistant message.")
                    flush()
                    result.append({
                        "type": "function_call", "call_id": _required_text(block, "id"),
                        "name": _required_text(block, "name"),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False, sort_keys=True),
                    })
                elif kind == "tool_result":
                    flush()
                    result.append({
                        "type": "function_call_output", "call_id": _required_text(block, "tool_use_id"),
                        "output": _tool_output(block),
                    })
                else:
                    raise ProviderConfigurationError(f"Responses does not support the input block type {kind!r}.")
            flush()
        return result

    def _preserved_output(self, blocks: Sequence[ContentBlock]) -> list[dict[str, Any]] | None:
        for block in blocks:
            if block.get("type") != "provider_reasoning" or block.get("provider") != self._reasoning_provider:
                continue
            try:
                envelope = json.loads(str(block.get("text", "")))
            except (TypeError, ValueError):
                continue
            if not isinstance(envelope, dict) or envelope.get("type") != "responses_output_items":
                continue
            output = envelope.get("output")
            if envelope.get("version") == 1 and envelope.get("scope") == self._reasoning_scope and isinstance(output, list) and all(isinstance(item, dict) for item in output):
                return copy.deepcopy(output)
        return None


def _required_text(value: Mapping[str, Any], key: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text:
        raise _ProtocolError(f"Responses requires a non-empty {key}.")
    return text


def _call_identity(item: Mapping[str, Any]) -> tuple[str, str]:
    return _required_text(item, "call_id"), _required_text(item, "name")


def _index(event: Mapping[str, Any], field: str) -> int:
    value = event.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _ProtocolError(f"Responses stream contains an invalid {field}.")
    return value


def _format_tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function", "name": _required_text(tool, "name"),
        "description": str(tool.get("description", "")),
        "parameters": copy.deepcopy(tool.get("input_schema", {"type": "object", "properties": {}})),
        # Preserve optional arguments and schemas used by existing local tools.
        "strict": False,
    }


def _image_input(block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "input_image", "image_url": f"data:{block.get('media_type', 'image/png')};base64,{block.get('data', '')}"}


def _tool_output(block: Mapping[str, Any]) -> Any:
    value = block.get("content", "")
    if isinstance(value, list):
        content = []
        for part in value:
            if not isinstance(part, dict):
                raise ProviderConfigurationError("Responses tool output content must contain valid blocks.")
            if part.get("type") == "text":
                content.append({"type": "input_text", "text": str(part.get("text", ""))})
            elif part.get("type") == "image":
                content.append(_image_input(part))
            else:
                raise ProviderConfigurationError("Responses tool output supports text and image blocks.")
        return content
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"Tool error: {text}" if block.get("is_error") else text


def _usage_from(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    input_details = input_details if isinstance(input_details, Mapping) else {}
    output_details = output_details if isinstance(output_details, Mapping) else {}
    cost = raw.get("cost")
    return Usage(
        input_tokens=_token_value(raw.get("input_tokens")), output_tokens=_token_value(raw.get("output_tokens")),
        cached_tokens=_token_value(input_details.get("cached_tokens")),
        reasoning_tokens=_token_value(output_details.get("reasoning_tokens")),
        cache_write_tokens=_token_value(input_details.get("cache_write_tokens")),
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) else None,
    )


def _token_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _response_error(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return f"Responses returned an {fallback}."
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:2000]
    if payload.get("message"):
        return str(payload["message"])[:2000]
    details = payload.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    return f"Responses returned {payload.get('status', fallback)}{': ' + str(reason) if reason else ''}. No unfinished tool calls were executed."


def _http_error(status: int, body: bytes) -> str:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        payload = None
    return f"HTTP {status}: {_response_error(payload, 'API error')}"


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise _ProtocolError("Responses body exceeded the supported size limit.")
        body.extend(chunk)
    return bytes(body)


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    buffer = bytearray()
    data: list[bytes] = []
    event_name = ""
    event_size = total_size = 0
    skip_lf = False

    def dispatch() -> dict[str, Any] | None:
        nonlocal event_name, event_size
        raw = b"\n".join(data)
        data.clear()
        name, event_name = event_name, ""
        event_size = 0
        if not raw or raw == b"[DONE]":
            return None
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise _ProtocolError("Responses stream contains invalid event JSON.") from exc
        if not isinstance(event, dict):
            raise _ProtocolError("Responses stream event must be a JSON object.")
        if "type" not in event and name:
            event["type"] = name
        return event

    async for chunk in response.aiter_bytes():
        total_size += len(chunk)
        if total_size > MAX_RESPONSE_BYTES:
            raise _ProtocolError("Responses stream exceeded the supported size limit.")
        buffer.extend(chunk)
        while buffer:
            if skip_lf:
                if buffer[0] == 10:
                    del buffer[:1]
                skip_lf = False
            newlines = [index for delimiter in (b"\r", b"\n") if (index := buffer.find(delimiter)) >= 0]
            if not newlines:
                break
            index = min(newlines)
            line = bytes(buffer[:index])
            skip_lf = buffer[index] == 13
            del buffer[:index + 1]
            if not line:
                if event := dispatch():
                    yield event
                continue
            event_size += len(line)
            if event_size > MAX_EVENT_BYTES:
                raise _ProtocolError("Responses stream event exceeded the supported size limit.")
            field, _, value = line.partition(b":")
            if value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                data.append(value)
            elif field == b"event":
                event_name = value.decode("utf-8")
        if len(buffer) + event_size > MAX_EVENT_BYTES:
            raise _ProtocolError("Responses stream event exceeded the supported size limit.")
    # A final SSE event still requires its blank-line delimiter. A truncated
    # event or a bare [DONE] cannot authorize executing pending function calls.
