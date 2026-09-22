# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from libre_claw.core.session import ChatMessage, provider_reasoning_block, text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import Done, ProviderConfigurationError, ProviderError, ReasoningDelta, TextDelta, ToolCallReady
from libre_claw.providers.model_catalog import ModelInfo
from libre_claw.providers.opencode_google import OpenCodeGoogleProvider


BASE = "https://opencode.ai/zen/v1"
MODEL = "future-gemini-model"
MESSAGES = [ChatMessage("user", [text_block("Help with this repository.")])]


def provider(client, **options):
    return OpenCodeGoogleProvider("sk-opencode-test", options.pop("model", MODEL), 8192, base_url=options.pop("base_url", BASE), client=client, **options)


def result(parts=None, *, finish="STOP", usage=None):
    candidate = {"index": 0, "content": {"role": "model", "parts": parts if parts is not None else [{"text": "Ready."}]}}
    if finish is not None:
        candidate["finishReason"] = finish
    data = {"candidates": [candidate]}
    if usage:
        data["usageMetadata"] = usage
    return data


def sse(*payloads):
    return b"".join(b"data: " + json.dumps(payload).encode() + b"\r\n\r\n" for payload in payloads)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("stream", [False, True])
async def test_native_google_request_uses_opencode_key_and_normalizes_usage(stream):
    seen = []
    payload = result(usage={"promptTokenCount": 100, "cachedContentTokenCount": 40, "candidatesTokenCount": 20, "thoughtsTokenCount": 7})

    def respond(request):
        seen.append(request)
        return httpx.Response(200, content=sse(payload) if stream else json.dumps(payload).encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [event async for event in provider(client, default_headers={"x-opencode-session": "session"}).complete(
            MESSAGES, [{"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
            system="Use the project instructions.", stream=stream,
        )]
        assert not client.is_closed
    request = seen[0]
    assert request.url.host == "opencode.ai"
    assert request.url.path == f"/zen/v1/models/{MODEL}:{'streamGenerateContent' if stream else 'generateContent'}"
    assert dict(request.url.params) == ({"alt": "sse"} if stream else {})
    assert request.headers["x-goog-api-key"] == "sk-opencode-test"
    assert "authorization" not in request.headers
    assert request.headers["x-opencode-session"] == "session"
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "Use the project instructions."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Help with this repository."}]}]
    assert body["generationConfig"]["maxOutputTokens"] == 8192
    assert body["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]["properties"]["path"] == {"type": "string"}
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["Ready."]
    done = events[-1]
    assert isinstance(done, Done)
    assert (done.usage.input_tokens, done.usage.output_tokens, done.usage.cached_tokens, done.usage.reasoning_tokens) == (100, 27, 40, 7)


async def test_parallel_tool_history_replays_signatures_and_native_ids_without_mutation():
    parts = [
        {"text": "Private reasoning", "thought": True},
        {"text": "I will check both files."},
        {"functionCall": {"name": "read_file", "args": {"path": "a.py"}, "id": "native-a"}, "thoughtSignature": "opaque-A=="},
        {"functionCall": {"name": "read_file", "args": {"path": "b.py"}}},
        {"text": "", "thoughtSignature": "opaque-tail=="},
    ]
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=sse(result(parts if len(seen) == 1 else [{"text": "Checked."}])))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        transport = provider(client)
        events = [event async for event in transport.complete(MESSAGES)]
        reasoning = next(event for event in events if isinstance(event, ReasoningDelta))
        calls = [event for event in events if isinstance(event, ToolCallReady)]
        assert len(calls) == 2
        assert calls[0].tool_call_id == "native-a"
        assert calls[1].tool_call_id.startswith("google_")
        assert "Private reasoning" not in "".join(event.text for event in events if isinstance(event, TextDelta))
        history = [*MESSAGES, ChatMessage("assistant", [provider_reasoning_block(reasoning.text, reasoning.provider), text_block("I will check both files."), *[
            tool_use_block(call.tool_call_id, call.name, call.input) for call in calls
        ]]), ChatMessage("user", [tool_result_block(calls[0].tool_call_id, "File A"), tool_result_block(calls[1].tool_call_id, "File B")])]
        original = copy.deepcopy(history)
        replay = [event async for event in transport.complete(history)]
        assert isinstance(replay[-1], Done)
        assert history == original
    assert seen[1]["contents"][1] == {"role": "model", "parts": parts}
    responses = seen[1]["contents"][2]["parts"]
    assert responses == [
        {"functionResponse": {"id": "native-a", "name": "read_file", "response": {"output": "File A"}}},
        {"functionResponse": {"name": "read_file", "response": {"output": "File B"}}},
    ]


@pytest.mark.parametrize("changed", [{"model": "another-model"}, {"base_url": "https://opencode.ai/go/v1"}, {"provider_scope": "other-account"}])
async def test_opaque_output_is_not_replayed_to_another_scope(changed):
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=result([{"text": "Answer", "thoughtSignature": "private-signature"}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=False)]
        private = next(event for event in events if isinstance(event, ReasoningDelta))
        history = [*MESSAGES, ChatMessage("assistant", [provider_reasoning_block(private.text, private.provider), text_block("Answer")])]
        final = [event async for event in provider(client, **changed).complete(history, stream=False)]
        assert isinstance(final[-1], Done)
    assert seen[1]["contents"][1]["parts"] == [{"text": "Answer"}]


async def test_images_and_multimodal_tool_outputs_use_native_inline_data():
    seen = []
    image = {"type": "image", "media_type": "image/png", "data": "aW1hZ2U="}
    messages = [ChatMessage("user", [text_block("Inspect"), image]), ChatMessage("assistant", [tool_use_block("call", "screenshot", {})]),
                ChatMessage("user", [{"type": "tool_result", "tool_use_id": "call", "content": [text_block("Screenshot"), image]}])]
    original = copy.deepcopy(messages)

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=result())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [event async for event in provider(client).complete(messages, stream=False)]
    assert isinstance(events[-1], Done)
    assert messages == original
    expected = {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}}
    assert seen[0]["contents"][0]["parts"][1] == expected
    assert seen[0]["contents"][-1]["parts"][0]["functionResponse"] == {
        "id": "call", "name": "screenshot", "response": {"output": "Screenshot"}, "parts": [expected],
    }


@pytest.mark.parametrize("finish", [None, "MAX_TOKENS", "SAFETY", "MALFORMED_FUNCTION_CALL", "OTHER"])
async def test_unfinished_or_blocked_tool_calls_are_never_executable(finish):
    payload = result([{"functionCall": {"name": "bash", "args": {"command": "ls"}}}], finish=finish)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=sse(payload)))) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, (ToolCallReady, Done, ReasoningDelta)) for event in events)


