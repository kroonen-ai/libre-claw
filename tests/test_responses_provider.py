# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import httpx
import pytest

from libre_claw.core.session import (
    ChatMessage, UserAttachment, image_block, provider_reasoning_block,
    text_block, tool_result_block, tool_use_block,
)
from libre_claw.providers.base import (
    CacheableSystemPrompt, Done, ProviderConfigurationError, ProviderError,
    ReasoningDelta, TextDelta, ToolCallDelta, ToolCallReady, ToolCallStart, Usage,
)
from libre_claw.providers.model_catalog import ModelInfo
from libre_claw.providers.responses import ResponsesProvider


def message(text: str = "Hello", *, phase: str = "final_answer") -> dict[str, Any]:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "status": "completed", "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def function(call_id: str = "call_1", arguments: str = '{"path":"README.md"}') -> dict[str, Any]:
    return {
        "type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
        "name": "read_file", "arguments": arguments, "status": "completed",
    }


def response(output: list[dict[str, Any]], *, status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp_1", "object": "response", "status": status, "output": output,
        "usage": {"input_tokens": 1200, "output_tokens": 50,
                  "input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 200},
                  "output_tokens_details": {"reasoning_tokens": 30}},
    }


def sse(*events: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n" for event in events)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, block: bool = False) -> None:
        self.chunks = chunks
        self.closed = False
        self.block = block
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.block:
            self.waiting.set()
            await asyncio.Future()

    async def aclose(self) -> None:
        self.closed = True


async def test_responses_nonstream_formats_images_functions_and_headers() -> None:
    captured = []

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append((request, json.loads(request.content)))
        return httpx.Response(200, json=response([message("Done")]))

    image = image_block(UserAttachment("image/png", "aGVsbG8=", "screenshot.png"))
    messages = [
        ChatMessage("user", [text_block("Inspect"), image]),
        ChatMessage("assistant", [text_block("Reading"), tool_use_block("call_1", "read_file", {"path": "README.md"})]),
        ChatMessage("user", [tool_result_block("call_1", "File contents"), text_block("Continue")]),
    ]
    tools = [{"name": "read_file", "description": "Read text", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"],
    }}]
    original = copy.deepcopy((messages, tools))
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        provider = ResponsesProvider(
            "key", "gpt-6-astra", 100, "https://opencode.ai/zen/go/v1/", client,
            default_headers={"User-Agent": "libre-claw/test", "x-opencode-session": "session-1"},
            provider_scope="opencode-go",
        )
        events = [event async for event in provider.complete(
            messages, tools=tools, system=CacheableSystemPrompt("Policy", "Current checkpoint"), stream=False,
        )]
        assert not client.is_closed

    request, body = captured[0]
    assert str(request.url) == "https://opencode.ai/zen/go/v1/responses"
    assert request.headers["authorization"] == "Bearer key"
    assert request.headers["user-agent"] == "libre-claw/test"
    assert request.headers["x-opencode-session"] == "session-1"
    assert body["instructions"] == "Policy\n\nCurrent checkpoint"
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["max_output_tokens"] == 100
    assert body["stream"] is False
    assert "temperature" not in body and "prompt_cache_retention" not in body
    assert body["tools"][0] == {"type": "function", "name": "read_file", "description": "Read text",
                                  "parameters": tools[0]["input_schema"], "strict": False}
    assert body["input"] == [
        {"role": "user", "content": [
            {"type": "input_text", "text": "Inspect"}, {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
        ]},
        {"role": "assistant", "content": [{"type": "input_text", "text": "Reading"}]},
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "README.md"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "File contents"},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]
    assert (messages, tools) == original
    assert events[0] == TextDelta("Done")
    assert isinstance(events[1], ReasoningDelta)
    assert events[-1] == Done(Usage(1200, 50, 900, 30, cache_write_tokens=200), "stop")


