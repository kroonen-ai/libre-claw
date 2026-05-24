# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from libre_claw.config import load_config
from libre_claw.core.session import ChatMessage
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolCallReady, ToolSchema
from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
)


class FakeProvider(LLMProvider):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, system, stream, temperature, max_tokens
        yield TextDelta("hi")
        yield Done()


class FakeToolProvider(LLMProvider):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del tools, system, stream, temperature, max_tokens
        if len(messages) == 1:
            yield ToolCallReady("toolu_1", "write_file", {"path": "telegram.txt", "content": "hello"})
            yield Done(stop_reason="tool_use")
            return
        yield TextDelta("done")
        yield Done()


def test_telegram_auth_allowlist() -> None:
    auth = TelegramAuth(allowed_user_ids=frozenset({123}))

    assert auth.is_allowed(123) is True
    assert auth.is_allowed(456) is False
    assert auth.is_allowed(None) is False


async def test_telegram_bridge_streams_text(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda config: FakeProvider())

    events = [event async for event in bridge.stream_message(1, "hello")]

    assert events == [TelegramText("hi"), TelegramDone(None)]


async def test_telegram_bridge_prompts_and_resolves_permission(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda config: FakeToolProvider())

    events: list[object] = []
    async for event in bridge.stream_message(1, "read"):
        events.append(event)
        if isinstance(event, TelegramPermissionPrompt):
            assert bridge.resolve_permission(event.prompt_id, "deny") is True

    assert any(isinstance(event, TelegramToolNotice) for event in events)
    assert any(isinstance(event, TelegramPermissionPrompt) for event in events)
    assert isinstance(events[-1], TelegramDone)
