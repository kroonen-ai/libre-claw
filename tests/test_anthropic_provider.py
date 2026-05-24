# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from libre_claw.core.session import ChatMessage, text_block
from libre_claw.providers.anthropic import AnthropicProvider
from libre_claw.providers.base import Done, TextDelta, ToolCallDelta, ToolCallReady, ToolCallStart, Usage


class FakeMessages:
    def __init__(self, manager: FakeStreamManager) -> None:
        self.manager = manager
        self.last_request: dict[str, Any] | None = None

    def stream(self, **request: Any) -> FakeStreamManager:
        self.last_request = request
        return self.manager


class FakeClient:
    def __init__(self, manager: FakeStreamManager) -> None:
        self.messages = FakeMessages(manager)


class FakeStreamManager:
    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self.stream

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.closed = True


class FakeStream:
    def __init__(self, events: list[object], final_message: object) -> None:
        self.events = events
        self.final_message = final_message

    async def __aiter__(self) -> object:
        for event in self.events:
            yield event

    async def get_final_message(self) -> object:
        return self.final_message


async def test_anthropic_provider_normalizes_text_streaming_events() -> None:
    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=4, output_tokens=2),
        stop_reason="end_turn",
    )
    stream = FakeStream(
        events=[
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=4, output_tokens=1)),
            ),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hel")),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="lo")),
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="end_turn"),
                usage=SimpleNamespace(output_tokens=2),
            ),
            SimpleNamespace(type="message_stop"),
        ],
        final_message=final_message,
    )
    manager = FakeStreamManager(stream)
    client = FakeClient(manager)
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6", max_tokens=99, client=client)

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
        Done(usage=Usage(input_tokens=4, output_tokens=2), stop_reason="end_turn"),
    ]
    assert client.messages.last_request == {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        "max_tokens": 99,
        "temperature": 0.7,
        "system": "test system",
    }
    assert manager.closed is True


async def test_anthropic_provider_normalizes_streamed_tool_call() -> None:
    final_message = SimpleNamespace(usage=SimpleNamespace(input_tokens=8, output_tokens=5), stop_reason="tool_use")
    stream = FakeStream(
        events=[
            SimpleNamespace(
                type="content_block_start",
                index=1,
                content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={}),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":'),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="input_json_delta", partial_json='"README.md"}'),
            ),
            SimpleNamespace(type="content_block_stop", index=1),
            SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="tool_use"), usage=None),
        ],
        final_message=final_message,
    )
    provider = AnthropicProvider(
        api_key="test-key",
        model="claude-sonnet-4-6",
        max_tokens=99,
        client=FakeClient(FakeStreamManager(stream)),
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
        )
    ]

    assert events == [
        ToolCallStart(tool_call_id="toolu_1", name="read_file"),
        ToolCallDelta(tool_call_id="toolu_1", name="read_file", partial_json='{"path":'),
        ToolCallDelta(tool_call_id="toolu_1", name="read_file", partial_json='"README.md"}'),
        ToolCallReady(tool_call_id="toolu_1", name="read_file", input={"path": "README.md"}),
        Done(usage=Usage(input_tokens=8, output_tokens=5), stop_reason="tool_use"),
    ]
