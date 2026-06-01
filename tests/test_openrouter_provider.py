# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from libre_claw.core.session import ChatMessage, text_block
from libre_claw.providers.openrouter import (
    OPENROUTER_APP_TITLE,
    OPENROUTER_CATEGORIES,
    OPENROUTER_HTTP_REFERER,
    OpenRouterProvider,
)


class FakeClient:
    def __init__(self) -> None:
        self.chat = FakeChat()


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeCompletions()


class FakeCompletions:
    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> object:
        self.last_request = request
        return FakeStream()


class FakeStream:
    async def __aiter__(self) -> object:
        if False:
            yield object()


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
