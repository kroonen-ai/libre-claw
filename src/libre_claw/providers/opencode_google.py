# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Gemini GenerateContent transport for OpenCode's native Google route."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from libre_claw.core.session import ChatMessage
from libre_claw.providers.base import (
    Done, LLMProvider, ProviderConfigurationError, ProviderError, ReasoningDelta,
    StreamEvent, TextDelta, ToolCallReady, ToolCallStart, ToolSchema, Usage,
)
from libre_claw.providers.capabilities import configured_reasoning_effort, prepare_request


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
REQUEST_TIMEOUT = httpx.Timeout(300, connect=15, write=60, pool=15)


class _ProtocolError(ValueError):
    pass


def _reject_constant(value: str) -> Any:
    raise _ProtocolError("Gemini returned a non-finite JSON number.")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise _ProtocolError("Gemini returned a non-finite JSON number.")
    return result


def _json(raw: str | bytes) -> Any:
    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise _ProtocolError(f"Gemini requires a non-empty {key}.")
    return result


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _inline_image(block: Mapping[str, Any]) -> dict[str, Any]:
    media = _required_text(block, "media_type")
    if not media.startswith("image/"):
        raise ProviderConfigurationError("Gemini image blocks require an image MIME type.")
    return {"inlineData": {"mimeType": media, "data": _required_text(block, "data")}}


