# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import httpx
import pytest

from libre_claw.auth.api_keys import ApiKeyLookup
from libre_claw.config import load_config
from libre_claw.core.session import ChatMessage, provider_reasoning_block, text_block, tool_result_block, tool_use_block
from libre_claw.providers import model_catalog, opencode
from libre_claw.providers.base import CacheableSystemPrompt, Done, ProviderConfigurationError, ProviderError, ReasoningDelta, TextDelta, ToolCallReady
from libre_claw.providers.model_catalog import discover_models
from libre_claw.providers.opencode import OpenCodeProvider, discover_opencode_rows, opencode_metadata


@pytest.fixture(autouse=True)
def clean_caches(monkeypatch):
    opencode._METADATA.clear()
    monkeypatch.setattr(opencode, "_METADATA_EXPIRES", 0)
    model_catalog._CACHE.clear()
    model_catalog._ACTIVE_KEYS.clear()
    model_catalog._SELECTED_CACHE.clear()


def metadata(models, *, provider="opencode", npm="@ai-sdk/openai-compatible"):
    return {provider: {"npm": npm, "api": "https://must-never-receive-keys.invalid", "models": models}}


def chat_response(*, reasoning=None, tool=False):
    delta = {"content": "hello"}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tool:
        delta["tool_calls"] = [{"index": 0, "id": "call-1", "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}]
    chunks = [{"id": "response", "object": "chat.completion.chunk", "created": 1, "model": "future",
               "choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if tool else "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 8}}}]
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                          text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n")


@pytest.mark.parametrize("provider,prefix", [("opencode", "/zen/v1"), ("opencode-go", "/zen/go/v1")])
async def test_auto_chat_uses_metadata_not_model_name_and_keeps_plan_and_headers(provider, prefix):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.host == "models.dev":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=metadata({"brand-new-2029": {"tool_call": True}}, provider=provider))
        assert request.url.host == "opencode.ai"
        assert request.url.path == prefix + "/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.headers["user-agent"].startswith("libre-claw/")
        assert request.headers["x-opencode-session"] == hashlib.sha256(b"private-session").hexdigest()
        body = json.loads(request.content)
        assert body["model"] == "brand-new-2029" and body["max_tokens"] == 70
        assert "prompt_cache_key" not in body
        return chat_response(reasoning="consider this", tool=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "brand-new-2029", 70, provider=provider, client=client, session_id="private-session")
        prompt = CacheableSystemPrompt("stable", cache_scope="private-session")
        events = [event async for event in selected.complete([ChatMessage("user", [text_block("hi")])], system=prompt)]
        assert TextDelta("hello") in events
        assert any(isinstance(event, ToolCallReady) and event.input == {"path": "README.md"} for event in events)
        assert isinstance(events[-1], Done) and events[-1].usage.cached_tokens == 8
        reason = next(event for event in events if isinstance(event, ReasoningDelta))
        assert reason.provider.startswith(provider + ":")
        assert not client.is_closed
    assert len(requests) == 2


