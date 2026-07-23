# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from libre_claw.core.session import (
    ChatMessage,
    provider_reasoning_block,
    text_block,
    tool_use_block,
)
from libre_claw.providers.base import Done, ReasoningDelta, TextDelta, Usage
from libre_claw.providers.moonshot import MoonshotProvider


class FakeCompletions:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.last_request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> FakeMoonshotStream:
        self.last_request = request
        return FakeMoonshotStream(self.chunks)


class FakeChat:
    def __init__(self, chunks: list[object]) -> None:
        self.completions = FakeCompletions(chunks)


class FakeClient:
    def __init__(self, chunks: list[object]) -> None:
        self.chat = FakeChat(chunks)


class FakeMoonshotStream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> object:
        for chunk in self.chunks:
            yield chunk


def chunk(
    *,
    content: str | None = None,
    reasoning_content: str | None = None,
    finish_reason: str | None = None,
    choice_usage: object | None = None,
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=None,
                ),
                finish_reason=finish_reason,
                usage=choice_usage,
            )
        ]
    )


async def test_kimi_k3_streams_reasoning_and_uses_k3_parameters() -> None:
    client = FakeClient(
        [
            chunk(reasoning_content="private "),
            chunk(reasoning_content="thought", content="Hello", finish_reason="stop"),
            chunk(
                choice_usage=SimpleNamespace(
                    prompt_tokens=11,
                    completion_tokens=7,
                )
            ),
        ]
    )
    provider = MoonshotProvider(
        api_key="test-key",
        model="k3",
        max_tokens=4096,
        reasoning_effort="high",
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hi")])],
        )
    ]

    assert events == [
        ReasoningDelta("private ", provider="moonshot"),
        ReasoningDelta("thought", provider="moonshot"),
        TextDelta("Hello"),
        Done(usage=Usage(input_tokens=11, output_tokens=7), stop_reason="stop"),
    ]
    request = client.chat.completions.last_request
    assert request is not None
    assert request["max_tokens"] == 4096
    assert request["reasoning_effort"] == "high"
    assert "max_completion_tokens" not in request
    assert "stream_options" not in request
    assert "temperature" not in request


async def test_kimi_k2_7_uses_max_tokens_and_required_thinking_defaults() -> None:
    client = FakeClient([chunk(content="done", finish_reason="stop")])
    provider = MoonshotProvider(
        api_key="test-key",
        model="kimi-for-coding",
        max_tokens=32768,
        client=client,
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Fix it")])],
        )
    ]

    assert events == [TextDelta("done"), Done(usage=None, stop_reason="stop")]
    request = client.chat.completions.last_request
    assert request is not None
    assert request["max_tokens"] == 32768
    assert "max_completion_tokens" not in request
    assert "reasoning_effort" not in request
    assert "extra_body" not in request


async def test_kimi_k2_6_can_disable_thinking() -> None:
    client = FakeClient([chunk(content="done", finish_reason="stop")])
    provider = MoonshotProvider(
        api_key="test-key",
        model="kimi-k2.6",
        max_tokens=32768,
        service="platform",
        thinking="disabled",
        client=client,
    )

    _ = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Answer directly")])],
        )
    ]

    request = client.chat.completions.last_request
    assert request is not None
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_moonshot_preserves_reasoning_and_tool_calls_in_assistant_history() -> None:
    client = FakeClient([chunk(content="done", finish_reason="stop")])
    provider = MoonshotProvider(
        api_key="test-key",
        model="kimi-k3",
        max_tokens=4096,
        service="platform",
        client=client,
    )

    _ = [
        event
        async for event in provider.complete(
            messages=[
                ChatMessage(
                    role="assistant",
                    content=[
                        provider_reasoning_block("opaque reasoning", "moonshot"),
                        tool_use_block("call_1", "read_file", {"path": "README.md"}),
                    ],
                ),
            ],
        )
    ]

    request = client.chat.completions.last_request
    assert request is not None
    assert request["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "opaque reasoning",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "README.md"}',
                    },
                }
            ],
        }
    ]
