# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from libre_claw.config import load_config
from libre_claw.core.session import ChatMessage
from libre_claw.core.session import UserAttachment
from libre_claw.core.tools import ToolCall
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolCallReady, ToolSchema
from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bot import TelegramBot
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
    _tool_call_notice,
    _tool_result_notice,
)
from libre_claw.telegram.handlers import (
    TELEGRAM_MODEL_PRESETS,
    TelegramExpandablePayload,
    TelegramHandlers,
    _cancel_task,
    _finish_text_response,
    _message_chunks,
    _model_configuration_text,
    _model_keyboard,
    _provider_keyboard,
    _replace_general,
    _reply_text_chunks,
    _spawn_lifecycle_restart,
    _safe_edit_text_preview,
    _stream_preview,
    _telegram_image_attachments,
    _looks_like_file_send_request,
    _telegram_help_text,
    _telegram_document_paths_from_text,
    _tool_log_formatted_chunk,
    _tool_log_preview,
    _typing_indicator_loop,
    _unauthorized_text,
    _visible_tool_notices,
    telegram_command_specs,
)
from libre_claw.telegram.formatting import (
    clean_final_answer_for_telegram,
    markdown_to_telegram_html,
    telegram_html_chunks,
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
        self.base_url = "http://127.0.0.1:8766"
        self.resolutions: list[tuple[str, str, str]] = []
        self.start_payloads: list[dict[str, Any]] = []
        self.model_updates: list[tuple[str, str, bool]] = []
        self.model_payload: dict[str, Any] = {
            "provider": "openrouter",
            "model": "deepseek/deepseek-v4-flash",
        }
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
        return {
            "run": {
                "run_id": run_id,
                "state": "done",
                "title": "Fix the thing",
                "provider": "openrouter",
                "model": "deepseek/deepseek-v4-flash",
                "updated_at": "2026-05-30T10:00:00",
                "path": "/tmp/run",
            },
            "artifacts": {"summary.md": {"exists": True}},
        }

    async def list_runs(self, limit: int = 20) -> dict[str, Any]:
        assert limit > 0
        return {
            "runs": [
                {
                    "run_id": "run-20260530-abc123",
                    "state": "done",
                    "title": "Fix the thing",
                    "provider": "openrouter",
                    "model": "deepseek/deepseek-v4-flash",
                }
            ]
        }

    async def usage(self, provider: str = "", limit: int = 250) -> dict[str, Any]:
        del limit
        return {"text": f"Usage for {provider or 'all'} providers"}

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "active_runs": 1, "telegram_bridge": "external"}

    async def shutdown(self) -> dict[str, Any]:
        return {"ok": True, "stopping": True}

    async def current_model(self) -> dict[str, Any]:
        return self.model_payload

    async def update_model(self, provider: str, model: str, *, persist_global: bool = False) -> dict[str, Any]:
        self.model_updates.append((provider, model, persist_global))
        self.model_payload = {"provider": provider, "model": model}
        return {"provider": provider, "model": model, "persisted_path": None, "automations_updated": 1 if persist_global else 0}

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
    assert "/restart - Restart the Libre Claw daemon/Telegram stack" in text
    assert "/model - Open provider/model buttons" in text
    assert "/models - Open provider/model buttons" in text
    assert "/provider - Open provider buttons" in text
    assert "/usage [provider] - Show provider usage analytics" in text
    assert "/daemon - Show daemon connection health" in text
    assert "/runs [N] - List recent daemon runs" in text
    assert "/run <id> - Inspect a daemon run" in text
    assert "/status - Show model, context, token, and cost usage" in text
    assert "/stop - Cancel the active generation" in text
    assert "/shutdown - Shut down the daemon/bridge" in text
    assert "/btw <note> - Add a side note for future turns" in text
    assert "/steer <instruction> - Steer future agent turns" in text
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


def test_telegram_markdown_renders_to_safe_html() -> None:
    html = markdown_to_telegram_html(
        "## HN Brief\n\n"
        "• **Where does next-token prediction leave us?**\n"
        "[Read it](https://example.com?a=1&b=2)\n"
        "`src/app.py`\n\n"
        "```python\nprint('<safe>')\n```"
    )

    assert "<b>HN Brief</b>" in html
    assert "• <b>Where does next-token prediction leave us?</b>" in html
    assert '<a href="https://example.com?a=1&amp;b=2">Read it</a>' in html
    assert "<code>src/app.py</code>" in html
    assert "<pre><code>print('&lt;safe&gt;')</code></pre>" in html