def _call(part: Mapping[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    call = part.get("functionCall")
    if not isinstance(call, dict):
        raise _ProtocolError("Invalid Gemini function call.")
    name = _required_text(call, "name")
    arguments = call.get("args", {})
    native_id = call.get("id")
    if not isinstance(arguments, dict) or (native_id is not None and (not isinstance(native_id, str) or not native_id.strip())):
        raise _ProtocolError("Invalid Gemini function arguments or ID.")
    if "partialArgs" in call or call.get("willContinue"):
        raise _ProtocolError("Gemini returned unfinished function arguments.")
    json.dumps(arguments, allow_nan=False)
    return name, arguments, native_id


def _tool_response(block: Mapping[str, Any], name: str, native_id: str | None) -> dict[str, Any]:
    value = block.get("content", "")
    media: list[dict[str, Any]] = []
    if isinstance(value, list):
        text = []
        for part in value:
            if not isinstance(part, dict):
                raise ProviderConfigurationError("Gemini tool results must contain text or image blocks.")
            if part.get("type") == "text":
                text.append(str(part.get("text", "")))
            elif part.get("type") == "image":
                media.append(_inline_image(part))
            else:
                raise ProviderConfigurationError("Gemini tool results support text and image blocks.")
        value = "\n".join(text)
    response: dict[str, Any] = {"name": name, "response": {"error" if block.get("is_error") else "output": value}}
    if native_id is not None:
        response["id"] = native_id
    if media:
        response["parts"] = media
    return {"functionResponse": response}


class OpenCodeGoogleProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str, max_tokens: int, *, base_url: str,
        client: httpx.AsyncClient | None = None, default_headers: Mapping[str, str] | None = None,
        display_name: str = "OpenCode Zen", provider_scope: str = "opencode",
    ) -> None:
        self.api_key, self.model, self.max_tokens = api_key, model, max_tokens
        self.base_url = base_url.rstrip("/")
        try:
            parsed = urlsplit(self.base_url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            parsed.port
        except ValueError as exc:
            raise ProviderConfigurationError("Gemini base_url must be a valid HTTP or HTTPS URL.") from exc
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment or any(character.isspace() for character in self.base_url):
            raise ProviderConfigurationError("Gemini base_url must not contain credentials, query parameters, or fragments.")
        if not isinstance(model, str) or not model.strip():
            raise ProviderConfigurationError("Choose a model from the OpenCode catalog before using Gemini.")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ProviderConfigurationError("Gemini max_tokens must be a positive integer.")
        self._client = client
        self.default_headers = dict(default_headers or {})
        self.display_name, self.provider_scope = display_name, provider_scope
        self._reasoning_scope = hashlib.sha256(json.dumps([provider_scope, self.base_url, model]).encode()).hexdigest()

    def _preserved_parts(self, message: ChatMessage) -> list[dict[str, Any]] | None:
        for block in message.content:
            if block.get("type") != "provider_reasoning" or block.get("provider") != self.provider_scope:
                continue
            try:
                data = _json(block.get("text", ""))
            except (TypeError, ValueError):
                continue
            if (isinstance(data, dict) and data.get("type") == "google_message_parts"
                    and data.get("version") == 1 and data.get("scope") == self._reasoning_scope
                    and isinstance(data.get("parts"), list) and all(isinstance(part, dict) for part in data["parts"])):
                return copy.deepcopy(data["parts"])
        return None

    def _messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        call_names: dict[str, tuple[str, str | None]] = {}
        for message in messages:
            if message.role not in {"user", "assistant"}:
                raise ProviderConfigurationError("Gemini messages require a user or assistant role.")
            preserved = self._preserved_parts(message) if message.role == "assistant" else None
            native_calls = [part for part in preserved or [] if "functionCall" in part]
            call_index = 0
            parts: list[dict[str, Any]] = []
            for block in message.content:
                kind = block.get("type")
                if kind == "provider_reasoning":
                    continue
                if kind == "text":
                    parts.append({"text": str(block.get("text", ""))})
                elif kind == "image":
                    if message.role != "user":
                        raise ProviderConfigurationError("Gemini input images must belong to user messages.")
                    parts.append(_inline_image(block))
                elif kind == "tool_use":
                    if message.role != "assistant":
                        raise ProviderConfigurationError("Gemini function calls must belong to assistant messages.")
                    call_id, name = _required_text(block, "id"), _required_text(block, "name")
                    arguments = block.get("input", {})
                    if not isinstance(arguments, dict) or call_id in call_names:
                        raise ProviderConfigurationError("Gemini tool history contains invalid arguments or duplicate call IDs.")
                    native_id: str | None = call_id
                    if preserved is not None:
                        if call_index >= len(native_calls):
                            raise ProviderConfigurationError("Stored Gemini calls do not match the tool history.")
                        native_name, native_args, native_id = _call(native_calls[call_index])
                        if name != native_name or arguments != native_args:
                            raise ProviderConfigurationError("Stored Gemini calls do not match the tool history.")
                    call_index += 1
                    call_names[call_id] = (name, native_id)
                    parts.append({"functionCall": {"id": call_id, "name": name, "args": copy.deepcopy(arguments)}})
                elif kind == "tool_result":
                    if message.role != "user":
                        raise ProviderConfigurationError("Gemini function results must belong to user messages.")
                    call_id = _required_text(block, "tool_use_id")
                    if call_id not in call_names:
                        raise ProviderConfigurationError("A Gemini tool result has no matching function call in this conversation.")
                    parts.append(_tool_response(block, *call_names.pop(call_id)))
                else:
                    raise ProviderConfigurationError(f"Gemini does not support input block type {kind!r}.")
            if preserved is not None:
                if call_index != len(native_calls):
                    raise ProviderConfigurationError("Stored Gemini calls do not match the tool history.")
                parts = preserved
            if parts:
                contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        return contents

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                yield client

    @asynccontextmanager
    async def _response(self, client: httpx.AsyncClient, url: str, headers: httpx.Headers,
                        body: dict[str, Any], stream: bool) -> AsyncIterator[httpx.Response]:
        request = client.build_request("POST", url, headers=headers, json=body, timeout=REQUEST_TIMEOUT,
                                       params={"alt": "sse"} if stream else None)
        request.headers.pop("authorization", None)
        response = await client.send(request, stream=True, follow_redirects=False, auth=None)
        try:
            yield response
        finally:
            await response.aclose()

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema] | None = None,
        system: str | None = None, stream: bool = True, temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            tools, limit = prepare_request(self, messages, tools, max_tokens)
            limit = self.max_tokens if limit is None else limit
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                raise ProviderConfigurationError("Gemini max_tokens must be a positive integer.")
            config: dict[str, Any] = {"maxOutputTokens": limit, "candidateCount": 1}
            info = self.model_info
            if info is None or info.supports_temperature is not False:
                config["temperature"] = temperature
            if effort := configured_reasoning_effort(self):
                config["thinkingConfig"] = {"thinkingLevel": effort}
            body: dict[str, Any] = {"contents": self._messages(messages), "generationConfig": config}
            if system:
                body["systemInstruction"] = {"parts": [{"text": str(system)}]}
            if tools:
                body["tools"] = [{"functionDeclarations": [{
                    "name": _required_text(tool, "name"), "description": str(tool.get("description", "")),
                    "parametersJsonSchema": copy.deepcopy(tool.get("input_schema", {"type": "object", "properties": {}})),
                } for tool in tools]}]
            json.dumps(body, allow_nan=False)
            method = "streamGenerateContent" if stream else "generateContent"
            url = f"{self.base_url}/models/{quote(self.model, safe='')}:{method}"
            headers = httpx.Headers(self.default_headers)
            # The native Zen route accepts x-goog-api-key; a bearer header can
            # be forwarded incorrectly upstream by the gateway.
            headers.pop("authorization", None)
            headers["x-goog-api-key"] = self.api_key
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "text/event-stream" if stream else "application/json"
            accumulated: list[dict[str, Any]] = []
            calls: list[ToolCallReady] = []
            seen_ids: set[str] = set()
            usage = None
            finish_reason = None
            async with asyncio.timeout(900), self._http() as client:
                async with self._response(client, url, headers, body, stream) as response:
                    response.raise_for_status()
                    async for payload in _payloads(response, stream):
                        if payload.get("error"):
                            raise _ProtocolError("Gemini returned an API error.")
                        feedback = payload.get("promptFeedback")
                        if isinstance(feedback, dict) and feedback.get("blockReason"):
                            raise _ProtocolError("Gemini blocked this request.")
                        if isinstance(metadata := payload.get("usageMetadata"), dict):
                            thought_tokens = _count(metadata.get("thoughtsTokenCount"))
                            usage = Usage(input_tokens=_count(metadata.get("promptTokenCount")),
                                output_tokens=_count(metadata.get("candidatesTokenCount")) + thought_tokens,
                                cached_tokens=_count(metadata.get("cachedContentTokenCount")), reasoning_tokens=thought_tokens)
                        candidates = payload.get("candidates", [])
                        if not isinstance(candidates, list) or len(candidates) > 1:
                            raise _ProtocolError("Gemini returned an unexpected candidate count.")
                        for candidate in candidates:
                            if (not isinstance(candidate, dict) or not isinstance(candidate.get("index", 0), int)
                                    or isinstance(candidate.get("index", 0), bool)
                                    or candidate.get("index", 0) != 0):
                                raise _ProtocolError("Gemini returned an invalid candidate.")
                            content = candidate.get("content", {})
                            if not isinstance(content, dict):
                                raise _ProtocolError("Gemini returned invalid content.")
                            parts = content.get("parts", [])
                            if not isinstance(parts, list) or (parts and finish_reason is not None):
                                raise _ProtocolError("Gemini returned content after the completed response.")
                            reason = candidate.get("finishReason")
                            if reason is not None:
                                if not isinstance(reason, str) or (finish_reason and reason != finish_reason):
                                    raise _ProtocolError("Gemini returned inconsistent completion reasons.")
                                finish_reason = reason
                            for part in parts:
                                if not isinstance(part, dict):
                                    raise _ProtocolError("Gemini returned an invalid part.")
                                if "text" in part and "functionCall" in part:
                                    raise _ProtocolError("Gemini returned multiple data types in one part.")
                                if "thoughtSignature" in part and not isinstance(part["thoughtSignature"], str):
                                    raise _ProtocolError("Gemini returned an invalid thought signature.")
                                if "thought" in part and not isinstance(part["thought"], bool):
                                    raise _ProtocolError("Gemini returned invalid thought metadata.")
                                if "text" in part:
                                    if not isinstance(part["text"], str):
                                        raise _ProtocolError("Gemini returned invalid text.")
                                    if not part.get("thought") and part["text"]:
                                        yield TextDelta(part["text"])
                                elif "functionCall" in part:
                                    name, arguments, native_id = _call(part)
                                    call_id = native_id or f"google_{uuid.uuid4().hex}"
                                    if call_id in seen_ids:
                                        raise _ProtocolError("Gemini returned duplicate function call IDs.")
                                    seen_ids.add(call_id)
                                    calls.append(ToolCallReady(call_id, name, copy.deepcopy(arguments)))
                                elif not part.get("thoughtSignature"):
                                    raise _ProtocolError("Gemini returned an unsupported output part.")
                                accumulated.append(copy.deepcopy(part))
            if finish_reason not in {"STOP", "MAX_TOKENS"} or not accumulated:
                raise _ProtocolError("Gemini did not finish a valid response.")
            if calls and finish_reason != "STOP":
                raise _ProtocolError("Gemini stopped before completing its tool calls.")
            # Preserve original part boundaries, order, native IDs, and opaque
            # signatures. All calls are validated before any becomes executable.
            yield ReasoningDelta(json.dumps({"type": "google_message_parts", "version": 1,
                "scope": self._reasoning_scope, "parts": accumulated}, ensure_ascii=False, separators=(",", ":"), allow_nan=False), self.provider_scope)
            for call in calls:
                yield ToolCallStart(call.tool_call_id, call.name)
                yield call
            yield Done(usage, "tool_calls" if calls else "length" if finish_reason == "MAX_TOKENS" else "stop")
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            yield ProviderError(f"{self.display_name} request failed (HTTP {exc.response.status_code}). Check your API key, model access, and plan limits.")
        except (TimeoutError, httpx.TimeoutException):
            yield ProviderError(f"{self.display_name} Gemini request timed out. No unfinished tool calls were executed.")
        except (ProviderConfigurationError, _ProtocolError) as exc:
            message = str(exc).replace(self.api_key, "[redacted]") if self.api_key else str(exc)
            yield ProviderError(message)
        except Exception:
            yield ProviderError(f"{self.display_name} returned an invalid or interrupted Gemini response.")


async def _payloads(response: httpx.Response, stream: bool) -> AsyncIterator[dict[str, Any]]:
    if not stream:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise _ProtocolError("Gemini response exceeded the supported size limit.")
            body.extend(chunk)
        payload = _json(bytes(body))
        if not isinstance(payload, dict):
            raise _ProtocolError("Gemini response must be a JSON object.")
        yield payload
        return
    buffer = bytearray()
    data: list[bytes] = []
    event_name = b""
    size = total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise _ProtocolError("Gemini stream exceeded the supported size limit.")
        buffer.extend(chunk)
        while (index := buffer.find(b"\n")) >= 0:
            line = bytes(buffer[:index]).rstrip(b"\r")
            del buffer[:index + 1]
            if not line:
                raw = b"\n".join(data)
                name, event_name = event_name, b""
                data, size = [], 0
                if name == b"error":
                    raise _ProtocolError("Gemini returned a streaming API error.")
                if raw and raw != b"[DONE]":
                    payload = _json(raw)
                    if not isinstance(payload, dict):
                        raise _ProtocolError("Gemini stream event must be a JSON object.")
                    yield payload
                continue
            size += len(line)
            if size > MAX_EVENT_BYTES:
                raise _ProtocolError("Gemini stream event exceeded the supported size limit.")
            field, _, value = line.partition(b":")
            if field == b"data":
                data.append(value[1:] if value.startswith(b" ") else value)
            elif field == b"event":
                event_name = value.strip()
        if len(buffer) + size > MAX_EVENT_BYTES:
            raise _ProtocolError("Gemini stream event exceeded the supported size limit.")
    if data or buffer:
        raise _ProtocolError("Gemini stream ended with an incomplete event.")
