# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from libre_claw.core.session import ChatMessage, provider_reasoning_block, text_block, tool_use_block
from libre_claw.providers.base import Done, ReasoningDelta
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
    assert client.chat.completions.last_request["extra_body"] == {"usage": {"include": True}}


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
