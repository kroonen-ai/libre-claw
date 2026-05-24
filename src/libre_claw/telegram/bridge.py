# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

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
from libre_claw.core.tools import ToolCall
from libre_claw.providers import ProviderConfigurationError, Usage, create_provider
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


class TelegramBridge:
    """Bridge Telegram chats to the same Libre Claw agent core."""

    def __init__(self, config: LibreClawConfig, memory_store: MemoryStore | None = None) -> None:
        self.config = config
        self.memory_store = memory_store or MemoryStore()
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
                    state.usage = event.usage
                yield TelegramDone(event.usage)
                continue
            if isinstance(event, AgentError):
                yield TelegramError(event.message)
                return

    def resolve_permission(self, prompt_id: str, resolution: PermissionResolution) -> bool:
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

    def cancel(self, chat_id: int) -> bool:
        state = self.state_for(chat_id)
        if state.task is None or state.task.done():
            return False
        state.task.cancel()
        return True

    def status_text(self, chat_id: int) -> str:
        state = self.state_for(chat_id)
        return f"Tokens: {state.usage.total_tokens} total. Cost: $0.00."

    def _create_agent(self, state: TelegramChatState) -> Agent:
        provider = create_provider(self.config)
        return Agent(
            session=state.session,
            provider=provider,
            tool_registry=create_builtin_registry(self.config, memory_store=self.memory_store),
            permission_manager=PermissionManager(self.config.permissions),
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            memory_facts=self._memory_facts,
        )
