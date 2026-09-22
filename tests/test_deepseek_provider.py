# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from libre_claw.core.session import (
    ChatMessage,
    UserAttachment,
    image_block,
    provider_reasoning_block,
    text_block,
    tool_result_block,
    tool_use_block,
)
from libre_claw.providers.base import (
    Done,
    ProviderConfigurationError,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    ToolCallReady,
    ToolCallStart,
    Usage,
)
from libre_claw.providers.deepseek import DeepSeekProvider
from libre_claw.providers.model_catalog import ModelInfo


READ_TOOL = {
    "name": "read_file",
    "description": "Read a file",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}


class FakeStream:
    def __init__(self, chunks: list[object], *, wait: bool = False) -> None:
        self.chunks = chunks
        self.wait = wait
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[object]:
        for item in self.chunks:
            if isinstance(item, Exception):
                raise item
            yield item
        self.started.set()
        if self.wait:
            await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


class FakeCompletions:
    def __init__(self, response: FakeStream) -> None:
        self.response = response
        self.last_request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> FakeStream:
        self.last_request = request
        return self.response


class FakeClient:
    def __init__(self, chunks: list[object], *, wait: bool = False) -> None:
        self.response = FakeStream(chunks, wait=wait)
        self.chat = SimpleNamespace(completions=FakeCompletions(self.response))


def chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    calls: list[object] | None = None,
    finish: str | None = None,
    usage: object | None = None,
) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=calls),
            finish_reason=finish,
        )],
        usage=usage,
    )


def tool_delta(index: int, call_id: str | None, name: str | None, arguments: str | None) -> object:
    return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def make_provider(client: object, **kwargs: Any) -> DeepSeekProvider:
    return DeepSeekProvider(api_key="test-deepseek-key", model="deepseek-flash", max_tokens=65536, client=client, **kwargs)


async def test_deepseek_thinking_request_and_incremental_usage() -> None:
    client = FakeClient([
        chunk(reasoning="opaque "),
        chunk(reasoning="reasoning", content="Hello"),
        chunk(finish="stop", usage={
            "prompt_tokens": 20, "completion_tokens": 8,
            "prompt_cache_hit_tokens": 12, "prompt_cache_miss_tokens": 8,
            "completion_tokens_details": {"reasoning_tokens": 6},
        }),
    ])
    provider = make_provider(client)
    provider.model_info = ModelInfo(provider="deepseek", model="deepseek-flash", label="DeepSeek Flash", supports_temperature=True)

    events = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])], system="Be helpful.", max_tokens=4096,
    )]

    assert events == [
        ReasoningDelta("opaque ", "deepseek"), ReasoningDelta("reasoning", "deepseek"), TextDelta("Hello"),
        Done(Usage(input_tokens=20, output_tokens=8, cached_tokens=12, reasoning_tokens=6), "stop"),
    ]
    assert client.chat.completions.last_request == {
        "model": "deepseek-flash",
        "messages": [{"role": "system", "content": "Be helpful."}, {"role": "user", "content": "Hello"}],
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 4096,
        "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    }
    assert client.response.closed


async def test_deepseek_nonthinking_keeps_sampling_and_does_not_reenable_reasoning() -> None:
    client = FakeClient([chunk(content="done", finish="stop")])
    provider = make_provider(client, thinking="disabled", reasoning_effort="max")
    provider.model_info = ModelInfo(
        provider="deepseek", model="deepseek-flash", label="DeepSeek Flash", supports_reasoning=True,
        supported_reasoning_efforts=("low",),
    )
    events = [event async for event in provider.complete(
        [ChatMessage(role="assistant", content=[text_block("Older nonthinking reply")])],
        tools=[READ_TOOL], temperature=0.3,
    )]

    assert events == [TextDelta("done"), Done(stop_reason="stop")]
    request = client.chat.completions.last_request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["temperature"] == 0.3
    assert "reasoning_effort" not in request
    assert "tool_choice" not in request
    assert request["tools"][0]["function"]["name"] == "read_file"


async def test_deepseek_nonreasoning_metadata_uses_nonthinking_sampling() -> None:
    client = FakeClient([chunk(content="done", finish="stop")])
    provider = make_provider(client)
    provider.model_info = ModelInfo(
        provider="deepseek", model="custom-model", label="Custom model", supports_reasoning=False,
    )
    _ = [event async for event in provider.complete([], tools=[READ_TOOL], temperature=0.25)]
    request = client.chat.completions.last_request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["temperature"] == 0.25
    assert "reasoning_effort" not in request


async def test_deepseek_preserves_all_assistant_reasoning_and_tool_order() -> None:
    client = FakeClient([chunk(content="done", finish="stop")])
    provider = make_provider(client)
    _ = [event async for event in provider.complete([
        ChatMessage(role="user", content=[text_block("First question")]),
        ChatMessage(role="assistant", content=[
            provider_reasoning_block("earlier reasoning", "deepseek"), text_block("Earlier final answer"),
        ]),
        ChatMessage(role="user", content=[text_block("Read the file")]),
        ChatMessage(role="assistant", content=[
            provider_reasoning_block("plan ", "deepseek"), provider_reasoning_block("read", "deepseek"),
            provider_reasoning_block("foreign opaque data", "moonshot"),
            tool_use_block("call_1", "read_file", {"path": "README.md"}),
        ]),
        ChatMessage(role="user", content=[tool_result_block("call_1", "File contents")]),
    ], tools=[READ_TOOL])]

    request = client.chat.completions.last_request
    assert request["messages"][1] == {
        "role": "assistant", "content": "Earlier final answer", "reasoning_content": "earlier reasoning",
    }
    assert request["messages"][3] == {
        "role": "assistant", "content": "", "reasoning_content": "plan read",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "read_file", "arguments": '{"path": "README.md"}',
        }}],
    }
    assert request["messages"][4] == {"role": "tool", "tool_call_id": "call_1", "content": "File contents"}
    assert "foreign opaque data" not in json.dumps(request)