@pytest.mark.parametrize("provider,prefix", [("opencode", "/zen/v1"), ("opencode-go", "/zen/go/v1")])
async def test_auto_messages_route_uses_anthropic_auth_and_native_thinking(provider, prefix):
    requests = []
    thinking = {"type": "thinking", "thinking": "private reasoning", "signature": "signature-value"}
    async def respond(request):
        requests.append(request)
        if request.url.host == "models.dev":
            return httpx.Response(200, json=metadata({"unbranded-agent": {"provider": {"npm": "@ai-sdk/anthropic"}}}, provider=provider))
        assert request.url.path == prefix + "/messages"
        assert request.headers["x-api-key"] == "test-secret"
        assert request.headers["x-opencode-session"]
        assert request.headers["user-agent"].startswith("libre-claw/")
        return httpx.Response(200, json={"id": "msg1", "type": "message", "role": "assistant", "model": "unbranded-agent",
            "content": [thinking, {"type": "text", "text": "okay"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 4}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "unbranded-agent", 90, provider=provider, client=client)
        events = [event async for event in selected.complete([ChatMessage("user", [text_block("hi")])], stream=False)]
        assert TextDelta("okay") in events
        reason = next(event for event in events if isinstance(event, ReasoningDelta))
        assert reason.provider != "anthropic"
        assert json.loads(reason.text)["content"][0] == thinking
        assert events[-1].usage.input_tokens == 11
        replay = [ChatMessage("assistant", [provider_reasoning_block(reason.text, provider=reason.provider), text_block("okay")]),
                  ChatMessage("user", [text_block("continue")])]
        _ = [event async for event in selected.complete(replay, stream=False)]
        sent = json.loads(requests[-1].content)
        assert sent["messages"][0]["content"][0] == thinking
        assert "cache_control" not in sent
        assert "cache_control" not in sent["messages"][0]["content"][0]


async def test_auto_responses_route_for_unknown_model_keeps_secure_endpoint_and_headers():
    def respond(request):
        if request.url.host == "models.dev":
            return httpx.Response(200, json=metadata({"future-model": {"provider": {"npm": "@ai-sdk/openai", "api": "https://untrusted.invalid"}}}, provider="opencode-go"))
        assert str(request.url) == "https://opencode.ai/zen/go/v1/responses"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.headers["x-opencode-session"]
        body = json.loads(request.content)
        assert body["store"] is False and "reasoning.encrypted_content" in body["include"]
        return httpx.Response(200, json={"id": "resp1", "status": "completed", "output": [
            {"id": "msg1", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "response", "annotations": []}]}
        ], "usage": {"input_tokens": 5, "output_tokens": 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "future-model", 100, provider="opencode-go", client=client)
        events = [event async for event in selected.complete([ChatMessage("user", [text_block("hi")])], stream=False)]
    assert TextDelta("response") in events
    assert isinstance(events[-1], Done)


async def test_protocol_change_does_not_replay_anthropic_opaque_content_as_chat_reasoning():
    def respond(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "msg1", "type": "message", "role": "assistant", "model": "future",
                "content": [{"type": "thinking", "thinking": "private", "signature": "signed-private-data"},
                            {"type": "text", "text": "okay"}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 2}})
        body = json.loads(request.content)
        assert "reasoning_content" not in body["messages"][0]
        assert "signed-private-data" not in request.content.decode()
        return chat_response()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "future", 90, api_format="anthropic", client=client)
        events = [event async for event in selected.complete([], stream=False)]
        reason = next(event for event in events if isinstance(event, ReasoningDelta))
        selected.api_format = "chat"
        replay = [ChatMessage("assistant", [provider_reasoning_block(reason.text, provider=reason.provider), text_block("okay")])]
        events = [event async for event in selected.complete(replay)]
    assert isinstance(events[-1], Done)


@pytest.mark.parametrize("reasoning", ["preserve this reasoning", ""])
async def test_chat_tool_roundtrip_uses_empty_text_and_only_its_scoped_reasoning(reasoning):
    def respond(request):
        body = json.loads(request.content)
        assistant, result = body["messages"]
        assert assistant["content"] == ""
        assert assistant["reasoning_content"] == reasoning
        assert assistant["tool_calls"][0]["id"] == "call-1"
        assert result == {"role": "tool", "tool_call_id": "call-1", "content": "file contents"}
        assert "foreign-reasoning" not in request.content.decode()
        return chat_response()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("secret", "future", 80, api_format="chat", client=client)
        history = [ChatMessage("assistant", [
            provider_reasoning_block(reasoning, provider=selected._scope + ":chat"),
            provider_reasoning_block("foreign-reasoning", provider="deepseek"),
            tool_use_block("call-1", "read_file", {"path": "README.md"}),
        ]), ChatMessage("user", [tool_result_block("call-1", "file contents")])]
        events = [event async for event in selected.complete(history)]
    assert isinstance(events[-1], Done)


