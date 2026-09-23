# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from libre_claw.config import load_config
from libre_claw.core.cordis_llm import CordisLlmBridge, CordisLlmError
from libre_claw.providers.base import (
    Done, ProviderError, ReasoningDelta, TextDelta, ToolCallDelta,
    ToolCallReady, ToolCallStart, Usage,
)
from libre_claw.providers.model_catalog import ModelCatalog, ModelInfo


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    original = load_config()
    return replace(original, providers={
        "deepseek": {"default_model": "future-model", "reasoning_effort": "high", "api_key": "never-export-key"},
        "codex": {"default_model": "coding-agent"},
        "untrusted": {"base_url": "https://private.example"},
    })


def request(**updates: Any) -> dict[str, Any]:
    return {"provider": "deepseek", "model": "future-model", "messages": [
        {"id": "u1", "role": "user", "content": [{"type": "text", "text": "Hello"}], "source": {"kind": "user"}},
    ], **updates}


class Harness:
    def __init__(self, config, events=()):
        self.config = config
        self.events = events
        self.discovery_calls = []
        self.factory_calls = []
        self.requests = []
        self.closed = False
        self.granted = True
        self.info = ModelInfo("deepseek", "future-model", "Future Model", context_window_tokens=65536,
                              max_completion_tokens=8192, supported_reasoning_efforts=("low", "high"))
        self.bridge = CordisLlmBridge(config, authorize=lambda: self.granted,
                                     provider_factory=self.factory, model_discovery=self.discover)

    async def discover(self, config, provider):
        self.discovery_calls.append(provider)
        return ModelCatalog((self.info,), "fake", error="never-export-error-key")

    def factory(self, config, **kwargs):
        self.factory_calls.append((config, kwargs))
        return self

    async def complete(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        try:
            for event in self.events:
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            self.closed = True


async def test_all_operations_deny_by_default_before_any_provider_work(config):
    bridge = CordisLlmBridge(config)
    for operation in (bridge.list_providers(), bridge.list_configurable_providers(),
                      bridge.list_models("deepseek"), bridge.resolve_model_info("deepseek", "future-model")):
        with pytest.raises(PermissionError, match="not been granted"):
            await operation
    with pytest.raises(PermissionError, match="not been granted"):
        await anext(bridge.stream(request()))


async def test_provider_directory_is_metadata_only_and_excludes_coding_agent(config):
    harness = Harness(config)
    assert await harness.bridge.list_providers() == [{"id": "deepseek", "name": "DeepSeek"}]
    assert await harness.bridge.list_configurable_providers() == [
        {"id": "deepseek", "name": "DeepSeek", "settingsNs": "providers.deepseek"},
    ]
    assert harness.discovery_calls == harness.factory_calls == []


async def test_dynamic_model_metadata_matches_native_provider_consumption(config):
    harness = Harness(config)
    assert await harness.bridge.list_models("deepseek") == [{"id": "future-model", "name": "Future Model"}]
    assert await harness.bridge.resolve_model_info("deepseek", "future-model") == {
        "id": "future-model", "name": "Future Model", "context": {"contextWindow": 65536},
        "defaultMaxTokens": 8192, "reasoning": {
            "efforts": [{"id": "low", "name": "low"}, {"id": "high", "name": "high"}],
            "defaultEffort": "high",
        },
    }
    assert harness.factory_calls == []


async def test_native_catalog_respects_configured_output_default(config):
    config = replace(config, providers={"deepseek": {**config.providers["deepseek"], "max_tokens": 512}})
    harness = Harness(config)
    assert (await harness.bridge.resolve_model_info("deepseek", "future-model"))["defaultMaxTokens"] == 512


async def test_request_conversion_preserves_system_tools_reasoning_and_error_results(config):
    harness = Harness(config, [TextDelta("Hello "), TextDelta("there"), Done(Usage(100, 20, 60, 4, cache_write_tokens=10), "stop")])
    payload = request(system="Explicit only", temperature=0.2, maxTokens=55, reasoningEffort="low", tools=[
        {"name": "lookup", "description": "Lookup data", "parameters": {"type": "object"}},
    ], messages=[
        {"id": "s", "role": "system", "content": [{"type": "text", "text": "Leading instructions"}], "source": {"kind": "plugin", "plugin": "example"}},
        request()["messages"][0],
        {"id": "a", "role": "assistant", "source": {"kind": "model", "provider": "deepseek", "model": "future-model"}, "content": [
            {"type": "reasoning", "text": "Saved thought"},
            {"type": "tool-call", "id": "call", "name": "lookup", "arguments": '{"q":"a"}'},
        ]},
        {"id": "t", "role": "user", "source": {"kind": "tool", "callId": "call"}, "content": [
            {"type": "tool-result", "toolCallId": "call", "content": [{"type": "text", "text": "No result"}], "isError": True},
        ]},
    ])
    result = [chunk async for chunk in harness.bridge.stream(payload)]
    assert result == [
        {"type": "block-start", "index": 0, "blockType": "text"},
        {"type": "text-delta", "index": 0, "text": "Hello "},
        {"type": "text-delta", "index": 0, "text": "there"},
        {"type": "block-end", "index": 0, "block": {"type": "text", "text": "Hello there"}},
        {"type": "usage", "usage": {"inputTokens": 30, "outputTokens": 20, "cacheReadTokens": 60,
                                    "cacheWriteTokens": 10, "reasoningTokens": 4, "totalTokens": 120}},
        {"type": "finish", "reason": {"kind": "stop"}},
    ]
    actual_config, factory_kwargs = harness.factory_calls[0]
    assert actual_config.general.default_provider == "deepseek"
    assert actual_config.general.default_model == "future-model"
    assert actual_config.providers["deepseek"]["reasoning_effort"] == "low"
    assert actual_config.providers["deepseek"]["max_tokens"] == 55
    assert actual_config.agent.system_prompt == actual_config.agent.system_prompt_extra == ""
    assert config.providers["deepseek"]["reasoning_effort"] == "high"
    assert factory_kwargs == {"provider_name": "deepseek", "model": "future-model"}
    messages, kwargs = harness.requests[0]
    assert messages[1].content == [
        {"type": "provider_reasoning", "provider": "deepseek", "text": "Saved thought"},
        {"type": "tool_use", "id": "call", "name": "lookup", "input": {"q": "a"}},
    ]
    assert messages[2].content == [{"type": "tool_result", "tool_use_id": "call", "content": "No result", "is_error": True}]
    assert kwargs["system"] == "Explicit only\n\nLeading instructions"
    assert kwargs["tools"] == [{"name": "lookup", "description": "Lookup data", "input_schema": {"type": "object"}}]
    assert kwargs["temperature"] == 0.2 and kwargs["max_tokens"] == 55
    assert harness.closed


async def test_streamed_tool_arguments_match_committed_value_and_replay_roundtrips(config):
    harness = Harness(config, [ReasoningDelta("Thought", "deepseek"),
                               ToolCallStart("c", "lookup"), ToolCallDelta("c", "lookup", '{"q":'),
                               ToolCallDelta("c", "lookup", '"a"}'), ToolCallReady("c", "lookup", {"q": "a"}),
                               Done(stop_reason="tool_calls")])
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert [chunk["block"] for chunk in result if chunk["type"] == "block-end"] == [
        {"type": "reasoning", "text": "Thought"},
        {"type": "tool-call", "id": "c", "name": "lookup", "arguments": '{"q":"a"}'},
    ]
    assert result[-1]["reason"] == {"kind": "tool-calls"}
    replay = result[-1]["replayState"]
    harness.events = [Done(stop_reason="stop")]
    assistant = {"id": "a", "role": "assistant", "source": {
        "kind": "model", "provider": "deepseek", "model": "future-model", "replayState": replay,
    }, "content": [{"type": "reasoning", "text": "Thought"}]}
    _ = [chunk async for chunk in harness.bridge.stream(request(messages=[request()["messages"][0], assistant]))]
    assert harness.requests[-1][0][1].content == [{"type": "provider_reasoning", "provider": "deepseek", "text": "Thought"}]


async def test_ready_without_deltas_produces_complete_tool_protocol(config):
    harness = Harness(config, [ToolCallReady("c", "lookup", {}), Done(stop_reason="tool_use")])
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert result[0] == {"type": "block-start", "index": 0, "blockType": "tool-call"}
    assert result[-2]["block"]["arguments"] == "{}"
    assert result[-1]["reason"]["kind"] == "tool-calls"


@pytest.mark.parametrize(("provider", "opaque", "visible"), [
    ("anthropic", {"type": "anthropic_message_content", "version": 1, "content": [
        {"type": "thinking", "thinking": "Readable thought", "signature": "opaque-signature"},
        {"type": "text", "text": "Do not duplicate the answer"},
    ]}, "Readable thought"),
    ("opencode-go:responses", {"type": "responses_output_items", "version": 1, "output": [
        {"type": "reasoning", "encrypted_content": "opaque-replay", "summary": [{"type": "summary_text", "text": "Summary"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "Answer"}]},
    ]}, "Summary"),
    ("openrouter", {"type": "reasoning.text", "text": "Provider thought", "signature": "opaque"}, "Provider thought"),
])
async def test_provider_reasoning_envelopes_have_readable_deltas_and_lossless_replay(config, provider, opaque, visible):
    encoded = json.dumps(opaque)
    harness = Harness(config, [ReasoningDelta(encoded, provider), Done()])
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert [chunk["text"] for chunk in result if chunk["type"] == "reasoning-delta"] == [visible]
    assert result[-1]["replayState"]["response"]["libreClawReasoning"]["blocks"] == [{"provider": provider, "text": encoded}]


@pytest.mark.parametrize("events", [
    [ProviderError("Authorization: Bearer never-export-key")],
    [RuntimeError("https://user:never-export-key@provider.invalid")],
    [TextDelta("Partial")],
    [ToolCallStart("c", "lookup"), Done(stop_reason="tool_calls")],
    [ToolCallDelta("c", "lookup", '{"q":1}'), ToolCallReady("c", "lookup", {"q": 2})],
    [Done(Usage(2, 1, cached_tokens=3))],
    [Done(stop_reason="content_filter")],
])
async def test_failed_or_incomplete_provider_output_never_becomes_success(config, events):
    harness = Harness(config, events)
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert result[-1]["reason"]["kind"] == "error"
    assert "never-export-key" not in repr(result)
    assert harness.closed


@pytest.mark.parametrize("updates", [
    {"stop": ["END"]}, {"temperature": True}, {"temperature": float("nan")},
    {"maxTokens": 0}, {"maxTokens": True}, {"messages": [] , "apiKey": "secret"},
    {"provider": "codex"}, {"provider": "untrusted"}, {"provider": "https://attacker.invalid"},
    {"tools": [{"name": "x", "description": "", "parameters": {}}] * 2},
    {"messages": [request()["messages"][0]] * 2},
])
async def test_invalid_requests_fail_before_provider_or_discovery(config, updates):
    harness = Harness(config)
    with pytest.raises((CordisLlmError, PermissionError)):
        _ = [chunk async for chunk in harness.bridge.stream(request(**updates))]
    assert harness.factory_calls == harness.discovery_calls == []


@pytest.mark.parametrize("block", [
    {"type": "image", "attachment": {"path": "/private/file"}},
    {"type": "file", "attachment": {"path": "/private/file"}},
    {"type": "reasoning", "text": "User cannot set provider-private state"},
    {"type": "tool-result", "toolCallId": "missing", "content": []},
])
async def test_attachment_capabilities_and_uncorrelated_history_are_rejected(config, block):
    harness = Harness(config)
    payload = request()
    payload["messages"][0]["content"] = [block]
    with pytest.raises(CordisLlmError):
        _ = [chunk async for chunk in harness.bridge.stream(payload)]
    assert harness.factory_calls == harness.discovery_calls == []


async def test_unknown_model_cannot_create_a_provider(config):
    harness = Harness(config)
    result = [chunk async for chunk in harness.bridge.stream(request(model="unlisted"))]
    assert result[-1]["reason"]["kind"] == "error"
    assert harness.factory_calls == []


async def test_unadvertised_reasoning_effort_never_reaches_provider(config):
    harness = Harness(config)
    result = [chunk async for chunk in harness.bridge.stream(request(reasoningEffort="invented"))]
    assert result[-1]["reason"]["kind"] == "error"
    assert harness.factory_calls == []


async def test_max_tokens_finish_retains_exact_usage(config):
    harness = Harness(config, [TextDelta("Limited"), Done(Usage(5, 2), "length")])
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert result[-1] == {"type": "finish", "reason": {"kind": "max-tokens"}}
    assert result[-2]["usage"]["inputTokens"] == 5


async def test_foreign_replay_is_rejected_instead_of_silently_discarded(config):
    harness = Harness(config)
    message = {"id": "a", "role": "assistant", "content": [], "source": {
        "kind": "model", "provider": "deepseek", "model": "future-model",
        "replayState": {"response": {"otherAdapterState": "opaque"}},
    }}
    with pytest.raises(CordisLlmError, match="replay state"):
        _ = [chunk async for chunk in harness.bridge.stream(request(messages=[message]))]
    assert harness.factory_calls == harness.discovery_calls == []


async def test_request_is_detached_before_async_catalog_lookup(config):
    harness = Harness(config, [Done()])
    entered, resume = asyncio.Event(), asyncio.Event()
    async def discover(config, provider):
        entered.set()
        await resume.wait()
        return ModelCatalog((harness.info,), "fake")
    harness.bridge._model_discovery = discover
    payload = request()
    stream = harness.bridge.stream(payload)
    task = asyncio.create_task(anext(stream))
    await entered.wait()
    payload["messages"][0]["content"][0]["text"] = "Changed after authorization"
    resume.set()
    await task
    await stream.aclose()
    assert harness.requests[0][0][0].content[0]["text"] == "Hello"


async def test_cancellation_closes_provider_stream_and_does_not_retry(config):
    harness = Harness(config)
    entered = asyncio.Event()
    async def complete(*args, **kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
            yield Done()
        finally:
            harness.closed = True
    harness.complete = complete
    stream = harness.bridge.stream(request())
    task = asyncio.create_task(anext(stream))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert harness.closed
    assert len(harness.factory_calls) == 1


async def test_consumer_close_and_revocation_close_provider(config):
    harness = Harness(config, [TextDelta("One"), TextDelta("Two"), Done()])
    stream = harness.bridge.stream(request())
    assert (await anext(stream))["type"] == "block-start"
    await stream.aclose()
    assert harness.closed
    harness.closed = False
    stream = harness.bridge.stream(request())
    await anext(stream)
    await anext(stream)
    harness.granted = False
    with pytest.raises(PermissionError):
        await anext(stream)
    assert harness.closed


async def test_catalog_and_factory_errors_are_redacted(config):
    harness = Harness(config)
    async def discover(*args):
        raise RuntimeError("never-export-key")
    harness.bridge._model_discovery = discover
    with pytest.raises(CordisLlmError, match="catalog is unavailable") as error:
        await harness.bridge.list_models("deepseek")
    assert "never-export-key" not in str(error.value)
    harness.bridge._model_discovery = harness.discover
    def factory(*args, **kwargs):
        raise RuntimeError("never-export-key")
    harness.bridge._provider_factory = factory
    result = [chunk async for chunk in harness.bridge.stream(request())]
    assert result[-1]["reason"]["kind"] == "error"
    assert "never-export-key" not in repr(result)


async def test_json_depth_and_unicode_bounds_reject_before_io(config):
    harness = Harness(config)
    nested: Any = {}
    for _ in range(34):
        nested = {"nested": nested}
    for value in (nested, {"text": "\ud800"}):
        with pytest.raises(CordisLlmError):
            _ = [chunk async for chunk in harness.bridge.stream(value)]
    assert harness.factory_calls == harness.discovery_calls == []
