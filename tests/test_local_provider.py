# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from libre_claw.core.session import ChatMessage, text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallReady,
    ToolCallStart,
    ToolSchema,
    Usage,
)
from libre_claw.providers.local import (
    LocalProvider,
    format_local_tool_schema,
    format_ollama_messages,
    parse_xml_tool_calls,
    _ollama_api_url,
)


class FakeHTTPClient:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.last_request: dict[str, Any] | None = None
        self.last_url = ""

    def stream(self, method: str, url: str, **kwargs: Any) -> "FakeHTTPStream":
        self.last_url = url
        self.last_request = {"method": method, **kwargs}
        return FakeHTTPStream(self.chunks)


class FakeHTTPStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    async def __aenter__(self) -> "FakeHTTPStream":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> object:
        for chunk in self.chunks:
            yield json.dumps(chunk)


class FakeDelegateProvider(LLMProvider):
    def __init__(self, events: list[StreamEvent]) -> None:
        self.events = events
        self.last_tools: object = None
        self.last_system: str | None = None

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, stream, temperature, max_tokens
        self.last_tools = tools
        self.last_system = system
        for event in self.events:
            yield event


TOOL_SCHEMA = {
    "name": "read_file",
    "description": "Read a file",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}


async def test_local_provider_streams_ollama_text_and_usage() -> None:
    client = FakeHTTPClient(
        [
            {"message": {"content": "Hel"}, "done": False},
            {
                "message": {"content": "lo"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        ]
    )
    provider = LocalProvider(
        base_url="http://localhost:11434",
        model="qwen3:32b",
        max_tokens=99,
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hello")])],
            system="system",
        )
    ]

    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        Done(usage=Usage(input_tokens=4, output_tokens=2), stop_reason="stop"),
    ]
    assert client.last_url == "http://localhost:11434/api/chat"
    assert client.last_request is not None
    assert client.last_request["json"]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "Hello"},
    ]
    assert client.last_request["json"]["options"]["num_predict"] == 99


async def test_local_provider_streams_ollama_tool_calls() -> None:
    client = FakeHTTPClient(
        [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            }
                        }
                    ]
                },
                "done": False,
            },
            {"done": True, "done_reason": "stop"},
        ]
    )
    provider = LocalProvider(
        base_url="http://localhost:11434",
        model="qwen3:32b",
        max_tokens=99,
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[TOOL_SCHEMA],
        )
    ]

    assert events == [
        ToolCallStart(tool_call_id="local_call_1", name="read_file"),
        ToolCallDelta(tool_call_id="local_call_1", name="read_file", partial_json='{"path": "README.md"}'),
        ToolCallReady(tool_call_id="local_call_1", name="read_file", input={"path": "README.md"}),
        Done(usage=None, stop_reason="stop"),
    ]
    assert client.last_request is not None
    assert client.last_request["json"]["tools"] == [format_local_tool_schema(TOOL_SCHEMA)]


async def test_local_provider_supports_ollama_cloud_api_key() -> None:
    client = FakeHTTPClient([{"message": {"content": "cloud"}, "done": True}])
    provider = LocalProvider(
        base_url="https://ollama.com/api",
        model="gpt-oss:120b",
        max_tokens=99,
        api_key="cloud-key",
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hello cloud")])],
        )
    ]

    assert events == [TextDelta("cloud"), Done(usage=None, stop_reason=None)]
    assert client.last_url == "https://ollama.com/api/chat"
    assert client.last_request is not None
    assert client.last_request["headers"]["Authorization"] == "Bearer cloud-key"


def test_ollama_api_url_accepts_root_or_api_base_url() -> None:
    assert _ollama_api_url("http://localhost:11434", "chat") == "http://localhost:11434/api/chat"
    assert _ollama_api_url("https://ollama.com", "chat") == "https://ollama.com/api/chat"
    assert _ollama_api_url("https://ollama.com/api", "chat") == "https://ollama.com/api/chat"


async def test_local_provider_xml_fallback_parses_tool_calls() -> None:
    client = FakeHTTPClient(
        [
            {
                "message": {
                    "content": '<tool_call name="read_file">\n{"path": "README.md"}\n</tool_call>'
                },
                "done": True,
                "prompt_eval_count": 5,
                "eval_count": 8,
            }
        ]
    )
    provider = LocalProvider(
        base_url="http://localhost:11434",
        model="tiny",
        max_tokens=99,
        supports_tools=False,
        tool_mode="auto",
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[TOOL_SCHEMA],
        )
    ]

    assert events == [
        ToolCallStart(tool_call_id="xml_call_1", name="read_file"),
        ToolCallDelta(tool_call_id="xml_call_1", name="read_file", partial_json='{"path": "README.md"}'),
        ToolCallReady(tool_call_id="xml_call_1", name="read_file", input={"path": "README.md"}),
        Done(usage=Usage(input_tokens=5, output_tokens=8), stop_reason="tool_calls"),
    ]
    assert client.last_request is not None
    system_message = client.last_request["json"]["messages"][0]
    assert system_message["role"] == "system"
    assert "Local tool-calling fallback" in system_message["content"]


def test_parse_xml_tool_call_body_name_shape() -> None:
    calls = parse_xml_tool_calls(
        '<tool_call>{"name": "bash", "arguments": {"command": "printf hello"}}</tool_call>'
    )

    assert calls[0].name == "bash"
    assert calls[0].arguments == {"command": "printf hello"}


def test_format_ollama_messages_preserves_tool_history() -> None:
    messages = [
        ChatMessage(role="assistant", content=[tool_use_block("call_1", "read_file", {"path": "README.md"})]),
        ChatMessage(role="user", content=[tool_result_block("call_1", "contents")]),
    ]

    assert format_ollama_messages(messages) == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "README.md"},
                    },
                }
            ],
        },
        {"role": "tool", "tool_name": "read_file", "content": "contents"},
    ]


async def test_local_provider_delegates_openai_compatible_native_tools() -> None:
    delegate = FakeDelegateProvider([TextDelta("ok"), Done(stop_reason="stop")])
    provider = LocalProvider(
        base_url="http://localhost:11434",
        model="qwen3:32b",
        max_tokens=99,
        api_format="openai",
        openai_provider=delegate,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hi")])],
            tools=[TOOL_SCHEMA],
        )
    ]

    assert events == [TextDelta("ok"), Done(stop_reason="stop")]
    assert delegate.last_tools == [TOOL_SCHEMA]


async def test_local_provider_openai_compatible_xml_fallback() -> None:
    delegate = FakeDelegateProvider(
        [
            TextDelta('<tool_call name="read_file">{"path": "README.md"}</tool_call>'),
            Done(usage=Usage(input_tokens=1, output_tokens=2), stop_reason="stop"),
        ]
    )
    provider = LocalProvider(
        base_url="http://localhost:11434",
        model="tiny",
        max_tokens=99,
        api_format="openai",
        supports_tools=False,
        openai_provider=delegate,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[TOOL_SCHEMA],
        )
    ]

    assert events == [
        ToolCallStart(tool_call_id="xml_call_1", name="read_file"),
        ToolCallDelta(tool_call_id="xml_call_1", name="read_file", partial_json='{"path": "README.md"}'),
        ToolCallReady(tool_call_id="xml_call_1", name="read_file", input={"path": "README.md"}),
        Done(usage=Usage(input_tokens=1, output_tokens=2), stop_reason="tool_calls"),
    ]
    assert delegate.last_tools is None
    assert delegate.last_system is not None
    assert "Local tool-calling fallback" in delegate.last_system