async def test_unknown_metadata_fails_before_inference_and_manual_format_works_offline():
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.host == "models.dev":
            return httpx.Response(503)
        return chat_response()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        auto = OpenCodeProvider("secret", "unlisted", 80, client=client)
        events = [event async for event in auto.complete([])]
        assert len(events) == 1 and isinstance(events[0], ProviderError)
        assert "api_format" in events[0].message
        assert len(requests) == 1
        explicit = OpenCodeProvider("secret", "unlisted", 80, api_format="chat", client=client)
        assert isinstance([event async for event in explicit.complete([])][-1], Done)
        assert len(requests) == 2


async def test_public_metadata_does_not_inherit_headers_cookies_or_basic_auth():
    requests = []
    def respond(request):
        requests.append(request)
        assert set(request.headers) == {"host", "user-agent", "accept"}
        return httpx.Response(200, json=metadata({"future": {}}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), auth=("secret-user", "secret-password"),
            headers={"Authorization": "Bearer another-secret", "x-api-key": "secret", "x-opencode-session": "private"},
            cookies={"session": "secret-cookie"}) as client:
        assert "opencode" in await opencode_metadata(client)
        await opencode_metadata(client)
        assert len(requests) == 1
        await opencode_metadata(client, refresh=True)
        assert len(requests) == 2


async def test_direct_auto_provider_honors_advertised_limits_temperature_and_reasoning():
    def respond(request):
        if request.url.host == "models.dev":
            return httpx.Response(200, json=metadata({"future": {
                "temperature": False, "reasoning": True, "tool_call": True,
                "reasoning_options": [{"type": "effort", "values": ["low", "high"]}],
                "limit": {"context": 1000, "output": 30},
            }}))
        body = json.loads(request.content)
        assert body["max_tokens"] == 30
        assert "temperature" not in body
        assert body["reasoning_effort"] == "high"
        return chat_response()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("secret", "future", 100, client=client, reasoning_effort="high")
        events = [event async for event in selected.complete([])]
    assert isinstance(events[-1], Done)


async def test_metadata_loads_coalesce_and_refresh_can_change_protocol():
    calls = 0
    async def respond(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=metadata({"future": {"provider": {"npm": "@ai-sdk/openai" if calls > 1 else "@ai-sdk/anthropic"}}}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await asyncio.gather(*(opencode_metadata(client) for _ in range(6)))
        assert calls == 1
        selected = OpenCodeProvider("secret", "future", 100, client=client)
        assert await selected._protocol() == "anthropic"
        await opencode_metadata(client, refresh=True)
        assert await selected._protocol() == "responses"
        assert opencode._METADATA_LOCKS == {}


def test_session_headers_are_stable_and_do_not_fingerprint_identical_prompts():
    first = OpenCodeProvider("secret", "model", 100)
    second = OpenCodeProvider("secret", "model", 100)
    prompt = CacheableSystemPrompt("same prompt", cache_scope="same-content-digest")
    first_id = first._headers(prompt)["x-opencode-session"]
    assert first._headers(prompt)["x-opencode-session"] == first_id
    assert second._headers(prompt)["x-opencode-session"] != first_id
    assert "same" not in first_id


async def test_public_metadata_size_is_bounded_and_response_closed(monkeypatch):
    monkeypatch.setattr(opencode, "_MAX_METADATA_BYTES", 64)
    responses = []
    def respond(request):
        response = httpx.Response(200, json=metadata({"future": {"name": "x" * 300}}))
        responses.append(response)
        return response
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await opencode_metadata(client) == {}
    assert responses[0].is_closed


async def test_quota_errors_never_fall_back_to_zen_and_do_not_expose_keys():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "Quota failed for test-secret", "type": "quota_error"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, provider="opencode-go", api_format="chat", client=client)
        events = [event async for event in selected.complete([])]
    assert len(requests) == 1 and requests[0].url.path == "/zen/go/v1/chat/completions"
    assert isinstance(events[-1], ProviderError) and "test-secret" not in events[-1].message


def streamed_chat_calls(calls, finish_reason, *, done=True):
    payload = {"id": "response", "object": "chat.completion.chunk", "created": 1, "model": "future",
               "choices": [{"index": 0, "delta": {"tool_calls": calls}, "finish_reason": finish_reason}]}
    return "data: " + json.dumps(payload) + "\n\n" + ("data: [DONE]\n\n" if done else "")


def call_delta(index=0, *, call_id="call-1", name="read_file", arguments='{"path":"README.md"}'):
    return {"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.parametrize("finish_reason", [None, "length", "stop", "content_filter"])
async def test_chat_never_releases_tool_calls_without_a_valid_tool_completion(finish_reason):
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text=streamed_chat_calls([call_delta()], finish_reason))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, provider="opencode-go", api_format="chat", client=client)
        events = [event async for event in selected.complete([])]
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)
    assert isinstance(events[-1], ProviderError)
    assert "OpenCode Go" in events[-1].message


