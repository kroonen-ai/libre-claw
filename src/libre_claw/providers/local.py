# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
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
from libre_claw.providers.openai import OpenAIProvider


LocalApiFormat = Literal["ollama", "openai"]
LocalToolMode = Literal["auto", "native", "xml"]

XML_TOOL_RE = re.compile(r"<tool_call(?:\s+name=\"(?P<name>[A-Za-z0-9_.-]+)\")?\s*>(?P<body>.*?)</tool_call>", re.DOTALL)


@dataclass(frozen=True)
class ParsedXMLToolCall:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


class LocalProvider(LLMProvider):
    """Local inference provider for Ollama and OpenAI-compatible servers."""

    def __init__(
        self,
        base_url: str,
        model: str,
        max_tokens: int,
        api_format: LocalApiFormat = "ollama",
        api_key: str = "ollama",
        supports_tools: bool = True,
        tool_mode: LocalToolMode = "auto",
        client: Any | None = None,
        openai_provider: LLMProvider | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.api_format = api_format
        self.api_key = api_key
        self.supports_tools = supports_tools
        self.tool_mode = tool_mode
        self._client = client
        self._openai_provider = openai_provider
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
        tool_schemas = list(tools or [])
        if tool_schemas and self._use_xml_tools():
            async for event in self._complete_with_xml_tools(
                messages=messages,
                tools=tool_schemas,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield event
            return

        if self.api_format == "openai":
            async for event in self._openai_delegate().complete(
                messages=messages,
                tools=tool_schemas or None,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield event
            return

        async for event in self._complete_ollama(
            messages=messages,
            tools=tool_schemas if tool_schemas and self._use_native_tools() else None,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield event

    async def _complete_with_xml_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema],
        system: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[StreamEvent]:
        chunks: list[str] = []
        done: Done | None = None
        xml_system = _append_xml_tool_prompt(system, tools)

        async for event in self._complete_text_only(
            messages=messages,
            system=xml_system,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if isinstance(event, TextDelta):
                chunks.append(event.text)
                continue
            if isinstance(event, Done):
                done = event
                continue
            if isinstance(event, ProviderError):
                yield event
                return

        text = "".join(chunks)
        try:
            parsed_calls = parse_xml_tool_calls(text)
        except ValueError as exc:
            yield ProviderError(str(exc))
            return

        if not parsed_calls:
            if text:
                yield TextDelta(text)
            yield done or Done()
            return

        visible_text = XML_TOOL_RE.sub("", text).strip()
        if visible_text:
            yield TextDelta(visible_text)

        for call in parsed_calls:
            yield ToolCallStart(tool_call_id=call.tool_call_id, name=call.name)
            yield ToolCallDelta(
                tool_call_id=call.tool_call_id,
                name=call.name,
                partial_json=call.raw_arguments,
            )
            yield ToolCallReady(
                tool_call_id=call.tool_call_id,
                name=call.name,
                input=call.arguments,
            )

        yield Done(usage=done.usage if done else None, stop_reason="tool_calls")

    async def _complete_text_only(
        self,
        messages: Sequence[ChatMessage],
        system: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[StreamEvent]:
        if self.api_format == "openai":
            async for event in self._openai_delegate().complete(
                messages=messages,
                tools=None,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield event
            return

        async for event in self._complete_ollama(
            messages=messages,
            tools=None,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield event

    async def _complete_ollama(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None,
        system: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[StreamEvent]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": format_ollama_messages(messages, system),
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if tools:
            request["tools"] = [format_local_tool_schema(tool) for tool in tools]

        usage: Usage | None = None
        stop_reason: str | None = None
        call_counter = 0
        try:
            async for chunk in self._stream_ollama_request(request):
                if "error" in chunk:
                    yield ProviderError(str(chunk["error"]))
                    return

                message = chunk.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        yield TextDelta(content)

                    for raw_call in _raw_tool_calls(message):
                        call_counter += 1
                        name, arguments = _tool_call_parts(raw_call)
                        if not name:
                            continue
                        tool_call_id = f"local_call_{call_counter}"
                        raw_arguments = json.dumps(arguments, sort_keys=True)
                        yield ToolCallStart(tool_call_id=tool_call_id, name=name)
                        yield ToolCallDelta(tool_call_id=tool_call_id, name=name, partial_json=raw_arguments)
                        yield ToolCallReady(tool_call_id=tool_call_id, name=name, input=arguments)

                usage = _ollama_usage_from(chunk, usage)
                done_reason = chunk.get("done_reason")
                if isinstance(done_reason, str):
                    stop_reason = done_reason
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning("local_ollama_stream_failed", error=str(exc))
            yield ProviderError(f"Local provider request failed: {exc}")
            return

        yield Done(usage=usage, stop_reason=stop_reason)

    async def _stream_ollama_request(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = _ollama_api_url(self.base_url, "chat")
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "ollama":
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self._client is not None:
            async with self._client.stream("POST", url, json=request, headers=headers, timeout=None) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    parsed = _parse_ndjson_line(line)
                    if parsed is not None:
                        yield parsed
            return

        async with httpx.AsyncClient() as client:
            async with client.stream("POST", url, json=request, headers=headers, timeout=None) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    parsed = _parse_ndjson_line(line)
                    if parsed is not None:
                        yield parsed

    def _openai_delegate(self) -> LLMProvider:
        if self._openai_provider is not None:
            return self._openai_provider
        self._openai_provider = OpenAIProvider(
            api_key=self.api_key or "ollama",
            model=self.model,
            max_tokens=self.max_tokens,
            base_url=_openai_base_url(self.base_url),
        )
        return self._openai_provider

    def _use_native_tools(self) -> bool:
        return self.tool_mode == "native" or (self.tool_mode == "auto" and self.supports_tools)

    def _use_xml_tools(self) -> bool:
        return self.tool_mode == "xml" or (self.tool_mode == "auto" and not self.supports_tools)


def format_ollama_messages(messages: Sequence[ChatMessage], system: str | None = None) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    tool_names_by_id: dict[str, str] = {}
    if system:
        formatted.append({"role": "system", "content": system})

    for message in messages:
        if message.role == "assistant":
            formatted.append(_format_ollama_assistant_message(message.content, tool_names_by_id))
            continue
        formatted.extend(_format_ollama_user_or_tool_messages(message.content, tool_names_by_id))

    return formatted


def format_local_tool_schema(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def parse_xml_tool_calls(text: str) -> list[ParsedXMLToolCall]:
    calls: list[ParsedXMLToolCall] = []
    for index, match in enumerate(XML_TOOL_RE.finditer(text), start=1):
        name = match.group("name")
        body = match.group("body").strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse XML tool call JSON: {exc}") from exc

        arguments: dict[str, Any]
        if name:
            if not isinstance(parsed, dict):
                raise ValueError("XML tool call arguments must be a JSON object.")
            arguments = parsed
        else:
            if not isinstance(parsed, dict):
                raise ValueError("XML tool call body must be a JSON object.")
            raw_name = parsed.get("name")
            if not isinstance(raw_name, str) or not raw_name:
                raise ValueError("XML tool call is missing a tool name.")
            name = raw_name
            raw_arguments = parsed.get("arguments", parsed.get("input", {}))
            if not isinstance(raw_arguments, dict):
                raise ValueError("XML tool call arguments must be a JSON object.")
            arguments = raw_arguments

        raw_arguments_text = json.dumps(arguments, sort_keys=True)
        calls.append(
            ParsedXMLToolCall(
                tool_call_id=f"xml_call_{index}",
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments_text,
            )
        )
    return calls


def _format_ollama_assistant_message(
    blocks: Sequence[ContentBlock],
    tool_names_by_id: dict[str, str],
) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            tool_use_id = str(block.get("id", ""))
            name = str(block.get("name", ""))
            tool_names_by_id[tool_use_id] = name
            tool_calls.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": block.get("input", {}),
                    },
                }
            )

    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(part for part in text_parts if part)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _format_ollama_user_or_tool_messages(
    blocks: Sequence[ContentBlock],
    tool_names_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "tool_result":
            tool_use_id = str(block.get("tool_use_id", ""))
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_names_by_id.get(tool_use_id, tool_use_id),
                    "content": str(block.get("content", "")),
                }
            )
        elif block_type == "text":
            text_parts.append(str(block.get("text", "")))

    if text_parts:
        messages.append({"role": "user", "content": "\n".join(part for part in text_parts if part)})
    return messages


def _append_xml_tool_prompt(system: str | None, tools: Sequence[ToolSchema]) -> str:
    schemas = json.dumps([format_local_tool_schema(tool)["function"] for tool in tools], indent=2, sort_keys=True)
    prompt = (
        "Local tool-calling fallback is enabled. If you need to call a tool, output only one or more XML blocks "
        "in this exact format, with JSON arguments inside the block:\n"
        '<tool_call name="read_file">\n{"path": "README.md"}\n</tool_call>\n'
        "Do not wrap tool calls in Markdown. After tool results are provided, answer normally.\n"
        "Available tools:\n"
        f"{schemas}"
    )
    return "\n\n".join(part for part in (system, prompt) if part)


def _raw_tool_calls(message: Mapping[str, Any]) -> list[Any]:
    tool_calls = message.get("tool_calls")
    return tool_calls if isinstance(tool_calls, list) else []


def _tool_call_parts(raw_call: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw_call, Mapping):
        return "", {}
    function = raw_call.get("function", {})
    if not isinstance(function, Mapping):
        return "", {}
    name = function.get("name")
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = {}
        arguments = parsed
    if not isinstance(arguments, dict):
        arguments = {}
    return str(name or ""), arguments


def _ollama_usage_from(chunk: Mapping[str, Any], previous: Usage | None) -> Usage | None:
    input_tokens = chunk.get("prompt_eval_count")
    output_tokens = chunk.get("eval_count")
    if not isinstance(input_tokens, int) and not isinstance(output_tokens, int):
        return previous
    return Usage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else (previous.input_tokens if previous else 0),
        output_tokens=output_tokens if isinstance(output_tokens, int) else (previous.output_tokens if previous else 0),
    )


def _parse_ndjson_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        return None
    return parsed


def _openai_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized + "/"
    return normalized + "/v1/"


def _ollama_api_url(base_url: str, endpoint: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api"):
        return urljoin(normalized + "/", endpoint)
    return urljoin(normalized + "/", "api/" + endpoint.lstrip("/"))