@pytest.mark.parametrize("tail", [b"data: {broken", b'data: {"error":{"message":"failed"}}\n\n',
                                 b'event: error\ndata: {"message":"failed"}\n\n', httpx.ReadError("connection interrupted")])
async def test_trailing_stream_failure_cannot_release_an_earlier_tool_call(tail):
    call = {"functionCall": {"id": "one", "name": "bash", "args": {"command": "ls"}}}
    chunks = Chunks(sse(result([call])), tail)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=chunks))) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, ToolCallReady) for event in events)
    assert chunks.closed


@pytest.mark.parametrize("call", [
    {"name": "", "args": {}}, {"name": "bash", "args": []}, {"name": "bash", "id": 1, "args": {}},
    {"name": "bash", "args": {"value": float("nan")}}, {"name": "bash", "partialArgs": [{"value": "unfinished"}]},
])
async def test_malformed_functions_cannot_release_any_valid_sibling(call):
    payload = result([{"functionCall": {"id": "valid", "name": "read_file", "args": {}}}, {"functionCall": call}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=sse(payload)))) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, ToolCallReady) for event in events)


async def test_duplicate_native_call_ids_are_rejected():
    call = {"functionCall": {"id": "duplicate", "name": "read_file", "args": {}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result([call, call])))) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, ToolCallReady) for event in events)


async def test_sse_handles_split_utf8_comments_and_multiline_json():
    raw = ': keepalive\r\n\r\ndata: {"candidates":\r\ndata: [{"content":{"parts":[{"text":"✓ café"}]},"finishReason":"STOP"}]}\r\n\r\n'.encode()
    chunks = Chunks(*(raw[index:index + 1] for index in range(len(raw))))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=chunks))) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["✓ café"]
    assert isinstance(events[-1], Done)
    assert chunks.closed


@pytest.mark.parametrize("stream", [False, True])
async def test_response_size_is_bounded(monkeypatch, stream):
    monkeypatch.setattr("libre_claw.providers.opencode_google.MAX_RESPONSE_BYTES", 50)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=sse(result())))) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=stream)]
    assert isinstance(events[-1], ProviderError)
    assert "size limit" in events[-1].message


async def test_cancellation_closes_response_without_yielding_a_tool():
    call = {"functionCall": {"name": "read_file", "args": {}}}
    chunks = Chunks(sse(result([call], finish=None)), asyncio.CancelledError())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=chunks))) as client:
        with pytest.raises(asyncio.CancelledError):
            _ = [event async for event in provider(client).complete(MESSAGES)]
        assert not client.is_closed
    assert chunks.closed


