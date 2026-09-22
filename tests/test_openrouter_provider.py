# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from libre_claw.core.session import ChatMessage, provider_reasoning_block, text_block, tool_use_block
from libre_claw.providers.base import CacheableSystemPrompt, Done, ProviderConfigurationError, ReasoningDelta, Usage
from libre_claw.providers.openrouter import (
    OPENROUTER_APP_TITLE,
    OPENROUTER_CATEGORIES,
    OPENROUTER_HTTP_REFERER,
    OpenRouterProvider,
)


class FakeClient:
    def __init__(self, chunks: list[object] | None = None) -> None:
        self.chat = FakeChat(chunks or [])


class FakeChat:
    def __init__(self, chunks: list[object]) -> None:
        self.completions = FakeCompletions(chunks)


class FakeCompletions:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.last_request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> object:
        self.last_request = request
        return FakeStream(self.chunks)


class FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> object:
        for chunk in self.chunks:
            yield chunk


def test_openrouter_provider_uses_openai_compatible_defaults() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key",
        model="openrouter/auto",
        max_tokens=99,
        client=client,
    )

    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.display_name == "OpenRouter"
    assert provider.default_headers == {
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
        "X-OpenRouter-Categories": OPENROUTER_CATEGORIES,
    }


def test_openrouter_provider_always_uses_libre_claw_attribution() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key",
        model="openrouter/auto",
        max_tokens=99,
        client=client,
    )

    assert provider.default_headers["HTTP-Referer"] == "https://libreclaw.sh"
    assert provider.default_headers["X-OpenRouter-Title"] == "Libre Claw"
    assert provider.default_headers["X-OpenRouter-Categories"] == "cli-agent,personal-agent"


async def test_openrouter_provider_requests_usage_accounting() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key",
        model="qwen/qwen3.7-max",
        max_tokens=99,
        client=client,
    )

    _ = [event async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Hi")])])]

    assert client.chat.completions.last_request is not None
    extra_body = client.chat.completions.last_request["extra_body"]
    assert extra_body["usage"] == {"include": True}
    assert extra_body["session_id"].startswith("libre-claw:")
    assert "cache_control" not in extra_body


async def test_openrouter_sticky_routing_stays_with_conversation() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(api_key="test-key", model="openrouter/auto", max_tokens=99, client=client)
    history = [ChatMessage(role="user", content=[text_block("first private task")])]
    _ = [event async for event in provider.complete(history, system="Stable system")]
    session_id = client.chat.completions.last_request["extra_body"]["session_id"]
    history.extend([
        ChatMessage(role="assistant", content=[text_block("Looking at it")]),
        ChatMessage(role="user", content=[text_block("Continue")]),
    ])
    _ = [
        event async for event in provider.complete(
            history, system="Updated checkpoint, plan, and memory", tools=[{"name": "read_file"}],
        )
    ]
    assert client.chat.completions.last_request["extra_body"]["session_id"] == session_id
    history[:] = [ChatMessage(role="user", content=[text_block("another task")])]
    _ = [event async for event in provider.complete(history, system="Stable system")]
    assert client.chat.completions.last_request["extra_body"]["session_id"] != session_id
    assert len(session_id) <= 256 and "private" not in session_id


@pytest.mark.parametrize("value", ["false", "true", "auto", 0, 1, {}, []])
def test_openrouter_rejects_non_boolean_prompt_caching(value: Any) -> None:
    with pytest.raises(ProviderConfigurationError, match="prompt_caching must be a boolean"):
        OpenRouterProvider(
            api_key="test-key", model="openrouter/auto", max_tokens=99, client=FakeClient(), prompt_caching=value,
        )


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5", "~anthropic/claude-sonnet-latest"])
async def test_openrouter_claude_enables_automatic_five_minute_caching(model: str) -> None:
    client = FakeClient()
    provider = OpenRouterProvider(api_key="test-key", model=model, max_tokens=99, client=client)
    history = [ChatMessage(role="user", content=[text_block("Hello")])]
    original = history[0].content.copy()
    _ = [event async for event in provider.complete(history)]
    request = client.chat.completions.last_request
    assert request["extra_body"]["cache_control"] == {"type": "ephemeral"}
    assert request["messages"] == [{"role": "user", "content": "Hello"}]
    assert history[0].content == original
    assert "prompt_cache_key" not in request["extra_body"]


async def test_openrouter_optional_session_scope_survives_compacted_history() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key", model="anthropic/claude-opus-5", max_tokens=99, client=client,
        cache_session_id="session-123",
    )
    for text in ("Original task", "Compacted task summary"):
        _ = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block(text)])])]
        assert client.chat.completions.last_request["extra_body"]["session_id"] == "session-123"