def test_telegram_final_answer_cleaner_removes_process_preamble() -> None:
    cleaned = clean_final_answer_for_telegram(
        "Let me fetch the stories.No prior HN memory found.\n\n"
        "- temporary candidate list\n\n"
        "---\n\n"
        "**AI / ML**\n\n"
        "• **A story**\n"
        "https://example.com"
    )

    assert cleaned.startswith("**AI / ML**")
    assert "Let me fetch" not in cleaned
    assert "temporary candidate" not in cleaned


def test_telegram_html_chunks_keep_messages_under_limit() -> None:
    chunks = telegram_html_chunks("## Title\n\n" + ("**bold** text " * 500), configured_limit=300)

    assert len(chunks) > 1
    assert all(chunk.parse_mode == "HTML" for chunk in chunks)
    assert all(len(chunk.text) <= 300 for chunk in chunks)


def test_telegram_document_path_extraction_is_workspace_scoped(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pdf = workspace / "Claude Fable 5 system card.pdf"
    outside = tmp_path / "outside.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    outside.write_bytes(b"%PDF-1.7")

    text = f"Saved here: `{pdf}`\nDo not send {outside}"

    assert _telegram_document_paths_from_text(text, workspace) == [pdf.resolve()]
    assert _looks_like_file_send_request("send it over to telegram here") is True
    assert _looks_like_file_send_request("summarize it") is False


async def test_telegram_message_can_send_remembered_workspace_document(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pdf = workspace / "anthropic_fable5_system_card.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    config = load_config(working_directory=workspace)

    class State:
        task: asyncio.Task[None] | None = None

    class Bridge:
        def __init__(self) -> None:
            self.config = config
            self.state = State()

        def state_for(self, chat_id: int) -> State:
            assert chat_id == 123
            return self.state

    class EffectiveMessage:
        text = "send it over"
        caption = ""

        def __init__(self) -> None:
            self.replies: list[str] = []
            self.documents: list[tuple[str, bytes]] = []

        async def reply_text(self, text: str, **kwargs: object) -> None:
            del kwargs
            self.replies.append(text)

        async def reply_document(self, document: object, **kwargs: object) -> None:
            filename = str(kwargs.get("filename", ""))
            payload = document.read()  # type: ignore[attr-defined]
            self.documents.append((filename, payload))

    class User:
        id = 123

    class Chat:
        id = 123

    class Update:
        effective_user = User()
        effective_chat = Chat()

        def __init__(self) -> None:
            self.effective_message = EffectiveMessage()

    handlers = TelegramHandlers(Bridge(), TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]
    handlers._remember_document_paths(123, [pdf])
    update = Update()

    await handlers.message(update, object())  # type: ignore[arg-type]

    assert update.effective_message.documents == [(pdf.name, b"%PDF-1.7")]
    assert update.effective_message.replies == []


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


async def test_telegram_safe_edit_ignores_retry_after() -> None:
    class RetryAfterError(Exception):
        retry_after = 190

    class Message:
        async def edit_text(self, text: str) -> None:
            del text
            raise RetryAfterError("Flood control exceeded. Retry in 190 seconds")

    edited = await _safe_edit_text_preview(Message(), "hello", configured_limit=3900)

    assert edited is False


async def test_telegram_tool_heavy_runs_send_final_answer_last(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.telegram.handlers.TELEGRAM_TYPING_INTERVAL_SECONDS", 60)

    class State:
        task: asyncio.Task[None] | None = None

    class Bridge:
        config = load_config()

        def __init__(self) -> None:
            self.state = State()

        def state_for(self, chat_id: int) -> State:
            assert chat_id == 123
            return self.state

        async def stream_message(self, chat_id: int, text: str):
            assert chat_id == 123
            assert text == "hn"
            yield TelegramText("Let me fetch Hacker News.")
            yield TelegramToolNotice(
                "🌐 GET https://hacker-news.firebaseio.com/v0/topstories.json",
                tool_name="http_request",
            )
            yield TelegramToolNotice(
                "✅ http_request done\nstatus: 200\nbytes: 4501",
                tool_name="http_request",
                is_result=True,
            )
            yield TelegramText("Final answer")
            yield TelegramDone(None)

    class SentMessage:
        def __init__(self, text: str) -> None:
            self.text = text
            self.edits: list[str] = []

        async def edit_text(self, text: str) -> None:
            self.edits.append(text)

    class EffectiveMessage:
        text = "hn"

        def __init__(self) -> None:
            self.sent: list[SentMessage] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> SentMessage:
            del reply_markup
            message = SentMessage(text)
            self.sent.append(message)
            return message

    class User:
        id = 123

    class Chat:
        id = 123

    class Update:
        effective_user = User()
        effective_chat = Chat()

        def __init__(self) -> None:
            self.effective_message = EffectiveMessage()

    class Bot:
        async def send_chat_action(self, chat_id: int, action: str) -> None:
            del chat_id, action

    class Context:
        bot = Bot()

    bridge = Bridge()
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]
    update = Update()

    await handlers.message(update, Context())  # type: ignore[arg-type]
    assert bridge.state.task is not None
    await bridge.state.task

    sent_texts = [message.text for message in update.effective_message.sent]
    assert sent_texts[0] == "Libre Claw is thinking..."
    assert sent_texts[1].startswith("🧰 Tool activity (1)")
    assert "HTTP requests: 1 requested, 0 done" in sent_texts[1]
    assert update.effective_message.sent[1].edits[-1].startswith("🧰 Tool activity (1)")
    assert "HTTP requests: 1 requested, 1 done" in update.effective_message.sent[1].edits[-1]
    assert sent_texts[-1] == "Final answer"
    assert update.effective_message.sent[0].edits[-1] == "✅ Run complete. Final answer below."


async def test_telegram_typing_indicator_stops_while_permission_is_blocked(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.telegram.handlers.TELEGRAM_TYPING_INTERVAL_SECONDS", 0.01)
    permission_replied = asyncio.Event()
    release = asyncio.Event()

    class State:
        task: asyncio.Task[None] | None = None

    class Bridge:
        config = load_config()

        def __init__(self) -> None:
            self.state = State()

        def state_for(self, chat_id: int) -> State:
            assert chat_id == 123
            return self.state

        async def stream_message(self, chat_id: int, text: str):
            assert chat_id == 123
            assert text == "run command"
            yield TelegramPermissionPrompt(
                "prompt-1",
                ToolCall(id="toolu_1", name="bash", arguments={"command": "echo hi"}),
                "Approve bash?",
            )
            await release.wait()
            yield TelegramDone(None)

    class SentMessage:
        def __init__(self, text: str) -> None:
            self.text = text
            self.edits: list[str] = []

        async def edit_text(self, text: str, **kwargs: object) -> None:
            del kwargs
            self.edits.append(text)

    class EffectiveMessage:
        text = "run command"

        def __init__(self) -> None:
            self.sent: list[SentMessage] = []

        async def reply_text(self, text: str, **kwargs: object) -> SentMessage:
            del kwargs
            message = SentMessage(text)
            self.sent.append(message)
            if "Approve bash?" in text:
                permission_replied.set()
            return message

    class User:
        id = 123

    class Chat:
        id = 123

    class Update:
        effective_user = User()
        effective_chat = Chat()

        def __init__(self) -> None:
            self.effective_message = EffectiveMessage()

    class Bot:
        def __init__(self) -> None:
            self.actions: list[tuple[int, str]] = []

        async def send_chat_action(self, chat_id: int, action: str) -> None:
            self.actions.append((chat_id, action))

    class Context:
        def __init__(self) -> None:
            self.bot = Bot()

    bridge = Bridge()
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]
    update = Update()
    context = Context()

    await handlers.message(update, context)  # type: ignore[arg-type]
    assert bridge.state.task is not None
    await asyncio.wait_for(permission_replied.wait(), timeout=1)
    action_count = len(context.bot.actions)
    await asyncio.sleep(0.04)
    release.set()
    await bridge.state.task

    assert action_count >= 1
    assert len(context.bot.actions) == action_count


async def test_telegram_typing_indicator_repeats_until_cancelled(monkeypatch) -> None:
    monkeypatch.setattr("libre_claw.telegram.handlers.TELEGRAM_TYPING_INTERVAL_SECONDS", 0.01)

    class Bot:
        def __init__(self) -> None:
            self.actions: list[tuple[int, str]] = []

        async def send_chat_action(self, chat_id: int, action: str) -> None:
            self.actions.append((chat_id, action))

    bot = Bot()
    task = asyncio.create_task(_typing_indicator_loop(bot, 42))

    await asyncio.sleep(0.025)
    await _cancel_task(task)

    assert len(bot.actions) >= 2
    assert all(action == (42, "typing") for action in bot.actions)


async def test_telegram_typing_indicator_stops_quietly_on_action_error() -> None:
    class Bot:
        async def send_chat_action(self, chat_id: int, action: str) -> None:
            del chat_id, action
            raise RuntimeError("telegram unavailable")

    await _typing_indicator_loop(Bot(), 42)


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
    assert commands["restart"] == "Restart Libre Claw"
    assert commands["models"] == "Open model configuration"
    assert commands["status"] == "Show model, context, tokens, and cost"
    assert commands["usage"] == "Show provider usage analytics"
    assert commands["daemon"] == "Show daemon health"
    assert commands["runs"] == "List recent daemon runs"
    assert commands["run"] == "Inspect one daemon run"
    assert commands["stop"] == "Cancel active generation"
    assert commands["shutdown"] == "Shut down Libre Claw"
    assert commands["btw"] == "Add a side note"
    assert commands["steer"] == "Steer future turns"
    assert "schedule" in commands
    assert commands["heartbeat"] == "Recurring check-ins"
    assert commands["memory"] == "Manage persistent memory"


def test_telegram_restart_spawns_cli_lifecycle_restart(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> object:
        calls.append((command, kwargs))
        return object()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.telegram.handlers._restart_entry_command", lambda: ["libre-claw"])
    monkeypatch.setattr("libre_claw.telegram.handlers.subprocess.Popen", fake_popen)

    log_path = _spawn_lifecycle_restart()

    assert log_path == tmp_path / ".libre-claw" / "daemon.log"
    assert calls[0][0] == ["libre-claw", "restart", "--force"]
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["start_new_session"] is True


async def test_telegram_daemon_commands_report_remote_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    bridge = TelegramBridge(load_config(), daemon_client=FakeDaemonClient())  # type: ignore[arg-type]

    usage = await bridge.usage_command_text(1, "openrouter")
    daemon = await bridge.daemon_command_text(1)
    runs = await bridge.runs_command_text("5")
    run = await bridge.run_command_text("run-20260530-abc123")

    assert "Usage for openrouter providers" in usage
    assert "Daemon: online" in daemon
    assert "Active runs: 1" in daemon
    assert "[done] openrouter:deepseek/deepseek-v4-flash" in runs
    assert "Fix the thing" in runs
    assert "Artifacts: summary.md" in run


async def test_telegram_steering_commands_store_session_summary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    bridge = TelegramBridge(load_config(), daemon_client=FakeDaemonClient())  # type: ignore[arg-type]

    btw = await bridge.add_steering_note(1, "btw", "prefer concise replies")
    steer = await bridge.add_steering_note(1, "steer", "use the HN skill")

    assert btw == "Side note saved for future turns."
    assert steer == "Steering instruction saved for future turns."
    summary = bridge.state_for(1).session.summary or ""
    assert "Side note: prefer concise replies" in summary
    assert "Steering instruction: use the HN skill" in summary


def test_telegram_model_configuration_uses_inline_keyboards(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()

    text = _model_configuration_text(config)
    provider_keyboard = _provider_keyboard(config)
    model_keyboard = _model_keyboard(config, "openrouter")
    ollama_keyboard = _model_keyboard(config, "ollama")

    assert "Model Configuration" in text
    assert "Select a provider" in text
    assert provider_keyboard.inline_keyboard
    assert any("OpenRouter" in button.text for row in provider_keyboard.inline_keyboard for button in row)
    assert len([button for row in model_keyboard.inline_keyboard for button in row if button.callback_data.startswith("cfg:model:openrouter:")]) == len(
        TELEGRAM_MODEL_PRESETS["openrouter"]
    )
    assert any("MiniMax M3" in button.text for row in model_keyboard.inline_keyboard for button in row)
    assert any("Kimi K3" in button.text for row in model_keyboard.inline_keyboard for button in row)
    assert any(
        preset.model == "moonshotai/kimi-k3" for preset in TELEGRAM_MODEL_PRESETS["openrouter"]
    )
    assert any("MiniMax M3" in button.text for row in ollama_keyboard.inline_keyboard for button in row)
    assert any(preset.model == "minimax-m3:cloud" for preset in TELEGRAM_MODEL_PRESETS["ollama"])
    assert any("GLM 5.2" in button.text for row in ollama_keyboard.inline_keyboard for button in row)
    assert any(preset.model == "glm-5.2:cloud" for preset in TELEGRAM_MODEL_PRESETS["ollama"])


def test_telegram_openrouter_keyboard_promotes_current_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = _replace_general(config, default_provider="openrouter", default_model="minimax/minimax-m3")

    model_keyboard = _model_keyboard(config, "openrouter")

    first = model_keyboard.inline_keyboard[0][0]
    minimax_index = next(
        index
        for index, preset in enumerate(TELEGRAM_MODEL_PRESETS["openrouter"])
        if preset.model == "minimax/minimax-m3"
    )
    assert first.text == "✓ MiniMax M3"
    assert first.callback_data == f"cfg:model:openrouter:{minimax_index}"


async def test_telegram_model_command_strips_global_flag_and_persists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))

    class User:
        id = 123

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.replies.append(text)

    class Update:
        effective_user = User()

        def __init__(self) -> None:
            self.effective_message = Message()

    class Context:
        args = ["openrouter:minimax/minimax-m3", "--global"]

    update = Update()

    await handlers.model(update, Context())  # type: ignore[arg-type]

    persisted = tmp_path / ".libre-claw" / "config.toml"
    assert bridge.config.general.default_provider == "openrouter"
    assert bridge.config.general.default_model == "minimax/minimax-m3"
    assert update.effective_message.replies == [
        f"Model set to openrouter:minimax/minimax-m3.\nSaved as global default in {persisted}."
    ]
    assert 'default_provider = "openrouter"' in persisted.read_text(encoding="utf-8")
    assert 'default_model = "minimax/minimax-m3"' in persisted.read_text(encoding="utf-8")
    assert "--global" not in persisted.read_text(encoding="utf-8")


async def test_telegram_model_command_syncs_daemon_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient(with_permission=False)
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))

    class User:
        id = 123

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.replies.append(text)

    class Update:
        effective_user = User()

        def __init__(self) -> None:
            self.effective_message = Message()

    class Context:
        args = ["openrouter:deepseek/deepseek-v4-pro"]

    update = Update()

    await handlers.model(update, Context())  # type: ignore[arg-type]

    assert bridge.config.general.default_provider == "openrouter"
    assert bridge.config.general.default_model == "deepseek/deepseek-v4-pro"
    assert daemon.model_updates == [("openrouter", "deepseek/deepseek-v4-pro", False)]
    assert update.effective_message.replies == [
        "Model set to openrouter:deepseek/deepseek-v4-pro.\n"
        "Daemon default updated for new daemon-backed runs."
    ]