async def test_plain_text_token_limit_is_a_length_completion():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result(finish="MAX_TOKENS")))) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], Done)
    assert events[-1].stop_reason == "length"


async def test_capability_settings_shape_the_google_request():
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=result())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        transport = provider(client)
        transport.model_info = ModelInfo("opencode", MODEL, "Future Gemini", supports_temperature=False, supports_tools=False,
                                         supports_reasoning=True, supported_reasoning_efforts=("low", "high"), max_completion_tokens=512)
        transport.reasoning_effort = "high"
        events = [event async for event in transport.complete(MESSAGES, [{"name": "unused"}], stream=False)]
    assert isinstance(events[-1], Done)
    assert seen[0]["generationConfig"] == {"maxOutputTokens": 512, "candidateCount": 1, "thinkingConfig": {"thinkingLevel": "high"}}
    assert "tools" not in seen[0]


@pytest.mark.parametrize("url", ["https://opencode.ai/zen/v1?key=secret", "https://user:secret@opencode.ai/v1", "file:///tmp/key", "http://"])
def test_invalid_base_urls_are_rejected(url):
    with pytest.raises(ProviderConfigurationError):
        provider(None, base_url=url)


async def test_http_error_redacts_response_content_and_does_not_follow_redirects():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/collect"}, text="sk-opencode-test")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert len(seen) == 1
    assert isinstance(events[-1], ProviderError)
    assert "sk-opencode-test" not in events[-1].message


async def test_native_auth_ignores_inherited_bearer_and_basic_auth():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=result())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"Authorization": "Bearer unrelated"},
                                 auth=("unrelated", "password")) as client:
        events = [event async for event in provider(client, default_headers={"authorization": "Bearer override"}).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], Done)
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["x-goog-api-key"] == "sk-opencode-test"


@pytest.mark.parametrize("payload", [
    {"promptFeedback": {"blockReason": "SAFETY"}}, {"error": {"message": "sk-opencode-test"}},
    {"candidates": []}, {"candidates": [{"index": 0, "content": {"parts": []}, "finishReason": "STOP"}]},
    {"candidates": [{"index": True, "content": {"parts": [{"text": "wrong candidate"}]}, "finishReason": "STOP"}]},
])
async def test_invalid_or_blocked_completions_are_reported_without_secrets(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, Done) for event in events)
    assert "sk-opencode-test" not in events[-1].message


async def test_overflowing_json_number_cannot_become_a_tool_argument():
    raw = b'{"candidates":[{"content":{"parts":[{"functionCall":{"name":"bash","args":{"amount":1e999}}}]},"finishReason":"STOP"}]}'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))) as client:
        events = [event async for event in provider(client).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, ToolCallReady) for event in events)


async def test_unterminated_sse_event_is_bounded(monkeypatch):
    monkeypatch.setattr("libre_claw.providers.opencode_google.MAX_EVENT_BYTES", 32)
    chunks = Chunks(b"data: " + b"x" * 33)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=chunks))) as client:
        events = [event async for event in provider(client).complete(MESSAGES)]
    assert isinstance(events[-1], ProviderError)
    assert "size limit" in events[-1].message
    assert chunks.closed


async def test_request_validation_prevents_network_for_unmatched_tools_and_nonfinite_arguments():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=result())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for messages in (
            [ChatMessage("user", [tool_result_block("missing", "result")])],
            [ChatMessage("assistant", [tool_use_block("call", "bash", {"number": float("inf")})])],
        ):
            events = [event async for event in provider(client).complete(messages, stream=False)]
            assert isinstance(events[-1], ProviderError)
    assert calls == []


async def test_opencode_dispatcher_uses_native_google_route_from_model_metadata(monkeypatch):
    from libre_claw.providers.opencode import OpenCodeProvider

    async def metadata(_client):
        return {"opencode": {"models": {MODEL: {"provider": {
            "npm": "@ai-sdk/google", "api": "https://generativelanguage.googleapis.com/v1beta",
        }}}}}

    monkeypatch.setattr("libre_claw.providers.opencode.opencode_metadata", metadata)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=result([{"text": "Native Google route", "thoughtSignature": "opaque"}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [event async for event in OpenCodeProvider("test-key", MODEL, 100, client=client).complete(MESSAGES, stream=False)]
    assert isinstance(events[-1], Done)
    assert seen[0].url.host == "opencode.ai"
    assert seen[0].url.path == f"/zen/v1/models/{MODEL}:generateContent"
    assert seen[0].headers["x-goog-api-key"] == "test-key"
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["x-opencode-session"]
    assert next(event for event in events if isinstance(event, ReasoningDelta)).provider.startswith("opencode:")