async def test_deepseek_missing_reasoning_with_tools_has_actionable_error() -> None:
    client = FakeClient([])
    provider = make_provider(client)
    events = [event async for event in provider.complete([
        ChatMessage(role="assistant", content=[provider_reasoning_block("private", "moonshot"), text_block("Hi")]),
    ], tools=[READ_TOOL])]
    assert len(events) == 1 and isinstance(events[0], ProviderError)
    assert "Start a new DeepSeek session" in events[0].message
    assert "thinking = 'disabled'" in events[0].message
    assert client.chat.completions.last_request is None


async def test_deepseek_explicit_empty_reasoning_is_distinct_from_missing_history() -> None:
    client = FakeClient([chunk(reasoning="", content="done", finish="stop")])
    provider = make_provider(client)
    events = [event async for event in provider.complete([
        ChatMessage(role="assistant", content=[provider_reasoning_block("", "deepseek"), text_block("Hi")]),
        ChatMessage(role="user", content=[text_block("Continue")]),
    ], tools=[READ_TOOL])]
    assert events == [ReasoningDelta("", "deepseek"), TextDelta("done"), Done(stop_reason="stop")]
    assert client.chat.completions.last_request["messages"][0]["reasoning_content"] == ""


async def test_deepseek_history_without_reasoning_works_without_tools() -> None:
    client = FakeClient([chunk(content="done", finish="stop")])
    provider = make_provider(client)
    events = [event async for event in provider.complete([
        ChatMessage(role="assistant", content=[text_block("Imported answer")]),
        ChatMessage(role="user", content=[text_block("Continue")]),
    ])]
    assert events == [TextDelta("done"), Done(stop_reason="stop")]