async def test_telegram_model_global_command_syncs_daemon_and_schedules(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient(with_permission=False)
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))

    class User:
        id = 123

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.replies.append(text)

    class Update:
        effective_user = User()

        def __init__(self) -> None:
            self.effective_message = Message()

    class Context:
        args = ["openrouter:xiaomi/mimo-v2.5-pro", "--global"]

    update = Update()

    await handlers.model(update, Context())  # type: ignore[arg-type]

    persisted = tmp_path / ".libre-claw" / "config.toml"
    assert daemon.model_updates == [("openrouter", "xiaomi/mimo-v2.5-pro", True)]
    assert update.effective_message.replies == [
        f"Model set to openrouter:xiaomi/mimo-v2.5-pro.\n"
        f"Saved as global default in {persisted}.\n"
        "Daemon default updated for new daemon-backed runs. Updated 1 scheduled automation(s)."
    ]


async def test_telegram_model_callback_sets_provider_and_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))

    class User:
        id = 123

    class Query:
        data = "cfg:model:openrouter:0"
        from_user = User()

        def __init__(self) -> None:
            self.answers: list[str] = []
            self.edits: list[str] = []

        async def answer(self, text: str, show_alert: bool = False) -> None:
            del show_alert
            self.answers.append(text)

        async def edit_message_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.edits.append(text)

    class Update:
        def __init__(self, query: Query) -> None:
            self.callback_query = query

    query = Query()

    await handlers.callback(Update(query), object())  # type: ignore[arg-type]

    assert bridge.config.general.default_provider == "openrouter"
    assert bridge.config.general.default_model == TELEGRAM_MODEL_PRESETS["openrouter"][0].model
    assert query.answers == ["Model selected."]
    assert "Your next Telegram message will use this model." in query.edits[-1]


