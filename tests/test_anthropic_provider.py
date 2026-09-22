# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic

from libre_claw.core.session import (
    ChatMessage,
    UserAttachment,
    image_block,
    provider_reasoning_block,
    text_block,
    tool_use_block,
)
from libre_claw.providers.anthropic import AnthropicProvider
from libre_claw.providers.base import (
    CacheableSystemPrompt,
    Done,
    ProviderConfigurationError,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    ToolCallReady,
    ToolCallStart,
    Usage,
)


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
    provider = AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001", max_tokens=99, client=client)

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
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        "max_tokens": 99,
        "temperature": 0.7,
        "system": [{"type": "text", "text": "test system", "cache_control": {"type": "ephemeral"}}],
        "cache_control": {"type": "ephemeral"},
    }
    assert manager.closed is True


async def test_anthropic_provider_omits_temperature_for_opus_5() -> None:
    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    stream = FakeStream(
        events=[
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="ok")),
            SimpleNamespace(type="message_stop"),
        ],
        final_message=final_message,
    )
    manager = FakeStreamManager(stream)
    client = FakeClient(manager)
    provider = AnthropicProvider(api_key="test-key", model="claude-opus-5", max_tokens=99, client=client)

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hello")])],
        )
    ]

    assert events == [
        TextDelta("ok"),
        Done(usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn"),
    ]
    assert client.messages.last_request is not None
    assert client.messages.last_request["model"] == "claude-opus-5"
    assert "temperature" not in client.messages.last_request


async def test_anthropic_provider_omits_temperature_for_sonnet_4_6() -> None:
    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    stream = FakeStream(
        events=[
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="ok")),
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
        )
    ]

    assert events == [
        TextDelta("ok"),
        Done(usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn"),
    ]
    assert client.messages.last_request is not None
    assert client.messages.last_request["model"] == "claude-sonnet-4-6"
    assert "temperature" not in client.messages.last_request


async def test_anthropic_provider_omits_temperature_for_sonnet_5() -> None:
    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    stream = FakeStream(
        events=[
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="ok")),
            SimpleNamespace(type="message_stop"),
        ],
        final_message=final_message,
    )
    manager = FakeStreamManager(stream)
    client = FakeClient(manager)
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-5", max_tokens=99, client=client)

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Hello")])],
        )
    ]

    assert events == [
        TextDelta("ok"),
        Done(usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn"),
    ]
    assert client.messages.last_request is not None
    assert client.messages.last_request["model"] == "claude-sonnet-5"
    assert "temperature" not in client.messages.last_request


async def test_anthropic_provider_formats_user_image_blocks() -> None:
    final_message = SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream = FakeStream(
        events=[
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="seen")),
            SimpleNamespace(type="message_stop"),
        ],
        final_message=final_message,
    )
    client = FakeClient(FakeStreamManager(stream))
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6", max_tokens=99, client=client)
    image = UserAttachment(media_type="image/png", data="aGVsbG8=", filename="shot.png")

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("What is this?"), image_block(image)])],
        )
    ]

    assert events == [TextDelta("seen"), Done(usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn")]
    assert client.messages.last_request["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aGVsbG8=",
                    },
                },
            ],
        }
    ]


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


async def test_anthropic_provider_round_trips_thinking_blocks_without_modification() -> None:
    final_message = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=8, output_tokens=5),
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="signed-thinking"),
            SimpleNamespace(type="redacted_thinking", data="encrypted-thinking"),
            SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "README.md"}),
        ],
    )
    stream = FakeStream(
        events=[
            SimpleNamespace(
                type="content_block_start",
                index=2,
                content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={}),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=2,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"README.md"}'),
            ),
            SimpleNamespace(type="content_block_stop", index=2),
        ],
        final_message=final_message,
    )
    provider = AnthropicProvider(
        api_key="test-key",
        model="claude-opus-5",
        max_tokens=65_536,
        client=FakeClient(FakeStreamManager(stream)),
    )

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("Read README")])],
            tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
        )
    ]

    reasoning = next(event for event in events if isinstance(event, ReasoningDelta))
    replayed = provider._format_messages(
        [
            ChatMessage(
                role="assistant",
                content=[
                    provider_reasoning_block(reasoning.text, "anthropic"),
                    tool_use_block("toolu_1", "read_file", {"path": "README.md"}),
                ],
            )
        ]
    )

    assert reasoning.provider == "anthropic"
    assert replayed == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "signed-thinking"},
                {"type": "redacted_thinking", "data": "encrypted-thinking"},
                {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "README.md"}},
            ],
        }
    ]