async def test_deepseek_fragmented_parallel_tool_calls() -> None:
    client = FakeClient([
        chunk(reasoning="Use tools", calls=[tool_delta(1, "call_b", "read_file", '{"path":')]),
        chunk(calls=[tool_delta(0, "call_a", "read_file", '{"path":"a.py"}')]),
        chunk(calls=[tool_delta(1, None, None, '"b.py"}')]),
        chunk(finish="tool_calls", usage={"prompt_tokens": 8, "completion_tokens": 5}),
    ])
    events = [event async for event in make_provider(client).complete([], tools=[READ_TOOL])]
    assert events == [
        ReasoningDelta("Use tools", "deepseek"),
        ToolCallStart("call_b", "read_file"), ToolCallDelta("call_b", "read_file", '{"path":'),
        ToolCallStart("call_a", "read_file"), ToolCallDelta("call_a", "read_file", '{"path":"a.py"}'),
        ToolCallDelta("call_b", "read_file", '"b.py"}'),
        ToolCallReady("call_a", "read_file", {"path": "a.py"}),
        ToolCallReady("call_b", "read_file", {"path": "b.py"}),
        Done(Usage(input_tokens=8, output_tokens=5), "tool_calls"),
    ]


@pytest.mark.parametrize("finish", ["length", "content_filter", "insufficient_system_resource", "aborted", None, "stop"])
async def test_deepseek_never_finalizes_tools_from_an_interrupted_response(finish: str | None) -> None:
    client = FakeClient([
        chunk(calls=[tool_delta(0, "call_1", "read_file", '{"path":"README.md"}')]),
        chunk(finish=finish),
    ])
    events = [event async for event in make_provider(client).complete([], tools=[READ_TOOL])]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)
    assert client.response.closed


@pytest.mark.parametrize("arguments", ['{"path":', '[]'])
async def test_deepseek_rejects_invalid_tool_arguments(arguments: str) -> None:
    client = FakeClient([
        chunk(calls=[tool_delta(0, "call_1", "read_file", arguments)]), chunk(finish="tool_calls"),
    ])
    events = [event async for event in make_provider(client).complete([], tools=[READ_TOOL])]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)


async def test_deepseek_rejects_tool_call_missing_id() -> None:
    client = FakeClient([chunk(calls=[tool_delta(0, None, "read_file", "{}")]), chunk(finish="tool_calls")])
    events = [event async for event in make_provider(client).complete([], tools=[READ_TOOL])]
    assert isinstance(events[-1], ProviderError)
    assert "without its ID or function name" in events[-1].message
    assert not any(isinstance(event, ToolCallReady) for event in events)


async def test_deepseek_preserves_openai_usage_fields_when_cache_extension_absent() -> None:
    client = FakeClient([chunk(finish="stop", usage={
        "prompt_tokens": 10, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 2},
        "completion_tokens_details": {"reasoning_tokens": 3},
    })])
    events = [event async for event in make_provider(client).complete([])]
    assert events == [Done(Usage(input_tokens=10, output_tokens=4, cached_tokens=2, reasoning_tokens=3), "stop")]


async def test_deepseek_derives_input_from_reported_cache_hits_and_misses() -> None:
    client = FakeClient([chunk(finish="stop", usage={
        "completion_tokens": 4, "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20,
    })])
    events = [event async for event in make_provider(client).complete([])]
    assert events == [Done(Usage(input_tokens=100, output_tokens=4, cached_tokens=80), "stop")]


def test_deepseek_partial_usage_preserves_total_and_ignores_invalid_cache_counts() -> None:
    provider = make_provider(FakeClient([]))
    previous = Usage(input_tokens=100, output_tokens=4, cached_tokens=80)
    assert provider._usage_from({"prompt_cache_hit_tokens": 80}, previous) == previous
    assert provider._usage_from({"prompt_cache_hit_tokens": -1}, previous) == previous
    assert provider._usage_from({"prompt_cache_hit_tokens": True}, previous) == previous


async def test_deepseek_maps_image_attachments() -> None:
    client = FakeClient([chunk(content="A cat", finish="stop")])
    image = UserAttachment(media_type="image/png", data="aGVsbG8=", filename="cat.png")
    _ = [event async for event in make_provider(client).complete([
        ChatMessage(role="user", content=[text_block("Describe this"), image_block(image)]),
    ])]
    assert client.chat.completions.last_request["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "Describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    ]}]