async def test_telegram_permission_callback_data_stays_under_telegram_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    long_prompt_id = "daemon:run-20260525-200404-a2b6bb7f:" + ("toolu_browser_call_" * 8)

    class Bridge:
        config = load_config()

        def __init__(self) -> None:
            self.resolved: list[tuple[str, str]] = []

        async def resolve_permission_async(self, prompt_id: str, resolution: str) -> bool:
            self.resolved.append((prompt_id, resolution))
            return True

    bridge = Bridge()
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]

    class User:
        id = 123

    class Query:
        from_user = User()

        def __init__(self, data: str) -> None:
            self.data = data
            self.answers: list[str] = []
            self.edits: list[str] = []

        async def answer(self, text: str, show_alert: bool = False) -> None:
            del show_alert
            self.answers.append(text)

        async def edit_message_text(self, text: str, reply_markup: object | None = None) -> None:
            del reply_markup
            self.edits.append(text)

    class Update:
        def __init__(self, query: Query) -> None:
            self.callback_query = query

    cases = [
        ("p:y:", "allow_once", "Approved.", "✅ Approved"),
        ("p:n:", "deny", "Denied.", "✖️ Denied"),
        ("p:t:", "always_allow_tool", "Always allowing this tool for this run.", "✅ Always allowing this tool"),
        (
            "p:c:",
            "always_allow_call",
            "Always allowing this exact call for this run.",
            "✅ Always allowing this exact call",
        ),
    ]

    for prefix, resolution, answer, edit_prefix in cases:
        markup = handlers._permission_reply_markup(long_prompt_id)
        callback_values = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data is not None
        ]
        assert callback_values
        assert all(len(value) <= 64 for value in callback_values)
        callback_data = next(value for value in callback_values if value.startswith(prefix))
        query = Query(callback_data)
        await handlers.callback(Update(query), object())  # type: ignore[arg-type]
        assert query.answers == [answer]
        assert query.edits and query.edits[-1].startswith(edit_prefix)

    assert bridge.resolved == [(long_prompt_id, resolution) for _, resolution, _, _ in cases]


