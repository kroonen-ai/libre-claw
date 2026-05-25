# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    Session,
)
from libre_claw.core.memory import MemoryStore
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.skills import SkillStore
from libre_claw.core.tools import ToolCall
from libre_claw.daemon import DaemonClient
from libre_claw.providers import ProviderConfigurationError, Usage, combine_usage, create_provider
from libre_claw.tools_builtin import create_builtin_registry


@dataclass(frozen=True)
class TelegramText:
    text: str


@dataclass(frozen=True)
class TelegramToolNotice:
    text: str


@dataclass(frozen=True)
class TelegramPermissionPrompt:
    prompt_id: str
    call: ToolCall
    text: str


@dataclass(frozen=True)
class TelegramDone:
    usage: Usage | None = None


@dataclass(frozen=True)
class TelegramError:
    text: str


TelegramEvent = TelegramText | TelegramToolNotice | TelegramPermissionPrompt | TelegramDone | TelegramError


@dataclass
class TelegramChatState:
    chat_id: int
    session: Session = field(default_factory=Session)
    usage: Usage = field(default_factory=Usage)
    task: asyncio.Task[None] | None = None
    pending_permissions: dict[str, AgentPermissionRequest] = field(default_factory=dict)
    daemon_run_id: str | None = None
    daemon_event_id: int = 0


class TelegramBridge:
    """Bridge Telegram chats to the same Libre Claw agent core."""

    def __init__(
        self,
        config: LibreClawConfig,
        memory_store: MemoryStore | None = None,
        daemon_client: DaemonClient | None = None,
    ) -> None:
        self.config = config
        self.memory_store = memory_store or MemoryStore()
        self.daemon_client = daemon_client
        self.skill_store = SkillStore(config.general.working_directory)
        self._states: dict[int, TelegramChatState] = {}
        self._memory_facts: list[str] = []

    async def initialize(self) -> None:
        await self.memory_store.initialize()
        facts = await self.memory_store.list_facts()
        self._memory_facts = [fact.fact for fact in facts]

    def state_for(self, chat_id: int) -> TelegramChatState:
        return self._states.setdefault(chat_id, TelegramChatState(chat_id=chat_id))

    def new_session(self, chat_id: int) -> TelegramChatState:
        state = TelegramChatState(chat_id=chat_id)
        self._states[chat_id] = state
        return state

    async def stream_message(self, chat_id: int, text: str):
        if self.daemon_client is not None:
            async for event in self._stream_daemon_message(chat_id, text):
                yield event
            return

        state = self.state_for(chat_id)
        try:
            agent = self._create_agent(state)
        except ProviderConfigurationError as exc:
            yield TelegramError(str(exc))
            return

        async for event in agent.run(text):
            if isinstance(event, AgentTextDelta):
                yield TelegramText(event.text)
                continue
            if isinstance(event, AgentToolCall):
                yield TelegramToolNotice(f"Calling {event.call.name} with {dict(event.call.arguments)}")
                continue
            if isinstance(event, AgentPermissionRequest):
                prompt_id = f"{chat_id}:{event.call.id}"
                state.pending_permissions[prompt_id] = event
                yield TelegramPermissionPrompt(
                    prompt_id=prompt_id,
                    call=event.call,
                    text=f"Approve {event.call.name} with {dict(event.call.arguments)}?",
                )
                continue
            if isinstance(event, AgentToolResult):
                status = "error" if event.result.is_error else "result"
                yield TelegramToolNotice(f"{event.call.name} {status}: {event.result.as_text()}")
                continue
            if isinstance(event, AgentDone):
                if event.usage is not None:
                    state.usage = combine_usage(state.usage, event.usage) or state.usage
                yield TelegramDone(event.usage)
                continue
            if isinstance(event, AgentError):
                yield TelegramError(event.message)
                return

    def resolve_permission(self, prompt_id: str, resolution: PermissionResolution) -> bool:
        if prompt_id.startswith("daemon:"):
            return False
        chat_id_text, _, _ = prompt_id.partition(":")
        if not chat_id_text.isdigit():
            return False
        state = self._states.get(int(chat_id_text))
        if state is None:
            return False
        request = state.pending_permissions.pop(prompt_id, None)
        if request is None or request.future.done():
            return False
        request.future.set_result(resolution)
        return True

    async def resolve_permission_async(self, prompt_id: str, resolution: PermissionResolution) -> bool:
        if not prompt_id.startswith("daemon:"):
            return self.resolve_permission(prompt_id, resolution)
        if self.daemon_client is None:
            return False
        parts = prompt_id.split(":", 2)
        if len(parts) != 3:
            return False
        _, run_id, tool_call_id = parts
        try:
            await self.daemon_client.resolve_permission(run_id, tool_call_id, resolution)
        except Exception:
            return False
        return True

    def cancel(self, chat_id: int) -> bool:
        state = self.state_for(chat_id)
        if state.task is None or state.task.done():
            return False
        state.task.cancel()
        return True

    async def cancel_async(self, chat_id: int) -> bool:
        state = self.state_for(chat_id)
        cancelled = self.cancel(chat_id)
        if self.daemon_client is None or state.daemon_run_id is None:
            return cancelled
        try:
            await self.daemon_client.cancel_run(state.daemon_run_id)
        except Exception:
            return cancelled
        return True

    def status_text(self, chat_id: int) -> str:
        state = self.state_for(chat_id)
        return (
            f"Tokens: {state.usage.total_tokens} total "
            f"({state.usage.input_tokens} input, {state.usage.output_tokens} output). "
            f"Cost: {_format_usage_cost(state.usage)}."
        )

    def _create_agent(self, state: TelegramChatState) -> Agent:
        provider = create_provider(self.config)
        return Agent(
            session=state.session,
            provider=provider,
            tool_registry=create_builtin_registry(self.config, memory_store=self.memory_store),
            permission_manager=PermissionManager(self.config.permissions),
            system_prompt=self.config.agent.system_prompt,
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            context_window_tokens=self.config.agent.context_window_tokens,
            memory_facts=self._memory_facts,
            system_prompt_extra=self.config.agent.system_prompt_extra,
            skill_provider=self.skill_store.relevant_skill_texts,
        )

    async def _stream_daemon_message(self, chat_id: int, text: str):
        if self.daemon_client is None:
            yield TelegramError("Daemon client is not configured.")
            return

        state = self.state_for(chat_id)
        try:
            started = await self.daemon_client.start_run(
                text,
                kind="chat",
                provider=self.config.general.default_provider,
                model=self.config.general.default_model,
                working_directory=str(self.config.general.working_directory),
            )
        except Exception as exc:
            yield TelegramError(f"Could not start daemon run: {exc}")
            return

        run = _object_payload(started.get("run"))
        run_id = str(run.get("run_id", ""))
        if not run_id:
            yield TelegramError("Daemon did not return a run id.")
            return
        state.daemon_run_id = run_id
        state.daemon_event_id = 0
        yielded_done = False
        yield TelegramToolNotice(f"Daemon run {run_id} started.")

        while True:
            try:
                payload = await self.daemon_client.get_events(run_id, after=state.daemon_event_id)
            except Exception as exc:
                yield TelegramError(f"Daemon event polling failed: {exc}")
                return

            events = payload.get("events", [])
            if not isinstance(events, list):
                events = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                state.daemon_event_id = max(state.daemon_event_id, int(event.get("event_id", 0) or 0))
                async for mapped in _telegram_events_from_daemon_event(run_id, event):
                    yielded_done = yielded_done or isinstance(mapped, TelegramDone)
                    yield mapped

            try:
                detail = await self.daemon_client.get_run(run_id)
            except Exception as exc:
                yield TelegramError(f"Daemon run lookup failed: {exc}")
                return
            run = _object_payload(detail.get("run"))
            if str(run.get("state", "")) in {"done", "failed", "cancelled"}:
                if not yielded_done:
                    yield TelegramDone(None)
                return
            await asyncio.sleep(max(0.1, self.config.daemon.poll_interval))


