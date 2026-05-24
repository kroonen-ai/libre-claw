# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from libre_claw.core.session import ChatMessage, text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import Done, TextDelta, ToolCallDelta, ToolCallReady, ToolCallStart, Usage
from libre_claw.providers.openai import OpenAIProvider


class FakeCompletions:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.last_request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> FakeOpenAIStream:
        self.last_request = request
        return FakeOpenAIStream(self.chunks)


class FakeChat:
    def __init__(self, chunks: list[object]) -> None:
        self.completions = FakeCompletions(chunks)


class FakeClient:
    def __init__(self, chunks: list[object]) -> None:
        self.chat = FakeChat(chunks)


class FakeOpenAIStream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> object:
        for chunk in self.chunks:
            yield chunk


def chunk(
    *,
    content: str | None = None,
    tool_calls: list[object] | None = None,
    finish_reason: str | None = None,
    usage: object | None = None,
) -> object:
    choices = []
    if content is not None or tool_calls is not None or finish_reason is not None:
        choices.append(
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        )
    return SimpleNamespace(choices=choices, usage=usage)


def tool_delta(index: int, tool_call_id: str | None, name: str | None, arguments: str | None) -> object:
    return SimpleNamespace(
        index=index,
        id=tool_call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


async def test_openai_provider_streams_text_and_formats_request() -> None:
    client = FakeClient(
        [
            chunk(content="Hel"),
            chunk(content="lo", finish_reason="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2)),
        ]
    )
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hello")])],
            system="test system",
        )
    ]

    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        Done(usage=Usage(input_tokens=4, output_tokens=2), stop_reason="stop"),
    ]
    assert client.chat.completions.last_request == {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "Hello"},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 99,
        "temperature": 0.7,
    }


async def test_openai_provider_streams_tool_calls_and_formats_tools() -> None:
    client = FakeClient(
        [
            chunk(
                tool_calls=[
                    tool_delta(index=0, tool_call_id="call_1", name="read_file", arguments='{"path":')
                ]
            ),
            chunk(tool_calls=[tool_delta(index=0, tool_call_id=None, name=None, arguments='"README.md"}')]),
            chunk(finish_reason="tool_calls"),
            chunk(usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4)),
        ]
    )
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        )
    ]

    assert events == [
        ToolCallStart(tool_call_id="call_1", name="read_file"),
        ToolCallDelta(tool_call_id="call_1", name="read_file", partial_json='{"path":'),
        ToolCallDelta(tool_call_id="call_1", name="read_file", partial_json='"README.md"}'),
        ToolCallReady(tool_call_id="call_1", name="read_file", input={"path": "README.md"}),
        Done(usage=Usage(input_tokens=9, output_tokens=4), stop_reason="tool_calls"),
    ]
    assert client.chat.completions.last_request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    assert client.chat.completions.last_request["tool_choice"] == "auto"


async def test_openai_provider_formats_tool_history_messages() -> None:
    client = FakeClient([chunk(content="done", finish_reason="stop")])
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)

    events = [
        event
        async for event in provider.complete(
            messages=[
                ChatMessage(role="assistant", content=[tool_use_block("call_1", "read_file", {"path": "README.md"})]),
                ChatMessage(role="user", content=[tool_result_block("call_1", "contents")]),
            ],
        )
    ]

    assert events == [TextDelta("done"), Done(usage=None, stop_reason="stop")]
    assert client.chat.completions.last_request["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "contents"},
    ]


async def test_openai_provider_omits_temperature_for_reasoning_models() -> None:
    client = FakeClient([chunk(content="ok", finish_reason="stop")])
    provider = OpenAIProvider(api_key="test-key", model="o3", max_tokens=99, client=client)

    _ = [event async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Hi")])])]

    assert "temperature" not in client.chat.completions.last_request