async def test_responses_streams_text_and_only_finishes_tool_calls_after_completed_event() -> None:
    call = function()
    complete = response([message("Hi café", phase="commentary"), call])
    body = sse(
        {"type": "response.created", "response": {"status": "in_progress"}},
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "Hi "},
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "café"},
        {"type": "response.output_item.added", "output_index": 1, "item": {**call, "arguments": "", "status": "in_progress"}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": call["id"], "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": call["id"], "delta": '"README.md"}'},
        {"type": "response.function_call_arguments.done", "output_index": 1, "arguments": call["arguments"]},
        {"type": "response.output_item.done", "output_index": 1, "item": call},
        {"type": "response.completed", "response": complete},
    )
    # Deliberately split within UTF-8 and JSON tokens as real networks may do.
    stream = TrackingStream([body[index:index + 7] for index in range(0, len(body), 7)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        provider = ResponsesProvider("key", "gpt-6-astra", 100, client=client)
        events = [event async for event in provider.complete([ChatMessage("user", [text_block("Read")])])]

    assert events[:5] == [
        TextDelta("Hi "), TextDelta("café"), ToolCallStart("call_1", "read_file"),
        ToolCallDelta("call_1", "read_file", '{"path":'), ToolCallDelta("call_1", "read_file", '"README.md"}'),
    ]
    assert isinstance(events[5], ReasoningDelta)
    assert events[6:] == [ToolCallReady("call_1", "read_file", {"path": "README.md"}),
                          Done(Usage(1200, 50, 900, 30, cache_write_tokens=200), "tool_calls")]
    assert stream.closed


async def test_responses_preserves_encrypted_reasoning_and_phases_without_duplicate_history() -> None:
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-encrypted-token"},
        message("Let me inspect that", phase="commentary"), function(),
    ]
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response(output if len(requests) == 1 else [message("Complete")]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        provider = ResponsesProvider("key", "gpt-6-astra", 100, "https://opencode.ai/zen/v1", client, provider_scope="opencode")
        initial = ChatMessage("user", [text_block("Read the file")])
        events = [event async for event in provider.complete([initial], stream=False)]
        saved = next(event for event in events if isinstance(event, ReasoningDelta))
        history = [
            initial,
            ChatMessage("assistant", [provider_reasoning_block(saved.text, saved.provider), text_block("Let me inspect that"),
                                      tool_use_block("call_1", "read_file", {"path": "README.md"})]),
            ChatMessage("user", [tool_result_block("call_1", "Read successfully")]),
        ]
        original = copy.deepcopy(history)
        _ = [event async for event in provider.complete(history, stream=False)]
        assert requests[1]["input"][1:4] == output
        assert requests[1]["input"][4] == {"type": "function_call_output", "call_id": "call_1", "output": "Read successfully"}
        assert len(requests[1]["input"]) == 5
        assert history == original
        # Switching service, endpoint, or model must not replay opaque state.
        for options in (
            {"provider_scope": "opencode-go"}, {"base_url": "https://other.example/v1"}, {"model": "another-model"},
        ):
            settings = {"api_key": "key", "model": "gpt-6-astra", "max_tokens": 100,
                        "base_url": "https://opencode.ai/zen/v1", "client": client, "provider_scope": "opencode", **options}
            foreign = ResponsesProvider(**settings)
            assert "opaque-encrypted-token" not in json.dumps(foreign._format_messages(history))


@pytest.mark.parametrize("ending", ["incomplete", "failed", "error", "eof", "done_only", "truncated"])
async def test_responses_failure_never_exposes_executable_tool_calls(ending: str) -> None:
    call = function()
    body = sse(
        {"type": "response.output_item.added", "output_index": 0, "item": call},
        {"type": "response.function_call_arguments.done", "output_index": 0, "arguments": call["arguments"]},
        {"type": "response.output_item.done", "output_index": 0, "item": call},
    )
    if ending in {"incomplete", "failed"}:
        payload = response([call], status=ending)
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
        body += sse({"type": f"response.{ending}", "response": payload})
    elif ending == "error":
        body += sse({"type": "error", "message": "upstream unavailable"})
    elif ending == "done_only":
        body += b"data: [DONE]\n\n"
    elif ending == "truncated":
        body += sse({"type": "response.completed", "response": response([call])})[:-1]
    stream = TrackingStream([body])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        events = [event async for event in ResponsesProvider("key", "gpt-6-astra", 100, client=client).complete([])]
    assert any(isinstance(event, ProviderError) for event in events)
    assert not any(isinstance(event, (ToolCallReady, Done, ReasoningDelta)) for event in events)
    assert stream.closed


@pytest.mark.parametrize("invalid", [
    [function("call_1"), function("call_2", "not JSON")],
    [function("call_1"), function("call_2", "[]")],
    [function("call_1"), function("call_1")],
    [{**function(), "call_id": ""}],
    [{**function(), "status": "in_progress"}],
])
async def test_responses_validates_all_final_calls_before_any_can_execute(invalid) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(invalid)))) as client:
        events = [event async for event in ResponsesProvider("key", "gpt-6-astra", 100, client=client).complete([], stream=False)]
    assert len(events) == 1 and isinstance(events[0], ProviderError)


async def test_responses_missing_streamed_call_in_completed_payload_is_an_error() -> None:
    body = sse(
        {"type": "response.output_item.added", "output_index": 0, "item": function()},
        {"type": "response.completed", "response": response([])},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        events = [event async for event in ResponsesProvider("key", "gpt-6-astra", 100, client=client).complete([])]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, ToolCallReady) for event in events)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