@pytest.mark.parametrize("invalid", [call_delta(1, call_id="call-2", arguments="{"),
                                     call_delta(1, call_id="call-2", name=""),
                                     call_delta(1, call_id=""),
                                     call_delta(1),
                                     call_delta(1, call_id="call-2", arguments='{"value":NaN}')])
async def test_chat_invalid_sibling_discards_every_pending_tool(invalid):
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text=streamed_chat_calls([call_delta(), invalid], "tool_calls"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, api_format="chat", client=client)
        events = [event async for event in selected.complete([])]
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)
    assert isinstance(events[-1], ProviderError)


async def test_chat_transport_failure_after_finish_discards_buffered_tools():
    class BrokenStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield streamed_chat_calls([call_delta()], "tool_calls", done=False).encode()
            raise httpx.ReadError("interrupted test-secret")
        async def aclose(self):
            self.closed = True
    body = BrokenStream()
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, api_format="chat", client=client)
        events = [event async for event in selected.complete([])]
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)
    assert isinstance(events[-1], ProviderError) and "test-secret" not in events[-1].message
    assert body.closed


def streamed_messages_tool(stop_reason, *, terminal=True):
    events = [
        {"type": "message_start", "message": {"id": "msg1", "type": "message", "role": "assistant", "model": "future",
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 3}},
    ]
    if terminal:
        events.append({"type": "message_stop"})
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


@pytest.mark.parametrize("stop_reason,terminal,valid", [(None, True, False), ("max_tokens", True, False),
    ("end_turn", True, False), ("tool_use", False, False), ("tool_use", True, True)])
async def test_messages_wait_for_terminal_event_and_complete_tool_stop_reason(stop_reason, terminal, valid):
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text=streamed_messages_tool(stop_reason, terminal=terminal))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, provider="opencode-go", api_format="anthropic", client=client)
        events = [event async for event in selected.complete([])]
    ready = [event for event in events if isinstance(event, ToolCallReady)]
    assert bool(ready) is valid
    if valid:
        assert ready[0].input == {"path": "README.md"} and isinstance(events[-1], Done)
    else:
        assert isinstance(events[-1], ProviderError) and "OpenCode Go" in events[-1].message
        assert not any(isinstance(event, Done) for event in events)


async def test_messages_incomplete_tool_block_cannot_be_reported_as_a_successful_text_response():
    text = streamed_messages_tool("max_tokens").replace(
        'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n', ""
    )
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, api_format="anthropic", client=client)
        events = [event async for event in selected.complete([])]
    assert isinstance(events[-1], ProviderError)
    assert not any(isinstance(event, (ToolCallReady, Done)) for event in events)


@pytest.mark.parametrize("api_format", ["chat", "anthropic"])
@pytest.mark.parametrize("status", [401, 429])
async def test_sdk_auth_and_quota_failures_preserve_go_identity_without_key_leaks(api_format, status, capsys):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"type": "error", "error": {"type": "authentication_error", "message": "Rejected test-secret"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, provider="opencode-go", api_format=api_format, client=client)
        events = [event async for event in selected.complete([])]
    assert isinstance(events[-1], ProviderError) and "OpenCode Go" in events[-1].message
    assert "test-secret" not in events[-1].message
    assert len(requests) == 1 and requests[0].url.path.startswith("/zen/go/")
    captured = capsys.readouterr()
    assert "test-secret" not in captured.out + captured.err