async def test_deepseek_closes_stream_on_cancellation() -> None:
    client = FakeClient([], wait=True)
    iterator = make_provider(client).complete([])
    task = asyncio.create_task(anext(iterator))
    await client.response.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.response.closed


async def test_deepseek_closes_stream_when_consumer_stops() -> None:
    client = FakeClient([chunk(content="partial")], wait=True)
    iterator = make_provider(client).complete([])
    assert await anext(iterator) == TextDelta("partial")
    await iterator.aclose()
    assert client.response.closed


async def test_deepseek_closes_stream_on_transport_failure() -> None:
    client = FakeClient([RuntimeError("connection interrupted")])
    events = [event async for event in make_provider(client).complete([])]
    assert events == [ProviderError("DeepSeek request failed: connection interrupted")]
    assert client.response.closed


@pytest.mark.parametrize("status,code", [(401, "invalid_api_key"), (402, "insufficient_balance"), (429, "rate_limit_exceeded")])
async def test_deepseek_http_errors_surface_through_sdk(status: int, code: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": {"message": code, "type": code}}))
    async with AsyncOpenAI(
        api_key="deepseek-test-key", base_url="https://api.deepseek.com", max_retries=0,
        http_client=httpx.AsyncClient(transport=transport),
    ) as client:
        events = [event async for event in make_provider(client).complete([])]
    assert len(events) == 1 and isinstance(events[0], ProviderError)
    assert "DeepSeek request failed" in events[0].message and code in events[0].message


async def test_deepseek_sdk_uses_custom_url_bearer_auth_and_stream_extensions() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = {
            "id": "completion_1", "object": "chat.completion.chunk", "created": 1, "model": "custom-model",
            "choices": [{"index": 0, "delta": {"reasoning_content": "opaque", "content": "done"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7, "prompt_cache_hit_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        }
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n")

    async with AsyncOpenAI(
        api_key="deepseek-test-key", base_url="https://proxy.example/deepseek/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        provider = DeepSeekProvider("deepseek-test-key", "custom-model", 8192, client=client)
        events = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert events == [ReasoningDelta("opaque", "deepseek"), TextDelta("done"), Done(Usage(4, 3, 2, 1), "stop")]
    assert str(requests[0].url) == "https://proxy.example/deepseek/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer deepseek-test-key"
    body = json.loads(requests[0].content)
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 8192 and body["model"] == "custom-model"
    assert "max_completion_tokens" not in body


async def test_deepseek_supports_sdk_without_reasoning_effort_keyword() -> None:
    class LegacyCompletions:
        async def create(
            self, *, model: str, messages: list[dict[str, Any]], stream: bool,
            max_tokens: int, stream_options: dict[str, Any], extra_body: dict[str, Any],
        ) -> FakeStream:
            assert extra_body == {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}
            return FakeStream([chunk(content="done", finish="stop")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=LegacyCompletions()))
    events = [event async for event in make_provider(client, reasoning_effort="max").complete([])]
    assert events == [TextDelta("done"), Done(stop_reason="stop")]


@pytest.mark.parametrize("kwargs", [{"thinking": "auto"}, {"reasoning_effort": "invalid"}])
def test_deepseek_invalid_controls_fail_at_configuration(kwargs: dict[str, str]) -> None:
    with pytest.raises(ProviderConfigurationError):
        make_provider(FakeClient([]), **kwargs)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_deepseek_rejects_invalid_configured_token_limit(value: Any) -> None:
    with pytest.raises(ProviderConfigurationError, match="positive integer"):
        DeepSeekProvider("test-key", "custom-model", value, client=FakeClient([]))


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
async def test_deepseek_rejects_invalid_request_token_limit(value: Any) -> None:
    client = FakeClient([])
    events = [event async for event in make_provider(client).complete([], max_tokens=value)]
    assert events == [ProviderError("DeepSeek max_tokens must be a positive integer.")]
    assert client.chat.completions.last_request is None
