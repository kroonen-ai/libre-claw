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
from libre_claw.telegram.bot import TelegramBot
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
)
from libre_claw.telegram.handlers import (
    _finish_text_response,
    _message_chunks,
    _reply_text_chunks,
    _stream_preview,
    _telegram_help_text,
    _unauthorized_text,
    telegram_command_specs,
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
    def __init__(self, *, with_permission: bool = True) -> None:
        self.resolutions: list[tuple[str, str, str]] = []
        self.start_payloads: list[dict[str, Any]] = []
        self.with_permission = with_permission
        self._events_served: set[str] = set()
        self._run_count = 0

    async def start_run(self, message: str, **payload: Any) -> dict[str, Any]:
        self._run_count += 1
        self.start_payloads.append({"message": message, **payload})
        return {"run": {"run_id": f"run-{self._run_count}", "state": "queued"}}

    async def get_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        del after
        if run_id in self._events_served:
            return {"events": []}
        self._events_served.add(run_id)
        text = "hi" if run_id == "run-1" else "again"
        events = [{"event_id": 1, "type": "assistant_delta", "data": {"text": text}}]
        if self.with_permission:
            events.append(
                {
                    "event_id": 2,
                    "type": "permission_request",
                    "data": {"tool_call_id": "toolu_1", "name": "bash", "arguments": {"command": "date"}},
                }
            )
        events.append({"event_id": 3, "type": "usage", "data": {"input_tokens": 3, "output_tokens": 2, "cost": 0.001}})
        events.append({"event_id": 4, "type": "run_finished", "data": {"state": "done"}})
        return {"events": events}

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return {"run": {"run_id": run_id, "state": "done"}}

    async def resolve_permission(self, run_id: str, tool_call_id: str, resolution: str) -> dict[str, Any]:
        self.resolutions.append((run_id, tool_call_id, resolution))
        return {"run_id": run_id, "tool_call_id": tool_call_id, "resolution": resolution}


def test_telegram_auth_allowlist() -> None:
    auth = TelegramAuth(allowed_user_ids=frozenset({123}))

    assert auth.is_allowed(123) is True
    assert auth.is_allowed(456) is False
    assert auth.is_allowed(None) is False


def test_telegram_unauthorized_text_shows_numeric_user_id() -> None:
    text = _unauthorized_text(8720905071, "rob_x_ai")

    assert "@rob_x_ai" in text
    assert "8720905071" in text
    assert "libre-claw telegram allow 8720905071" in text


def test_telegram_help_text_lists_slash_commands() -> None:
    text = _telegram_help_text()

    assert "/help" in text
    assert "/start" in text
    assert "/model <name>" in text
    assert "/provider anthropic|openai|openrouter|ollama|codex" in text
    assert "Send a normal message" in text


def test_telegram_message_chunks_respect_config_and_hard_limits() -> None:
    chunks = _message_chunks("a" * 4500, configured_limit=5000)

    assert len(chunks) == 2
    assert all(0 < len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == "a" * 4500


def test_telegram_message_chunks_preserve_boundary_whitespace() -> None:
    text = ("word " * 1000) + "\nnext"
    chunks = _message_chunks(text, configured_limit=120)

    assert all(0 < len(chunk) <= 120 for chunk in chunks)
    assert "".join(chunks) == text


def test_telegram_stream_preview_keeps_live_edits_under_safe_limit() -> None:
    preview = _stream_preview("a" * 4500, configured_limit=5000)

    assert len(preview) < 4096
    assert preview.endswith("...[continued]")


async def test_telegram_finish_text_response_sends_all_final_chunks() -> None:
    class EditableMessage:
        def __init__(self) -> None:
            self.edits: list[str] = []

        async def edit_text(self, text: str) -> None:
            self.edits.append(text)

    class ReplyMessage:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.replies.append(text)

    placeholder = EditableMessage()
    reply_to = ReplyMessage()
    text = "a" * 4200

    await _finish_text_response(placeholder, reply_to, text, configured_limit=5000)

    assert len(placeholder.edits) == 1
    assert len(placeholder.edits[0]) < 4096
    assert reply_to.replies
    assert placeholder.edits[0] + "".join(reply_to.replies) == text


async def test_telegram_reply_text_chunks_retries_smaller_on_telegram_limit() -> None:
    class BadRequest(Exception):
        pass

    class ReplyMessage:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            if len(text) > 5:
                raise BadRequest("Message is too long")
            self.replies.append(text)

    message = ReplyMessage()

    await _reply_text_chunks(message, "abcdefghij", configured_limit=10)

    assert message.replies == ["abcde", "fghij"]


def test_telegram_command_specs_drive_bot_menu() -> None:
    commands = dict(telegram_command_specs())

    assert commands["help"] == "Show Telegram slash commands"
    assert "start" in commands
    assert "schedule" in commands


def test_telegram_bot_reads_secure_stored_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class Lookup:
        value = "stored-telegram-token"

    class Store:
        def get_api_key(self, provider: str, env_var: str | None = None) -> Lookup:
            assert provider == "telegram"
            assert env_var == "TELEGRAM_BOT_TOKEN"
            return Lookup()

    monkeypatch.setattr("libre_claw.telegram.bot.ApiKeyStore.from_config", lambda config: Store())

    assert TelegramBot(load_config())._bot_token() == "stored-telegram-token"


async def test_telegram_bot_run_uses_polling_lifecycle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    class FakeBridge:
        async def initialize(self) -> None:
            calls.append("bridge_initialize")

    class FakeUpdater:
        async def start_polling(self) -> None:
            calls.append("start_polling")

        async def stop(self) -> None:
            calls.append("stop_polling")

    class FakeBot:
        async def set_my_commands(self, commands: list[object]) -> None:
            assert len(commands) >= 8
            calls.append("set_my_commands")

    class FakeApplication:
        def __init__(self) -> None:
            self.updater = FakeUpdater()
            self.bot = FakeBot()

        def add_handler(self, handler: object) -> None:
            del handler
            calls.append("add_handler")

        async def initialize(self) -> None:
            calls.append("initialize")

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

        async def shutdown(self) -> None:
            calls.append("shutdown")

    fake_application = FakeApplication()

    class FakeBuilder:
        def token(self, token: str) -> FakeBuilder:
            assert token == "test-token"
            calls.append("token")
            return self

        def build(self) -> FakeApplication:
            calls.append("build")
            return fake_application

    class FakeApplicationFactory:
        @staticmethod
        def builder() -> FakeBuilder:
            calls.append("builder")
            return FakeBuilder()

    monkeypatch.setattr("libre_claw.telegram.bot.Application", FakeApplicationFactory)
    bot = TelegramBot(load_config(), bridge=FakeBridge())  # type: ignore[arg-type]

    async def stop_after_polling_starts() -> None:
        calls.append("wait")

    monkeypatch.setattr(bot, "_wait_until_stopped", stop_after_polling_starts)

    await bot.run()

    assert "wait_until_closed" not in calls
    assert calls[:5] == ["bridge_initialize", "builder", "token", "build", "add_handler"]
    assert calls[-5:] == ["start_polling", "wait", "stop_polling", "stop", "shutdown"]


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


async def test_telegram_daemon_bridge_preserves_chat_session_between_messages(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient(with_permission=False)
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    await bridge.initialize()

    first = [event async for event in bridge.stream_message(1, "hello")]
    second = [event async for event in bridge.stream_message(1, "follow up")]

    second_session = daemon.start_payloads[1]["session"]
    messages = second_session["messages"]
    assert daemon.start_payloads[0]["session"]["messages"] == []
    assert [[block["text"] for block in message["content"]] for message in messages] == [["hello"], ["hi"]]
    assert [message.role for message in bridge.state_for(1).session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert any(isinstance(event, TelegramText) and event.text == "hi" for event in first)
    assert any(isinstance(event, TelegramText) and event.text == "again" for event in second)
    assert "10 total" in bridge.status_text(1)


async def test_telegram_bridge_schedule_command_creates_telegram_route(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()

    examples = await bridge.schedule_text(42, "examples")
    created = await bridge.schedule_text(42, "add daily 08:30 | Morning brief | Summarize priorities")
    listed = await bridge.schedule_text(42, "list")
    automation = (await bridge.automation_store.list())[0]

    assert "Morning brief" in examples
    assert "Scheduled:" in created
    assert automation.route == "telegram"
    assert automation.telegram_chat_id == 42
    assert automation.automation_id in listed
