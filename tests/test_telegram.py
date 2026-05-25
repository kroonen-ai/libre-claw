# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

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
    system_prompts: list[str | None] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, stream, temperature, max_tokens
        self.system_prompts.append(system)
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


class FakeDaemonClient:
    def __init__(self) -> None:
        self.resolutions: list[tuple[str, str, str]] = []
        self._events_served = False

    async def start_run(self, message: str, **payload: Any) -> dict[str, Any]:
        del message, payload
        return {"run": {"run_id": "run-1", "state": "queued"}}

    async def get_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        del run_id, after
        if self._events_served:
            return {"events": []}
        self._events_served = True
        return {
            "events": [
                {"event_id": 1, "type": "assistant_delta", "data": {"text": "hi"}},
                {
                    "event_id": 2,
                    "type": "permission_request",
                    "data": {"tool_call_id": "toolu_1", "name": "bash", "arguments": {"command": "date"}},
                },
                {"event_id": 3, "type": "run_finished", "data": {"state": "done"}},
            ]
        }

    async def get_run(self, run_id: str) -> dict[str, Any]:
        del run_id
        return {"run": {"run_id": "run-1", "state": "done"}}

    async def resolve_permission(self, run_id: str, tool_call_id: str, resolution: str) -> dict[str, Any]:
        self.resolutions.append((run_id, tool_call_id, resolution))
        return {"run_id": run_id, "tool_call_id": tool_call_id, "resolution": resolution}


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


async def test_telegram_bridge_injects_skills(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    skill_path = tmp_path / ".libre-claw" / "skills" / "pytest-debug.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Pytest Debug\n\nUse for pytest failures.", encoding="utf-8")
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()
    provider = FakeProvider()
    provider.system_prompts.clear()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda config: provider)

    events = [event async for event in bridge.stream_message(1, "debug pytest")]

    assert events == [TelegramText("hi"), TelegramDone(None)]
    assert provider.system_prompts
    assert provider.system_prompts[0] is not None
    assert "Skill: Pytest Debug" in provider.system_prompts[0]


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


async def test_telegram_bridge_can_use_daemon_runs_for_approvals(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient()
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    await bridge.initialize()

    events = [event async for event in bridge.stream_message(1, "hello")]
    prompt = next(event for event in events if isinstance(event, TelegramPermissionPrompt))
    resolved = await bridge.resolve_permission_async(prompt.prompt_id, "allow_once")

    assert any(isinstance(event, TelegramText) and event.text == "hi" for event in events)
    assert prompt.prompt_id == "daemon:run-1:toolu_1"
    assert resolved is True
    assert daemon.resolutions == [("run-1", "toolu_1", "allow_once")]