async def test_responses_supports_multiline_sse_comments_and_event_type_field(newline) -> None:
    payload = json.dumps({"response": response([message("Okay")])}, indent=2)
    body = b": keepalive\r\n\r\nevent: response.completed\r\n" + b"".join(
        b"data: " + line.encode() + b"\r\n" for line in payload.splitlines()
    ) + b"\r\n"
    body = body.replace(b"\r\n", newline)
    stream = TrackingStream([body[index:index + 1] for index in range(len(body))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        events = [event async for event in ResponsesProvider("key", "gpt-6-astra", 100, client=client).complete([])]
    assert events[0] == TextDelta("Okay")
    assert isinstance(events[-1], Done)


async def test_responses_cancellation_closes_stream_and_propagates() -> None:
    stream = TrackingStream([], block=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        iterator = ResponsesProvider("key", "gpt-6-astra", 100, client=client).complete([])
        pending = asyncio.create_task(anext(iterator))
        await stream.waiting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert stream.closed
        assert not client.is_closed


@pytest.mark.parametrize("body", [b"data: not-json\n\n", b"data: []\n\n"])
async def test_responses_rejects_malformed_sse(body) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        events = [event async for event in ResponsesProvider("key", "model", 100, client=client).complete([])]
    assert len(events) == 1 and isinstance(events[0], ProviderError)


async def test_responses_limits_unterminated_events(monkeypatch) -> None:
    monkeypatch.setattr("libre_claw.providers.responses.MAX_EVENT_BYTES", 32)
    stream = TrackingStream([b"data: " + b"x" * 40])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        events = [event async for event in ResponsesProvider("key", "model", 100, client=client).complete([])]
    assert len(events) == 1 and isinstance(events[0], ProviderError)
    assert "size limit" in events[0].message and stream.closed


@pytest.mark.parametrize("stream", [True, False])
async def test_responses_http_failure_is_clear_and_redacts_key(stream: bool) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        429, json={"error": {"message": "Budget exhausted for secret-key"}},
    ))) as client:
        events = [event async for event in ResponsesProvider("secret-key", "model", 100, client=client).complete([], stream=stream)]
    assert len(events) == 1 and isinstance(events[0], ProviderError)
    assert "HTTP 429" in events[0].message and "Budget exhausted" in events[0].message
    assert "secret-key" not in events[0].message


async def test_responses_does_not_follow_redirects_even_when_client_enables_them() -> None:
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://different.example/responses"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        events = [event async for event in ResponsesProvider("secret-key", "model", 100, client=client).complete([], stream=False)]
    assert len(requests) == 1
    assert len(events) == 1 and isinstance(events[0], ProviderError)


async def test_responses_obeys_published_capabilities_before_network_request() -> None:
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response([message()]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        provider = ResponsesProvider("key", "model", 100, client=client)
        provider.model_info = ModelInfo("opencode", "model", "Model", max_completion_tokens=40,
                                        supports_tools=False, supports_vision=False, supports_temperature=True,
                                        supports_reasoning=True, supported_reasoning_efforts=("low", "high"))
        provider.reasoning_effort = "high"
        _ = [event async for event in provider.complete([], tools=[{"name": "tool"}], stream=False, temperature=0.3)]
        assert requests[0]["max_output_tokens"] == 40
        assert requests[0]["temperature"] == 0.3
        assert requests[0]["reasoning"] == {"effort": "high"}
        assert "tools" not in requests[0]
        events = [event async for event in provider.complete([
            ChatMessage("user", [image_block(UserAttachment("image/png", "aA=="))]),
        ])]
        assert len(requests) == 1 and isinstance(events[0], ProviderError)


@pytest.mark.parametrize("base_url", ["file:///tmp/service", "https://user:secret@example.com/v1", "https://api.example/v1?key=secret", "http://"])
def test_responses_rejects_invalid_endpoint_shapes(base_url) -> None:
    with pytest.raises(ProviderConfigurationError):
        ResponsesProvider("key", "model", 100, base_url=base_url)


def test_responses_encodes_multimodal_tool_output_and_foreign_reasoning_safely() -> None:
    provider = ResponsesProvider("key", "model", 100)
    result = provider._format_messages([
        ChatMessage("assistant", [provider_reasoning_block("private foreign data", "deepseek"), text_block("Read image")]),
        ChatMessage("user", [{"type": "tool_result", "tool_use_id": "call_1", "content": [
            text_block("Screenshot"), image_block(UserAttachment("image/png", "aA==")),
        ]}]),
    ])
    assert "private foreign data" not in json.dumps(result)
    assert result[-1] == {"type": "function_call_output", "call_id": "call_1", "output": [
        {"type": "input_text", "text": "Screenshot"}, {"type": "input_image", "image_url": "data:image/png;base64,aA=="},
    ]}