async def test_telegram_expand_callback_replies_with_full_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class Bridge:
        config = load_config()

    bridge = Bridge()
    handlers = TelegramHandlers(bridge, TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]
    markup = handlers._expand_reply_markup(
        TelegramExpandablePayload(title="Full response", text="This is the full hidden text.", clean_final=True)
    )
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert callback_data is not None
    assert callback_data.startswith("x:")
    assert len(callback_data) <= 64

    class User:
        id = 123

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str, **kwargs: object) -> None:
            del kwargs
            self.replies.append(text)

    class Query:
        data = callback_data
        from_user = User()

        def __init__(self) -> None:
            self.message = Message()
            self.answers: list[str] = []
            self.markup_cleared = False

        async def answer(self, text: str, show_alert: bool = False) -> None:
            del show_alert
            self.answers.append(text)

        async def edit_message_reply_markup(self, reply_markup: object | None = None) -> None:
            assert reply_markup is None
            self.markup_cleared = True

    class Update:
        def __init__(self, query: Query) -> None:
            self.callback_query = query

    query = Query()
    await handlers.callback(Update(query), object())  # type: ignore[arg-type]

    assert query.answers == ["Showing full content."]
    assert query.markup_cleared is True
    assert query.message.replies == ["Full response\n\nThis is the full hidden text."]


