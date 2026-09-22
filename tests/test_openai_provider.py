# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from libre_claw.core.session import ChatMessage, UserAttachment, image_block, text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import (
    CacheableSystemPrompt, Done, ProviderConfigurationError, TextDelta, ToolCallDelta, ToolCallReady, ToolCallStart, Usage,
)
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
    request = dict(client.chat.completions.last_request)
    extra_body = request.pop("extra_body")
    assert extra_body["prompt_cache_key"].startswith("libre-claw:")
    assert request == {
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


async def test_openai_provider_parses_extended_usage_metadata() -> None:
    client = FakeClient(
        [
            chunk(content="ok", finish_reason="stop"),
            chunk(
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 7},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                    "cost": 0.000071,
                }
            ),
        ]
    )
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)

    events = [event async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Hi")])])]

    assert events == [
        TextDelta("ok"),
        Done(
            usage=Usage(
                input_tokens=10,
                output_tokens=5,
                cached_tokens=3,
                reasoning_tokens=2,
                cost=0.000071,
                cache_write_tokens=7,
            ),
            stop_reason="stop",
        ),
    ]


async def test_openai_cache_key_tracks_reusable_prefix_not_growing_history() -> None:
    client = FakeClient([])
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)
    tools = [{"name": "read_file", "description": "Read a file", "input_schema": {"type": "object"}}]
    history = [ChatMessage(role="user", content=[text_block("private user text")])]

    async def request_key(system: str, request_tools: list[dict[str, Any]]) -> str:
        _ = [event async for event in provider.complete(history, tools=request_tools, system=system)]
        request = client.chat.completions.last_request
        assert request is not None
        assert "prompt_cache_retention" not in request["extra_body"]
        assert "prompt_cache_options" not in request["extra_body"]
        return request["extra_body"]["prompt_cache_key"]

    key = await request_key("private system prompt", tools)
    history.extend([
        ChatMessage(role="assistant", content=[text_block("Working on it")]),
        ChatMessage(role="user", content=[text_block("Keep going")]),
    ])
    assert await request_key("private system prompt", tools) == key
    # A separate conversation with the same reusable system/tools shares routing.
    history[:] = [ChatMessage(role="user", content=[text_block("A different task")])]
    assert await request_key("private system prompt", tools) == key
    assert await request_key("changed system prompt", tools) != key
    assert await request_key("private system prompt", []) != key
    assert len(key) <= 64
    assert "private" not in key and "test-key" not in key


async def test_openai_cache_key_uses_stable_system_boundary_but_sends_fresh_state() -> None:
    client = FakeClient([])
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)
    keys = []
    for suffix, user_text in [("Plan: pending", "Original task"), ("Plan: done", "Compacted history")]:
        system = CacheableSystemPrompt("Stable repository instructions", suffix, cache_scope="conversation-a")
        _ = [event async for event in provider.complete(
            [ChatMessage(role="user", content=[text_block(user_text)])], system=system,
        )]
        request = client.chat.completions.last_request
        keys.append(request["extra_body"]["prompt_cache_key"])
        assert request["messages"][0]["content"] == f"Stable repository instructions\n\n{suffix}"
        assert "cache_control" not in json.dumps(request)
    assert keys[0] == keys[1]


async def test_openai_explicit_cache_key_and_opt_out() -> None:
    client = FakeClient([])
    provider = OpenAIProvider(
        api_key="test-key", model="gpt-4o", max_tokens=99, client=client, prompt_cache_key="workspace-v1",
    )
    history = [ChatMessage(role="user", content=[text_block("Hello")])]
    _ = [event async for event in provider.complete(history)]
    assert client.chat.completions.last_request["extra_body"] == {"prompt_cache_key": "workspace-v1"}

    provider.prompt_caching = False
    _ = [event async for event in provider.complete(history)]
    assert "extra_body" not in client.chat.completions.last_request


@pytest.mark.parametrize("value", ["false", "true", "auto", 0, 1, {}, []])
def test_openai_rejects_non_boolean_prompt_caching(value: Any) -> None:
    with pytest.raises(ProviderConfigurationError, match="prompt_caching must be a boolean"):
        OpenAIProvider(
            api_key="test-key", model="gpt-4o", max_tokens=99, client=FakeClient([]), prompt_caching=value,
        )


@pytest.mark.parametrize("endpoint", [
    "https://api.deepseek.com/v1", "http://localhost:8080/v1", "https://proxy.example/v1",
    "https://api.openai.com.example/v1", "https://api.openai.com/custom/v1",
])
async def test_openai_custom_endpoints_never_receive_native_cache_controls(endpoint: str) -> None:
    client = FakeClient([])
    provider = OpenAIProvider(
        api_key="test-key", model="custom", max_tokens=99, client=client,
        base_url=endpoint, prompt_caching=True,
    )
    _ = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert "extra_body" not in client.chat.completions.last_request


async def test_openai_effective_client_endpoint_overrides_default_for_caching() -> None:
    client = FakeClient([])
    client.base_url = "https://private-proxy.example/v1"
    provider = OpenAIProvider(api_key="test-key", model="custom", max_tokens=99, client=client)
    _ = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert "extra_body" not in client.chat.completions.last_request


async def test_openai_cache_usage_survives_partial_stream_usage_updates() -> None:
    client = FakeClient([
        chunk(usage={"prompt_tokens": 20, "prompt_tokens_details": {"cached_tokens": 9, "cache_write_tokens": 11}}),
        chunk(usage={"completion_tokens": 4}),
    ])
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert events == [Done(usage=Usage(input_tokens=20, output_tokens=4, cached_tokens=9, cache_write_tokens=11))]


async def test_openai_cache_key_is_sent_on_sdk_wire_as_top_level_field() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = openai.AsyncOpenAI(api_key="test-key", base_url="https://api.openai.com/v1", http_client=http_client)
        provider = OpenAIProvider(
            api_key="test-key", model="gpt-4o", max_tokens=99, client=client, prompt_cache_key="stable-workspace",
        )
        _ = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]

    assert requests[0]["prompt_cache_key"] == "stable-workspace"
    assert "extra_body" not in requests[0]
    assert "prompt_cache_retention" not in requests[0]


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


async def test_openai_provider_formats_user_image_blocks() -> None:
    client = FakeClient([chunk(content="seen", finish_reason="stop")])
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o", max_tokens=99, client=client)
    image = UserAttachment(media_type="image/png", data="aGVsbG8=", filename="shot.png")

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("What is this?"), image_block(image)])],
        )
    ]

    assert events == [TextDelta("seen"), Done(usage=None, stop_reason="stop")]
    assert client.chat.completions.last_request["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
    ]


async def test_openai_provider_omits_temperature_for_reasoning_models() -> None:
    client = FakeClient([chunk(content="ok", finish_reason="stop")])
    provider = OpenAIProvider(api_key="test-key", model="o3", max_tokens=99, client=client)

    _ = [event async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Hi")])])]

    assert "temperature" not in client.chat.completions.last_request
