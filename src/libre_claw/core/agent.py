# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog

from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.session import Session, text_block, tool_result_block, tool_use_block
from libre_claw.core.tools import ToolCall, ToolRegistry, ToolRegistryError, ToolResult
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    ProviderError,
    TextDelta,
    ToolCallReady,
    Usage,
)


DEFAULT_SYSTEM_PROMPT = """You are Libre Claw, an autonomous coding agent running in the user's terminal.
You have access to tools for reading files, writing files, editing files, listing directories, and running shell commands.

RULES:
- Always read before editing. Understand the codebase before making changes.
- Make minimal, surgical edits. Never rewrite entire files when a targeted fix suffices.
- Explain what you're about to do before doing it, but keep it brief.
- If a task is ambiguous, make a reasonable assumption, proceed, and note the assumption.
- After making changes, verify them with available commands unless the user says otherwise.
- Never delete files or run destructive commands without explicit user approval.
- When you're done, summarize what you changed and why.

Current toolset: read_file, write_file, edit_file, list_directory, and bash."""


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


class Agent:
    """ReAct-style agent loop with client-side tools."""

    def __init__(
        self,
        session: Session,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        permission_manager: PermissionManager,
        max_tool_calls_per_turn: int = 50,
        auto_compact_threshold: float = 0.8,
        memory_facts: list[str] | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.session = session
        self.provider = provider
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.auto_compact_threshold = auto_compact_threshold
        self.memory_facts = memory_facts or []
        self.system_prompt = system_prompt
        self._logger = structlog.get_logger(__name__)

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        self.session.add_user_message(user_message)
        total_tool_calls = 0
        latest_usage: Usage | None = None

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
                        latest_usage = event.usage
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
                yield AgentDone(latest_usage)
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
        # There is no tokenizer-backed context estimator yet, so use message
        # count as the working-memory proxy.
        threshold = max(8, int(20 * self.auto_compact_threshold))
        if len(self.session.messages) > threshold:
            self.session.compact(keep_last=8)

    def _build_system_prompt(self) -> str:
        parts = [self.system_prompt]
        if self.memory_facts:
            facts = "\n".join(f"- {fact}" for fact in self.memory_facts)
            parts.append("Persistent user/project facts:\n" + facts)
        if self.session.summary:
            parts.append("Compacted prior conversation summary:\n" + self.session.summary)
        return "\n\n".join(parts)
