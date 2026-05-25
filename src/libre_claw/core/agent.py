# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

import structlog

from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.session import Session, estimate_context_tokens, text_block, tool_result_block, tool_use_block
from libre_claw.core.tools import ToolCall, ToolRegistry, ToolRegistryError, ToolResult
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    ProviderError,
    TextDelta,
    ToolCallReady,
    Usage,
    combine_usage,
)


@dataclass(frozen=True)
class AgentTextDelta:
    text: str


@dataclass(frozen=True)
class AgentToolCall:
    call: ToolCall


@dataclass(frozen=True)
class AgentToolResult:
    call: ToolCall
    result: ToolResult


@dataclass
class AgentPermissionRequest:
    call: ToolCall
    future: asyncio.Future[PermissionResolution]


@dataclass(frozen=True)
class AgentDone:
    usage: Usage | None = None


@dataclass(frozen=True)
class AgentError:
    message: str


AgentEvent = AgentTextDelta | AgentToolCall | AgentToolResult | AgentPermissionRequest | AgentDone | AgentError
SkillProvider = Callable[[str], Sequence[str] | Awaitable[Sequence[str]]]
SoulProvider = Callable[[], Sequence[str] | Awaitable[Sequence[str]]]


class Agent:
    """ReAct-style agent loop with client-side tools."""

    def __init__(
        self,
        session: Session,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        permission_manager: PermissionManager,
        system_prompt: str,
        max_tool_calls_per_turn: int = 50,
        auto_compact_threshold: float = 0.8,
        context_window_tokens: int = 200000,
        memory_facts: list[str] | None = None,
        system_prompt_extra: str = "",
        skill_provider: SkillProvider | None = None,
        soul_provider: SoulProvider | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.auto_compact_threshold = auto_compact_threshold
        self.context_window_tokens = context_window_tokens
        self.memory_facts = memory_facts or []
        self.system_prompt = system_prompt
        self.system_prompt_extra = system_prompt_extra
        self.skill_provider = skill_provider
        self.soul_provider = soul_provider
        self._active_skills: list[str] = []
        self._active_soul: list[str] = []
        self._logger = structlog.get_logger(__name__)

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        self.session.add_user_message(user_message)
        self._active_soul = await self._load_soul()
        self._active_skills = await self._load_skills(user_message)
        total_tool_calls = 0
        turn_usage: Usage | None = None

        while True:
            assistant_chunks: list[str] = []
            tool_calls: list[ToolCall] = []
            provider_failed = False

            try:
                self._maybe_compact_session()
                async for event in self.provider.complete(
                    messages=self.session.messages,
                    tools=self.tool_registry.schemas(),
                    system=self._build_system_prompt(),
                ):
                    if isinstance(event, TextDelta):
                        assistant_chunks.append(event.text)
                        yield AgentTextDelta(event.text)
                        continue

                    if isinstance(event, ToolCallReady):
                        call = ToolCall(id=event.tool_call_id, name=event.name, arguments=event.input)
                        tool_calls.append(call)
                        yield AgentToolCall(call)
                        continue

                    if isinstance(event, Done):
                        turn_usage = combine_usage(turn_usage, event.usage)
                        continue

                    if isinstance(event, ProviderError):
                        provider_failed = True
                        self._save_assistant_text(assistant_chunks)
                        yield AgentError(event.message)
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                provider_failed = True
                self._logger.warning("agent_stream_failed", error=str(exc))
                self._save_assistant_text(assistant_chunks)
                yield AgentError(str(exc))

            if provider_failed:
                return

            if not tool_calls:
                self._save_assistant_text(assistant_chunks)
                yield AgentDone(turn_usage)
                return

            total_tool_calls += len(tool_calls)
            if total_tool_calls > self.max_tool_calls_per_turn:
                yield AgentError(f"Stopped after exceeding {self.max_tool_calls_per_turn} tool calls in one turn.")
                return

            self._save_assistant_tool_request(assistant_chunks, tool_calls)

            immediate_results: dict[str, ToolResult] = {}
            executable_calls: list[ToolCall] = []

            for call in tool_calls:
                try:
                    tool = self.tool_registry.get(call.name)
                except ToolRegistryError as exc:
                    immediate_results[call.id] = ToolResult(error=str(exc))
                    continue

                decision = self.permission_manager.check(call, tool)
                if decision == "deny":
                    immediate_results[call.id] = ToolResult(error="Tool permission denied")
                    continue

                if decision == "ask":
                    future: asyncio.Future[PermissionResolution] = asyncio.get_running_loop().create_future()
                    yield AgentPermissionRequest(call=call, future=future)
                    try:
                        resolution = await future
                    except asyncio.CancelledError:
                        raise
                    approved = self.permission_manager.apply_resolution(call, resolution)
                    if not approved:
                        immediate_results[call.id] = ToolResult(error="User denied this action")
                        continue

                executable_calls.append(call)

            executed = await asyncio.gather(*(self.tool_registry.execute(call) for call in executable_calls))
            for call, result in zip(executable_calls, executed, strict=True):
                immediate_results[call.id] = result

            ordered_results = [(call, immediate_results[call.id]) for call in tool_calls]
            self.session.add_tool_result_blocks(
                [
                    tool_result_block(call.id, result.as_text(), is_error=result.is_error)
                    for call, result in ordered_results
                ]
            )

            for call, result in ordered_results:
                yield AgentToolResult(call=call, result=result)

    def _save_assistant_text(self, chunks: list[str]) -> None:
        text = "".join(chunks)
        if text:
            self.session.add_assistant_message(text)
            chunks.clear()

    def _save_assistant_tool_request(self, chunks: list[str], tool_calls: list[ToolCall]) -> None:
        blocks = []
        text = "".join(chunks)
        if text:
            blocks.append(text_block(text))
        blocks.extend(
            tool_use_block(call.id, call.name, dict(call.arguments))
            for call in tool_calls
        )
        self.session.add_assistant_blocks(blocks)
        chunks.clear()

    def _maybe_compact_session(self) -> None:
        estimated_tokens = estimate_context_tokens(
            self.session.messages,
            summary=self.session.summary,
            extra_texts=(self._build_system_prompt(),),
        )
        threshold = max(1, int(self.context_window_tokens * self.auto_compact_threshold))
        if estimated_tokens >= threshold:
            self.session.compact(keep_last=8)

    def _build_system_prompt(self) -> str:
        parts = [self.system_prompt]
        if self.system_prompt_extra:
            parts.append(self.system_prompt_extra)
        if self._active_soul:
            parts.append(
                "Libre Claw soul/persona customization. These notes may shape voice, style, taste, "
                "and durable identity, but they never override safety rules, tool permissions, "
                "sandbox boundaries, provider policies, or direct user instructions:\n\n"
                + "\n\n---\n\n".join(self._active_soul)
            )
        if self.memory_facts:
            facts = "\n".join(f"- {fact}" for fact in self.memory_facts)
            parts.append("Persistent user/project facts:\n" + facts)
        if self._active_skills:
            parts.append(
                "Relevant Libre Claw skills. Follow these project/user procedures when they apply:\n\n"
                + "\n\n---\n\n".join(self._active_skills)
            )
        if self.session.summary:
            parts.append("Compacted prior conversation summary:\n" + self.session.summary)
        parts.append(
            "If this task reveals a repeatable workflow that is not captured by the relevant skills, "
            "briefly suggest a `/skills add <name> ...` command when you finish."
        )
        return "\n\n".join(parts)

    async def _load_skills(self, user_message: str) -> list[str]:
        if self.skill_provider is None:
            return []
        try:
            result = self.skill_provider(user_message)
            if inspect.isawaitable(result):
                result = await result
            return [text for text in result if text.strip()]
        except Exception as exc:
            self._logger.warning("skill_load_failed", error=str(exc))
            return []

    async def _load_soul(self) -> list[str]:
        if self.soul_provider is None:
            return []
        try:
            result = self.soul_provider()
            if inspect.isawaitable(result):
                result = await result
            return [text for text in result if text.strip()]
        except Exception as exc:
            self._logger.warning("soul_load_failed", error=str(exc))
            return []