def test_telegram_tool_log_expand_markup_exposes_hidden_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class Bridge:
        config = load_config()

    handlers = TelegramHandlers(Bridge(), TelegramAuth(allowed_user_ids=frozenset({123})))  # type: ignore[arg-type]
    notices = [f"🔧 tool_{index}\noutput {index}" for index in range(12)]
    markup = handlers._tool_log_expand_markup(notices, http_started=0, http_done=0)

    assert markup is not None
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert callback_data is not None
    assert callback_data.startswith("x:")
    token = callback_data.removeprefix("x:")
    payload = handlers._expand_callback_ids[token]
    assert "tool_0" in payload.text
    assert "tool_11" in payload.text


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
        async def set_my_commands(self, commands: list[object], scope: object | None = None) -> None:
            del scope
            assert len(commands) >= 8
            calls.append("set_my_commands")

        async def set_chat_menu_button(self, chat_id: int | None = None, menu_button: object | None = None) -> None:
            del chat_id, menu_button
            calls.append("set_chat_menu_button")

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
    assert calls.count("set_my_commands") >= 2
    assert "set_chat_menu_button" in calls


async def test_telegram_bot_syncs_commands_to_allowed_private_chats(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, object | None, int | None]] = []

    class FakeBot:
        async def set_my_commands(self, commands: list[object], scope: object | None = None) -> None:
            assert len(commands) >= 8
            chat_id = getattr(scope, "chat_id", None)
            calls.append(("commands", scope.__class__.__name__ if scope is not None else None, chat_id))

        async def set_chat_menu_button(self, chat_id: int | None = None, menu_button: object | None = None) -> None:
            assert menu_button is not None
            calls.append(("menu", None, chat_id))

    class FakeApplication:
        bot = FakeBot()

    bot = TelegramBot(load_config(), bridge=object())  # type: ignore[arg-type]
    bot.auth = TelegramAuth(allowed_user_ids=frozenset({123, 456}))

    await bot._sync_command_menu(FakeApplication())

    command_scopes = [call for call in calls if call[0] == "commands"]
    menu_chat_ids = [call[2] for call in calls if call[0] == "menu"]
    assert any(call[1] == "BotCommandScopeDefault" for call in command_scopes)
    assert any(call[1] == "BotCommandScopeAllPrivateChats" for call in command_scopes)
    assert ("commands", "BotCommandScopeChat", 123) in command_scopes
    assert ("commands", "BotCommandScopeChat", 456) in command_scopes
    assert None in menu_chat_ids
    assert 123 in menu_chat_ids
    assert 456 in menu_chat_ids