async def test_anthropic_caches_stable_prefix_and_growing_history_without_mutation() -> None:
    final = SimpleNamespace(usage=None, stop_reason="end_turn")
    client = FakeClient(FakeStreamManager(FakeStream([], final)))
    provider = AnthropicProvider("key", "claude-opus-5", 99, client=client)
    tools = [{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}]
    messages = [ChatMessage(role="user", content=[text_block("Read README")])]
    original_tools = json.dumps(tools)

    _ = [event async for event in provider.complete(messages, tools=tools, system="Stable instructions")]
    first_request = client.messages.last_request
    messages.extend([
        ChatMessage(role="assistant", content=[text_block("Read the README.")]),
        ChatMessage(role="user", content=[text_block("Now read CONTRIBUTING")]),
    ])
    _ = [event async for event in provider.complete(messages, tools=tools, system="Stable instructions")]
    second_request = client.messages.last_request

    assert first_request["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert first_request["tools"] == second_request["tools"]
    assert first_request["system"] == second_request["system"]
    assert first_request["messages"] == second_request["messages"][:1]
    assert second_request["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(tools) == original_tools
    assert all("cache_control" not in block for message in messages for block in message.content)


@pytest.mark.parametrize("base_url, enabled, expected", [
    (None, None, True),
    ("https://api.anthropic.com", None, True),
    ("https://api.kimi.com/coding/", None, False),
    ("https://custom.example/anthropic", True, True),
    (None, False, False),
])
async def test_anthropic_caching_can_be_disabled_and_custom_endpoints_need_opt_in(
    base_url: str | None, enabled: bool | None, expected: bool,
) -> None:
    client = FakeClient(FakeStreamManager(FakeStream([], SimpleNamespace(usage=None))))
    provider = AnthropicProvider("key", "model", 99, client=client, base_url=base_url, prompt_caching=enabled)
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])], system="System",
    )]

    request = client.messages.last_request
    assert ("cache_control" in request) is expected
    assert isinstance(request["system"], list) is expected


def test_anthropic_custom_client_endpoint_does_not_auto_enable_caching() -> None:
    client = SimpleNamespace(base_url="https://custom.example/anthropic")
    assert AnthropicProvider("key", "model", 99, client=client).prompt_caching is False
    assert AnthropicProvider(
        "key", "model", 99, client=client, base_url="https://api.anthropic.com",
    ).prompt_caching is False


@pytest.mark.parametrize("settings", [{"prompt_cache_ttl": "24h"}, {"prompt_caching": "false"}])
def test_anthropic_rejects_invalid_cache_settings(settings: dict[str, Any]) -> None:
    with pytest.raises(ProviderConfigurationError):
        AnthropicProvider("key", "model", 99, client=object(), **settings)


async def test_anthropic_one_hour_cache_is_explicit_and_uses_consistent_ttl() -> None:
    client = FakeClient(FakeStreamManager(FakeStream([], SimpleNamespace(usage=None))))
    provider = AnthropicProvider("key", "model", 99, client=client, prompt_cache_ttl="1h")
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])],
        system="System", tools=[{"name": "tool", "input_schema": {"type": "object"}}],
    )]

    request = client.messages.last_request
    expected = {"type": "ephemeral", "ttl": "1h"}
    assert request["cache_control"] == expected
    assert request["system"][-1]["cache_control"] == expected
    assert request["tools"][-1]["cache_control"] == expected


async def test_anthropic_does_not_conflict_with_caller_supplied_cache_breakpoints() -> None:
    client = FakeClient(FakeStreamManager(FakeStream([], SimpleNamespace(usage=None))))
    provider = AnthropicProvider("key", "model", 99, client=client)
    tool = {"name": "tool", "input_schema": {"type": "object"},
            "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])], tools=[tool], system="System",
    )]

    request = client.messages.last_request
    assert "cache_control" not in request
    assert request["tools"] == [tool]
    assert request["system"] == "System"