@pytest.mark.parametrize("api_format", ["chat", "anthropic"])
async def test_sdk_requests_do_not_follow_redirects_or_close_borrowed_clients(api_format):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://foreign.invalid/collect"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        selected = OpenCodeProvider("test-secret", "future", 80, api_format=api_format, client=client)
        events = [event async for event in selected.complete([])]
        assert isinstance(events[-1], ProviderError)
        assert len(requests) == 1 and requests[0].url.host == "opencode.ai"
        assert not client.is_closed


@pytest.mark.parametrize("endpoint", ["file:///tmp/models", "https://user:secret@opencode.ai/zen/v1", "https://opencode.ai/zen/v1?key=secret", "http://bad\x00host"])
def test_endpoint_validation_prevents_credential_bearing_or_invalid_destinations(endpoint):
    with pytest.raises(ProviderConfigurationError):
        OpenCodeProvider("secret", "model", 100, base_url=endpoint)


class KeyStore:
    def __init__(self): self.calls = []
    def get_api_key(self, provider, env_var=None, **kwargs):
        self.calls.append((provider, env_var))
        return ApiKeyLookup("go-secret" if env_var == "OPENCODE_GO_API_KEY" else None, "environment")


async def test_catalog_keeps_live_ids_and_enriches_public_metadata_without_hardcoded_models(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, providers={**config.providers, "opencode-go": {"base_url": opencode.OPENCODE_BASE_URLS["opencode-go"], "default_model": "manual-model"}})
    store = KeyStore()
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.host == "models.dev":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=metadata({
                "future-model": {"name": "Future Model", "tool_call": True, "reasoning": True, "temperature": False,
                    "modalities": {"input": ["text", "image"], "output": ["text"]}, "limit": {"context": 250000, "output": 12000},
                    "reasoning_options": [{"type": "effort", "values": ["low", "high"]}], "cost": {"input": 2, "output": 8}},
                "retired-model": {"name": "Do not offer"},
            }, provider="opencode-go"))
        assert request.url.path == "/zen/go/v1/models" and request.headers["authorization"] == "Bearer go-secret"
        return httpx.Response(200, json={"data": [{"id": "future-model"}, {"id": "not-yet-described"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "go", api_key_store=store, client=client)
        again = await discover_models(config, "opencode-go", api_key_store=store, client=client)
    assert not result.error and again.source == "cache"
    assert {model.model for model in result.models} == {"future-model", "not-yet-described", "manual-model"}
    info = next(model for model in result.models if model.model == "future-model")
    assert info.provider == "opencode-go" and info.label == "Future Model"
    assert info.supports_tools and info.supports_vision and info.supports_reasoning
    assert info.supports_temperature is False and info.supported_reasoning_efforts == ("low", "high")
    assert info.context_window_tokens == 250000 and info.max_completion_tokens == 12000
    assert info.input_cost_per_token == 0.000002 and info.output_cost_per_token == 0.000008
    assert info.capability_source == "models.dev" and len(requests) == 2
    assert all(call == ("opencode-go", "OPENCODE_GO_API_KEY") for call in store.calls)


async def test_live_catalog_still_works_when_metadata_is_unavailable():
    def respond(request):
        return httpx.Response(503) if request.url.host == "models.dev" else httpx.Response(200, json={"data": [{"id": "new-id"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        rows = await discover_opencode_rows(client, "opencode", "https://opencode.ai/zen/v1", "")
    assert rows == [{"id": "new-id"}]


def test_catalog_preserves_explicit_empty_or_custom_auth_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    config = load_config()
    for env in ("", "CUSTOM_OPENCODE_KEY"):
        store = KeyStore()
        assert model_catalog._resolve_api_key(config, "opencode-go", {"api_key_env": env}, store) == ""
        assert store.calls == [("opencode-go", env)]