async def test_openrouter_cache_scope_survives_compaction_and_marks_only_stable_system_on_wire() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = openai.AsyncOpenAI(
            api_key="test-key", base_url="https://openrouter.ai/api/v1", http_client=http_client,
        )
        provider = OpenRouterProvider(api_key="test-key", model="anthropic/claude-opus-5", max_tokens=99, client=client)
        for suffix, history in [("Checkpoint: pending", "Original task"), ("Checkpoint: complete", "Compacted task")]:
            system = CacheableSystemPrompt("Stable instructions", suffix, cache_scope="private-conversation-scope")
            _ = [event async for event in provider.complete(
                [ChatMessage(role="user", content=[text_block(history)])], system=system,
            )]
            assert requests[-1]["messages"][0]["content"] == [
                {"type": "text", "text": "Stable instructions", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"\n\n{suffix}"},
            ]
            assert requests[-1]["cache_control"] == {"type": "ephemeral"}
            assert "private-conversation-scope" not in json.dumps(requests[-1])
            assert str(system) == f"Stable instructions\n\n{suffix}"

    assert requests[0]["session_id"] == requests[1]["session_id"]


async def test_openrouter_explicit_session_id_takes_priority_over_system_cache_scope() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key", model="openrouter/auto", max_tokens=99, client=client, cache_session_id="explicit-session",
    )
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])],
        system=CacheableSystemPrompt("Stable", "Dynamic", cache_scope="inferred-session"),
    )]
    request = client.chat.completions.last_request
    assert request["extra_body"]["session_id"] == "explicit-session"
    assert request["messages"][0]["content"] == "Stable\n\nDynamic"


@pytest.mark.parametrize("settings", [{"prompt_caching": False}, {"base_url": "https://proxy.example/v1"}])
async def test_openrouter_stable_system_metadata_is_plain_text_without_native_caching(settings: dict[str, Any]) -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key", model="anthropic/claude-opus-5", max_tokens=99, client=client, **settings,
    )
    _ = [event async for event in provider.complete(
        [ChatMessage(role="user", content=[text_block("Hello")])],
        system=CacheableSystemPrompt("Stable", "Fresh checkpoint", cache_scope="session"),
    )]
    request = client.chat.completions.last_request
    assert request["messages"][0]["content"] == "Stable\n\nFresh checkpoint"
    assert "cache_control" not in json.dumps(request)


@pytest.mark.parametrize("settings", [
    {"prompt_caching": False},
    {"base_url": "https://private-proxy.example/api/v1"},
])
async def test_openrouter_cache_controls_are_optional_and_native_only(settings: dict[str, Any]) -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key", model="anthropic/claude-opus-5", max_tokens=99, client=client, **settings,
    )
    _ = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert client.chat.completions.last_request["extra_body"] == {"usage": {"include": True}}


async def test_openrouter_reports_cache_reads_and_writes_from_usage() -> None:
    client = FakeClient([{
        "choices": [],
        "usage": {
            "prompt_tokens": 6000,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 2000},
            "cost": 0.012,
        },
    }])
    provider = OpenRouterProvider(api_key="test-key", model="anthropic/claude-opus-5", max_tokens=99, client=client)
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[text_block("Hello")])])]
    assert events == [Done(usage=Usage(
        input_tokens=6000, output_tokens=30, cached_tokens=4000, cache_write_tokens=2000, cost=0.012,
    ))]


async def test_openrouter_opus_5_uses_supported_request_parameters() -> None:
    client = FakeClient()
    provider = OpenRouterProvider(
        api_key="test-key",
        model="anthropic/claude-opus-5",
        max_tokens=65_536,
        client=client,
    )

    events = [
        event
        async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Hi")])])
    ]

    assert events == [Done(usage=None, stop_reason=None)]
    assert client.chat.completions.last_request is not None
    assert client.chat.completions.last_request["max_tokens"] == 65_536
    assert "max_completion_tokens" not in client.chat.completions.last_request
    assert "temperature" not in client.chat.completions.last_request


async def test_openrouter_round_trips_structured_reasoning_details() -> None:
    first_detail = {
        "type": "reasoning.text",
        "text": "Inspect the repository.",
        "signature": None,
        "id": "reasoning-1",
        "format": "anthropic-claude-v1",
        "index": 0,
    }
    second_detail = {
        "type": "reasoning.encrypted",
        "data": "encrypted",
        "id": "reasoning-2",
        "format": "anthropic-claude-v1",
        "index": 1,
    }
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(reasoning_details=[first_detail]),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(reasoning_details=[SimpleNamespace(**second_detail)]),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        ),
    ]
    client = FakeClient(chunks)
    provider = OpenRouterProvider(
        api_key="test-key",
        model="anthropic/claude-opus-5",
        max_tokens=65_536,
        client=client,
    )

    events = [
        event
        async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("Inspect")])])
    ]
    reasoning_events = [event for event in events if isinstance(event, ReasoningDelta)]
    serialized_reasoning = "".join(event.text for event in reasoning_events)

    replay_client = FakeClient()
    replay_provider = OpenRouterProvider(
        api_key="test-key",
        model="anthropic/claude-opus-5",
        max_tokens=65_536,
        client=replay_client,
    )
    _ = [
        event
        async for event in replay_provider.complete(
            messages=[
                ChatMessage(
                    role="assistant",
                    content=[
                        provider_reasoning_block(serialized_reasoning, "openrouter"),
                        tool_use_block("call_1", "read_file", {"path": "README.md"}),
                    ],
                )
            ]
        )
    ]

    assert [event.provider for event in reasoning_events] == ["openrouter", "openrouter"]
    assert replay_client.chat.completions.last_request is not None
    assert replay_client.chat.completions.last_request["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "reasoning_details": [first_detail, second_detail],
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