async def test_telegram_bridge_streams_text(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda config: FakeProvider())

    events = [event async for event in bridge.stream_message(1, "hello")]

    assert events == [TelegramText("hi"), TelegramDone(None)]


async def test_telegram_bridge_passes_image_attachments_to_daemon(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient(with_permission=False)
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    await bridge.initialize()
    attachment = UserAttachment(
        media_type="image/png",
        data="aGVsbG8=",
        filename="shot.png",
        path=str(tmp_path / "shot.png"),
    )

    events = [event async for event in bridge.stream_message(1, "inspect", attachments=[attachment])]

    assert any(isinstance(event, TelegramText) and event.text == "hi" for event in events)
    assert daemon.start_payloads[0]["attachments"] == [
        {
            "media_type": "image/png",
            "data": "aGVsbG8=",
            "filename": "shot.png",
            "path": str(tmp_path / "shot.png"),
        }
    ]


async def test_telegram_image_attachments_download_photo(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    payload = b"\x89PNG\r\n\x1a\nimage"

    class FakeTelegramFile:
        async def download_to_drive(self, custom_path: Path) -> None:
            custom_path.write_bytes(payload)

    class FakePhoto:
        file_id = "file-id"
        file_unique_id = "unique-id"
        file_size = len(payload)

        async def get_file(self) -> FakeTelegramFile:
            return FakeTelegramFile()

    class FakeMessage:
        photo = [FakePhoto()]
        document = None

    attachments, warnings = await _telegram_image_attachments(FakeMessage(), 42)

    assert warnings == []
    assert len(attachments) == 1
    assert attachments[0].media_type == "image/jpeg"
    assert attachments[0].data
    assert attachments[0].filename == "telegram-photo-unique-id.jpg"
    assert Path(attachments[0].path).exists()


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


async def test_telegram_bridge_injects_soul(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    soul_path = tmp_path / "SOUL.md"
    soul_path.write_text("# Telegram Soul\n\nSound like Libre Claw on mobile.", encoding="utf-8")
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()
    provider = FakeProvider()
    provider.system_prompts.clear()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda config: provider)

    events = [event async for event in bridge.stream_message(1, "hello")]

    assert events == [TelegramText("hi"), TelegramDone(None)]
    assert provider.system_prompts
    assert provider.system_prompts[0] is not None
    assert "Libre Claw soul/persona customization" in provider.system_prompts[0]
    assert "Sound like Libre Claw on mobile." in provider.system_prompts[0]


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
    resolved = await bridge.resolve_permission_async(prompt.prompt_id, "always_allow_tool")

    assert any(isinstance(event, TelegramText) and event.text == "hi" for event in events)
    assert prompt.text.startswith("🔐 Approve bash?")
    assert "Approve daemon run" not in prompt.text
    assert prompt.prompt_id == "daemon:run-1:toolu_1"
    assert resolved is True
    assert daemon.resolutions == [("run-1", "toolu_1", "always_allow_tool")]


async def test_telegram_daemon_event_cursor_is_per_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()

    class ResettingDaemon:
        def __init__(self) -> None:
            self.afters: list[int] = []
            self.lookup_count = 0
            self.bridge: TelegramBridge | None = None

        async def start_run(self, message: str, **payload: Any) -> dict[str, Any]:
            del message, payload
            return {"run": {"run_id": "run-cursor", "state": "queued"}}

        async def get_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
            assert run_id == "run-cursor"
            self.afters.append(after)
            if after == 0:
                return {"events": [{"event_id": 1, "type": "assistant_delta", "data": {"text": "hi"}}]}
            return {"events": []}

        async def get_run(self, run_id: str) -> dict[str, Any]:
            assert run_id == "run-cursor"
            self.lookup_count += 1
            if self.lookup_count == 1:
                assert self.bridge is not None
                self.bridge.state_for(1).daemon_event_id = 0
                return {"run": {"run_id": run_id, "state": "running"}}
            return {"run": {"run_id": run_id, "state": "done"}}

    daemon = ResettingDaemon()
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    daemon.bridge = bridge
    await bridge.initialize()

    events = [event async for event in bridge.stream_message(1, "hello")]

    assert daemon.afters == [0, 1]
    assert [event.text for event in events if isinstance(event, TelegramText)] == ["hi"]


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
    status = bridge.status_text(1)
    assert "## Libre Claw Status" in status
    assert "**Model**" in status
    assert "- Provider:" in status
    assert "- Model:" in status
    assert "**Context**" in status
    assert "- Window: 200k tokens" in status
    assert "- Fill: `[" in status
    assert "10 total" in status
    assert "Last turn: 5 tokens (3 input, 2 output)" in status


async def test_telegram_status_uses_daemon_openrouter_context_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    daemon = FakeDaemonClient(with_permission=False)
    daemon.model_payload = {
        "provider": "openrouter",
        "model": "minimax/minimax-m3",
        "context_window_tokens": 1_048_576,
        "detected_context_window_tokens": 1_048_576,
        "detected_max_completion_tokens": 32_768,
        "detected_context_source": "models",
        "detected_context_model": "minimax/minimax-m3",
    }
    bridge = TelegramBridge(config, daemon_client=daemon)  # type: ignore[arg-type]
    await bridge.initialize()

    status = await bridge.status_text_async(1)

    assert "- Provider: `openrouter`" in status
    assert "- Model: `minimax/minimax-m3`" in status
    assert "- Window: 1M tokens" in status
    assert bridge.config.agent.context_window_tokens == 1_048_576
    assert bridge.config.providers["openrouter"]["detected_max_completion_tokens"] == 32_768


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


async def test_telegram_memory_command_manages_searchable_items(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    bridge = TelegramBridge(config)
    await bridge.initialize()

    added = await bridge.memory_command_text(42, "add Robin prefers EST")
    searched = await bridge.memory_command_text(42, "search EST")
    status = await bridge.memory_command_text(42, "status")
    off = await bridge.memory_command_text(42, "off")

    assert "Added memory" in added
    assert "Robin prefers EST" in searched
    assert "Memory status" in status
    assert off == "Persistent memory disabled for Telegram."


def test_telegram_tool_notices_are_compact() -> None:
    call = _tool_call_notice("browser_navigate", {"url": "https://github.com/kroonen-ai/libre-claw"})
    result = _tool_result_notice("browser_read", is_error=False, content="x" * 2000)

    assert call == "🔧 browser_navigate\nurl: https://github.com/kroonen-ai/libre-claw"
    assert result.startswith("✅ browser_read done\n")
    assert "… truncated" in result
    assert len(result) < 1300


def test_telegram_http_request_notices_hide_response_bodies() -> None:
    call = _tool_call_notice(
        "http_request",
        {
            "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
            "max_response_chars": 20000,
        },
    )
    content = (
        "GET https://hacker-news.firebaseio.com/v0/topstories.json\n"
        "status: 200 OK\n"
        "content_type: application/json; charset=utf-8\n"
        "bytes: 4501\n\n"
        "[48281226,48281367,48280636]"
    )
    result = _tool_result_notice(
        "http_request",
        is_error=False,
        content=content,
        metadata={
            "method": "GET",
            "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
            "status_code": 200,
            "content_type": "application/json; charset=utf-8",
            "bytes": 4501,
            "truncated": True,
        },
    )

    assert call == "🌐 GET https://hacker-news.firebaseio.com/v0/topstories.json"
    assert "max_response_chars" not in call
    assert result.startswith("✅ http_request done\nGET https://hacker-news.firebaseio.com/v0/topstories.json")
    assert "status: 200" in result
    assert "bytes: 4501" in result
    assert "[48281226" not in result


def test_telegram_tool_log_preview_keeps_latest_activity_in_one_message() -> None:
    notices = [f"🌐 GET https://example.com/{index}\nurl: https://example.com/{index}" for index in range(12)]

    preview = _tool_log_preview(notices, configured_limit=3900)
    html = _tool_log_formatted_chunk(preview, configured_limit=3900)

    assert preview.startswith("🧰 Tool activity (12)")
    assert "4 earlier events hidden" in preview
    assert "https://example.com/11" in preview
    assert "https://example.com/0" not in preview
    assert "```text" in preview
    assert "<pre><code>url: https://example.com/11</code></pre>" in html.text


def test_telegram_http_activity_is_aggregated_in_tool_log() -> None:
    notices = _visible_tool_notices(["🔧 bash\ncommand: pytest"], http_started=119, http_done=118)
    preview = _tool_log_preview(notices, configured_limit=3900, total_count=238)

    assert preview.startswith("🧰 Tool activity (238)")
    assert "🌐 HTTP requests: 119 requested, 118 done" in preview
    assert "🔧 bash" in preview
    assert "hacker-news.firebaseio.com/v0/item" not in preview