@pytest.mark.parametrize("has_final_usage", [True, False])
async def test_anthropic_stream_usage_preserves_cache_counts_without_double_counting(has_final_usage: bool) -> None:
    start_usage = SimpleNamespace(
        input_tokens=7, output_tokens=1, cache_read_input_tokens=1000, cache_creation_input_tokens=200,
    )
    final_usage = SimpleNamespace(
        input_tokens=7, output_tokens=11, cache_read_input_tokens=1000, cache_creation_input_tokens=200,
    )
    stream = FakeStream([
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=start_usage)),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"),
                        usage=SimpleNamespace(output_tokens=11, input_tokens=None,
                                              cache_read_input_tokens=None, cache_creation_input_tokens=None)),
    ], SimpleNamespace(usage=final_usage if has_final_usage else None, stop_reason="end_turn"))
    provider = AnthropicProvider("key", "model", 99, client=FakeClient(FakeStreamManager(stream)))
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hi")])])]

    assert events[-1] == Done(
        usage=Usage(input_tokens=1207, output_tokens=11, cached_tokens=1000, cache_write_tokens=200),
        stop_reason="end_turn",
    )


async def test_anthropic_nonstream_sdk_request_and_cache_usage() -> None:
    requests = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "signed"},
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "README.md"}},
            ],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 3,
                      "cache_read_input_tokens": 1200, "cache_creation_input_tokens": 100},
        })

    async with AsyncAnthropic(api_key="key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        provider = AnthropicProvider("key", "claude-opus-5", 99, client=client)
        events = [event async for event in provider.complete(
            [ChatMessage(role="user", content=[text_block("Read README")])], system="System", stream=False,
        )]

    assert requests[0]["cache_control"] == {"type": "ephemeral"}
    assert requests[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert requests[0].get("stream", False) is False
    assert events[:2] == [TextDelta("Checking."), ToolCallReady("tool_1", "read_file", {"path": "README.md"})]
    assert isinstance(events[2], ReasoningDelta)
    assert events[-1] == Done(
        usage=Usage(input_tokens=1309, output_tokens=3, cached_tokens=1200, cache_write_tokens=100),
        stop_reason="tool_use",
    )


async def test_anthropic_sdk_keeps_stable_system_breakpoint_when_task_state_changes() -> None:
    requests = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": "Okay."}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    prefix = "Follow the project policy.\n\nRelevant skills: run the focused tests."
    prompts = [
        CacheableSystemPrompt(prefix, "Task plan:\n1. [running] Run tests", cache_scope="task-1"),
        CacheableSystemPrompt(prefix, "Task plan:\n1. [done] Run tests\n\nVerification: all passed", cache_scope="task-1"),
    ]
    tools = [{"name": "read_file", "input_schema": {"type": "object"}}]
    messages = [ChatMessage(role="user", content=[text_block("Check this project")])]
    async with AsyncAnthropic(api_key="key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        provider = AnthropicProvider("key", "claude-opus-5", 99, client=client)
        for prompt in prompts:
            _ = [event async for event in provider.complete(messages, system=prompt, tools=tools, stream=False)]

    assert requests[0]["system"][0] == requests[1]["system"][0] == {
        "type": "text", "text": prefix, "cache_control": {"type": "ephemeral"},
    }
    for prompt, request in zip(prompts, requests, strict=True):
        assert "".join(block["text"] for block in request["system"]) == str(prompt)
        assert request["system"][1]["text"] == prompt[len(prefix):]
        assert "cache_control" not in request["system"][1]
        assert request["cache_control"] == {"type": "ephemeral"}
        assert request["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_scope" not in json.dumps(request)
    assert "[running]" not in json.dumps(requests[1])
    assert "Verification: all passed" in requests[1]["system"][1]["text"]
    assert prompts[0].cache_prefix == prompts[1].cache_prefix == prefix
    assert "cache_control" not in tools[0]
    assert messages[0].content == [text_block("Check this project")]


async def test_anthropic_empty_cache_prefix_does_not_mark_empty_system_block() -> None:
    client = FakeClient(FakeStreamManager(FakeStream([], SimpleNamespace(usage=None))))
    provider = AnthropicProvider("key", "model", 99, client=client)
    prompt = CacheableSystemPrompt("", "Only dynamic context")
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])], system=prompt,
    )]

    assert client.messages.last_request["system"] == [{"type": "text", "text": "Only dynamic context"}]
