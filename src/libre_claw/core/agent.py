# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

import structlog

from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.session import (
    Session,
    UserAttachment,
    estimate_context_tokens,
    text_block,
    tool_result_block,
    tool_use_block,
)
from libre_claw.core.skills import SKILL_AUTHORING_GUIDANCE
from libre_claw.core.tools import ToolCall, ToolRegistry, ToolRegistryError, ToolResult
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    ProviderError,
    StreamEvent,
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
    provider_label: str | None = None


@dataclass(frozen=True)
class AgentFallback:
    provider_label: str
    reason: str
    failed_provider_label: str = "primary"


AgentEvent = (
    AgentTextDelta
    | AgentToolCall
    | AgentToolResult
    | AgentPermissionRequest
    | AgentDone
    | AgentError
    | AgentFallback
)
SkillProvider = Callable[[str], Sequence[str] | Awaitable[Sequence[str]]]
SoulProvider = Callable[[], Sequence[str] | Awaitable[Sequence[str]]]
MemoryProvider = Callable[[str], Sequence[str] | Awaitable[Sequence[str]]]


class _AgentDeadlineReached(TimeoutError):
    """Internal signal used to stop provider streams before the outer runner kills them."""


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
        compact_keep_last: int = 8,
        provider_retry_attempts: int = 0,
        provider_retry_initial_delay: float = 1.0,
        memory_facts: list[str] | None = None,
        system_prompt_extra: str = "",
        skill_provider: SkillProvider | None = None,
        soul_provider: SoulProvider | None = None,
        memory_provider: MemoryProvider | None = None,
        fallback_providers: Sequence[tuple[str, LLMProvider]] | None = None,
        fallback_recheck_after_attempts: int = 3,
        deadline_monotonic: float | None = None,
        deadline_reserve_seconds: float = 0.0,
    ) -> None:
        self.session = session
        self.provider = provider
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.auto_compact_threshold = auto_compact_threshold
        self.context_window_tokens = context_window_tokens
        self.compact_keep_last = max(1, compact_keep_last)
        self.provider_retry_attempts = max(0, provider_retry_attempts)
        self.provider_retry_initial_delay = max(0.0, provider_retry_initial_delay)
        self.memory_facts = memory_facts or []
        self.system_prompt = system_prompt
        self.system_prompt_extra = system_prompt_extra
        self.skill_provider = skill_provider
        self.soul_provider = soul_provider
        self.memory_provider = memory_provider
        self.fallback_providers = tuple(fallback_providers or ())
        self.fallback_recheck_after_attempts = max(1, fallback_recheck_after_attempts)
        self.deadline_monotonic = deadline_monotonic
        self.deadline_reserve_seconds = max(0.0, deadline_reserve_seconds)
        self._tool_schemas = self.tool_registry.schemas()
        self._serialized_tool_schemas = json.dumps(
            self._tool_schemas,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        self._last_provider_input_tokens = 0
        self._active_skills: list[str] = []
        self._active_soul: list[str] = []
        self._active_memory: list[str] = []
        self._logger = structlog.get_logger(__name__)

    async def run(
        self,
        user_message: str,
        attachments: Sequence[UserAttachment] = (),
    ) -> AsyncIterator[AgentEvent]:
        self.session.add_user_message(user_message, attachments=attachments)
        self._active_soul = await self._load_soul()
        self._active_skills = await self._load_skills(user_message)
        self._active_memory = await self._load_memory(user_message)
        total_tool_calls = 0
        turn_usage: Usage | None = None
        provider_chain = (("primary", self.provider), *self.fallback_providers)
        active_provider_index = 0
        fallback_calls_since_recheck = 0

        while True:
            if self._deadline_expired():
                yield AgentError("Run deadline reached before a final response was produced.")
                return
            if active_provider_index > 0 and fallback_calls_since_recheck >= self.fallback_recheck_after_attempts:
                active_provider_index = 0
                fallback_calls_since_recheck = 0
            assistant_chunks: list[str] = []
            tool_calls: list[ToolCall] = []
            provider_failed = False
            provider_error = ""
            provider_attempt = 0
            provider_index = active_provider_index
            active_provider = provider_chain[provider_index][1]

            while True:
                try:
                    self._maybe_compact_session()
                    async for event in self._stream_provider(active_provider):
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
                            if event.usage is not None:
                                self._last_provider_input_tokens = max(
                                    0,
                                    event.usage.input_tokens,
                                )
                            continue

                        if isinstance(event, ProviderError):
                            provider_failed = True
                            provider_error = event.message
                            break
                except _AgentDeadlineReached:
                    self._save_assistant_text(assistant_chunks)
                    yield AgentError(
                        "Run deadline reached before a final response was produced."
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    provider_failed = True
                    provider_error = str(exc)
                    self._logger.warning("agent_stream_failed", error=provider_error)

                if not provider_failed and not assistant_chunks and not tool_calls:
                    provider_failed = True
                    provider_error = "Provider returned no assistant text or tool calls."

                if not provider_failed:
                    break

                can_retry_same_provider = (
                    not assistant_chunks
                    and not tool_calls
                    and provider_attempt < self.provider_retry_attempts
                    and _should_retry_provider_error(provider_error)
                )
                if can_retry_same_provider:
                    provider_attempt += 1
                    retry_delay = _retry_delay(
                        self.provider_retry_initial_delay,
                        provider_attempt,
                    )
                    remaining = self._remaining_seconds()
                    if remaining is not None:
                        retry_delay = min(retry_delay, max(0.0, remaining))
                    if retry_delay > 0:
                        await asyncio.sleep(retry_delay)
                    provider_failed = False
                    provider_error = ""
                    continue

                next_provider_index = provider_index + 1
                if assistant_chunks or tool_calls or next_provider_index >= len(provider_chain):
                    self._save_assistant_text(assistant_chunks)
                    yield AgentError(
                        provider_error,
                        provider_label=provider_chain[provider_index][0],
                    )
                    break

                failed_provider_label = provider_chain[provider_index][0]
                provider_index = next_provider_index
                active_provider_index = provider_index
                active_provider = provider_chain[provider_index][1]
                provider_attempt = 0
                fallback_calls_since_recheck = 0
                yield AgentFallback(
                    provider_label=provider_chain[provider_index][0],
                    reason=provider_error,
                    failed_provider_label=failed_provider_label,
                )
                provider_failed = False
                provider_error = ""

            if provider_failed:
                return

            active_provider_index = provider_index
            if active_provider_index > 0:
                fallback_calls_since_recheck += 1

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
                        remaining = self._remaining_seconds()
                        if remaining is None:
                            resolution = await future
                        elif remaining <= 0:
                            future.cancel()
                            yield AgentError(
                                "Run deadline reached while waiting for tool approval."
                            )
                            return
                        else:
                            resolution = await asyncio.wait_for(future, timeout=remaining)
                    except asyncio.TimeoutError:
                        yield AgentError(
                            "Run deadline reached while waiting for tool approval."
                        )
                        return
                    except asyncio.CancelledError:
                        raise
                    approved = self.permission_manager.apply_resolution(call, resolution)
                    if not approved:
                        immediate_results[call.id] = ToolResult(error="User denied this action")
                        continue

                executable_calls.append(call)

            executed = await self._execute_tools(executable_calls)
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
            extra_texts=(self._build_system_prompt(), self._serialized_tool_schemas),
        )
        estimated_tokens = max(estimated_tokens, self._last_provider_input_tokens)
        threshold = max(1, int(self.context_window_tokens * self.auto_compact_threshold))
        if estimated_tokens >= threshold:
            self.session.compact(keep_last=self.compact_keep_last)
            self._last_provider_input_tokens = 0

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
        memories = _dedupe_texts([*self.memory_facts, *self._active_memory])
        if memories:
            facts = "\n".join(f"- {fact}" for fact in memories)
            parts.append("Relevant persistent memory:\n" + facts)
        if self._active_skills:
            parts.append(
                "Relevant Libre Claw skills. Follow these project/user procedures when they apply:\n\n"
                + "\n\n---\n\n".join(self._active_skills)
            )
        if self.session.summary:
            parts.append("Compacted prior conversation summary:\n" + self.session.summary)
        tool_names = [
            str(schema.get("name", ""))
            for schema in self._tool_schemas
            if str(schema.get("name", "")).strip()
        ]
        if tool_names:
            parts.append("Available tools for this run: " + ", ".join(tool_names) + ".")
        else:
            parts.append("No tools are enabled for this run.")
        if self.skill_provider is not None:
            parts.append(
                SKILL_AUTHORING_GUIDANCE
                + "\n\n"
                "If this task reveals a repeatable workflow that is not captured by the relevant skills, "
                "briefly suggest a `/skills add <name> ...` command when you finish."
            )
        remaining = self._remaining_seconds()
        if remaining is not None:
            parts.append(
                f"Run deadline: about {max(0, int(remaining))} seconds remain. "
                "Prioritize the requested result, stop starting nonessential work as the deadline "
                "approaches, and return the best verified final answer before time expires."
            )
        return "\n\n".join(parts)

    async def _execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        if not calls:
            return []
        remaining = self._remaining_seconds()
        if remaining is None:
            return list(
                await asyncio.gather(*(self.tool_registry.execute(call) for call in calls))
            )
        available = remaining - self.deadline_reserve_seconds
        if available <= 0:
            return [
                ToolResult(
                    error=(
                        "Run deadline is near. Do not call more tools; return the best final answer now."
                    )
                )
                for _ in calls
            ]
        tasks = [
            asyncio.create_task(self.tool_registry.execute(call))
            for call in calls
        ]
        try:
            _, pending = await asyncio.wait(tasks, timeout=available)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        results: list[ToolResult] = []
        for task in tasks:
            if task in pending:
                results.append(
                    ToolResult(
                        error=(
                            "Tool execution was stopped to preserve time for a final answer "
                            "before the run deadline."
                        )
                    )
                )
                continue
            try:
                results.append(task.result())
            except Exception as exc:
                results.append(ToolResult(error=str(exc)))
        return results

    async def _stream_provider(
        self,
        provider: LLMProvider,
    ) -> AsyncIterator[StreamEvent]:
        stream = provider.complete(
            messages=self.session.messages,
            tools=self._tool_schemas,
            system=self._build_system_prompt(),
        ).__aiter__()
        while True:
            remaining = self._remaining_seconds()
            if remaining is not None and remaining <= 0:
                raise _AgentDeadlineReached
            try:
                if remaining is None:
                    event = await anext(stream)
                else:
                    event = await asyncio.wait_for(anext(stream), timeout=remaining)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise _AgentDeadlineReached from exc
            yield event

    def _remaining_seconds(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return self.deadline_monotonic - time.monotonic()

    def _deadline_expired(self) -> bool:
        remaining = self._remaining_seconds()
        return remaining is not None and remaining <= 0

    def resolved_system_prompt(self) -> str:
        """Return the fully resolved prompt used by the current agent turn."""
        return self._build_system_prompt()

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

    async def _load_memory(self, user_message: str) -> list[str]:
        if self.memory_provider is None:
            return []
        try:
            result = self.memory_provider(user_message)
            if inspect.isawaitable(result):
                result = await result
            return [text for text in result if text.strip()]
        except Exception as exc:
            self._logger.warning("memory_load_failed", error=str(exc))
            return []


def _dedupe_texts(texts: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for text in texts:
        cleaned = " ".join(text.split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _should_retry_provider_error(message: str) -> bool:
    text = message.lower()
    retry_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "connection",
        "connecterror",
        "network",
        "no assistant text or tool calls",
        "overloaded",
        "rate limit",
        "readerror",
        "retry",
        "temporarily",
        "timeout",
        "timed out",
        "transport",
    )
    non_retry_markers = (
        "api key",
        "authentication",
        "invalid model",
        "not a valid model",
        "permission",
        "unauthorized",
    )
    return any(marker in text for marker in retry_markers) and not any(
        marker in text for marker in non_retry_markers
    )


def _retry_delay(initial_delay: float, attempt: int) -> float:
    if initial_delay <= 0:
        return 0.0
    return min(initial_delay * (2 ** max(0, attempt - 1)), 8.0)