def _format_usage_cost(usage: Usage) -> str:
    if usage.cost is None or usage.cost == 0:
        return "$0.00"
    if usage.cost < 0.01:
        return f"${usage.cost:.6f}"
    return f"${usage.cost:.2f}"


async def _telegram_events_from_daemon_event(run_id: str, event: dict[str, Any]):
    data = _object_payload(event.get("data"))
    event_type = str(event.get("type", ""))
    if event_type == "assistant_delta":
        yield TelegramText(str(data.get("text", "")))
        return
    if event_type == "tool_call":
        yield TelegramToolNotice(f"Calling {data.get('name', 'tool')} with {_object_payload(data.get('arguments'))}")
        return
    if event_type == "permission_request":
        call = ToolCall(
            id=str(data.get("tool_call_id", "")),
            name=str(data.get("name", "tool")),
            arguments=_object_payload(data.get("arguments")),
        )
        yield TelegramPermissionPrompt(
            prompt_id=f"daemon:{run_id}:{call.id}",
            call=call,
            text=f"Approve daemon run {run_id} tool {call.name} with {dict(call.arguments)}?",
        )
        return
    if event_type == "tool_result":
        status = "error" if data.get("is_error") else "result"
        yield TelegramToolNotice(f"{data.get('name', 'tool')} {status}: {data.get('content', '')}")
        return
    if event_type == "error":
        yield TelegramError(str(data.get("message", "Daemon run failed.")))
        return
    if event_type == "run_finished":
        yield TelegramDone(None)


def _object_payload(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
