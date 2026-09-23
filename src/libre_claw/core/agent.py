# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from libre_claw.core.instructions import InstructionLoader, ProjectInstruction, render_instructions, tool_paths
from libre_claw.core.cordis_engine import CordisEngine, CordisEngineError
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.questions import AgentUserQuestionRequest, validate_questions
from libre_claw.core.session import (
    ContentBlock,
    Session,
    UserAttachment,
    estimate_context_tokens,
    image_block,
    provider_reasoning_block,
    text_block,
    tool_result_block,
    tool_use_block,
)
from libre_claw.core.skills import SKILL_AUTHORING_GUIDANCE
from libre_claw.core.tools import ToolCall, ToolRegistry, ToolRegistryError, ToolResult
from libre_claw.providers.base import (
    CacheableSystemPrompt,
    Done,
    LLMProvider,
    ProviderError,
    ReasoningDelta,
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
class AgentSubagentUpdate:
    snapshot: dict[str, Any]


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
    | AgentSubagentUpdate
    | AgentUserQuestionRequest
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
        checkpoint_callback: Callable[[Session], Awaitable[None]] | None = None,
        usage_callback: Callable[[Usage], Awaitable[None]] | None = None,
        engine: CordisEngine | None = None,
    ) -> None:
        self.session = session
        self.engine = engine
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
        self._deadline_prompt = ""
        if deadline_monotonic is not None:
            budget = max(0.0, deadline_monotonic - time.monotonic())
            deadline = datetime.now(timezone.utc) + timedelta(seconds=budget)
            # A changing countdown invalidates the entire conversation cache on
            # every request. Execution still enforces the live monotonic deadline.
            self._deadline_prompt = (
                f"Run deadline: {deadline.isoformat(timespec='seconds')} UTC. "
                f"The starting time budget was about {int(budget)} seconds; this is not the remaining time. "
                "Prioritize the requested result, stop starting nonessential work as the deadline "
                "approaches, and return the best verified final answer before time expires."
            )
        self.checkpoint_callback = checkpoint_callback
        self.usage_callback = usage_callback
        self.control_events: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._checkpoint_lock = asyncio.Lock()
        context = self.tool_registry.context
        self._instruction_loader = InstructionLoader(context.working_directory) if context else None
        self._instruction_paths: list[Path] = []
        self._active_instructions: list[ProjectInstruction] = []
        self._known_instructions: dict[Path, str] = {}
        self.accepting_control = False
        self.subagents = None
        if context is not None:
            context.shared_state["agent_session"] = session
            context.shared_state["user_question_handler"] = self.request_user_questions
            if "subagent_spawn" in tool_registry:
                from libre_claw.core.subagents import SubagentManager
                self.subagents = SubagentManager(self)
                context.shared_state["subagent_manager"] = self.subagents
        self._tool_schemas: list[dict[str, Any]] = []
        self._serialized_tool_schemas = "[]"
        self._refresh_tool_schemas(provider)
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
        if self.engine is None:
            async with aclosing(self._run_local(user_message, attachments)) as stream:
                async for event in stream:
                    yield event
            return
        try:
            async with aclosing(self.engine.stream("agent", "run",
                    handler=lambda: self._run_local(user_message, attachments))) as stream:
                async for event in stream:
                    yield event
        except CordisEngineError as exc:
            yield AgentError(message=str(exc))

    async def _run_local(
        self, user_message: str, attachments: Sequence[UserAttachment],
    ) -> AsyncIterator[AgentEvent]:
        self.accepting_control = True
        if self.subagents is not None:
            self.subagents.begin_turn()
        context = self.tool_registry.context
        if context is not None:
            context.shared_state["agent_session"] = self.session
            context.shared_state["checkpoint_callback"] = self.checkpoint_callback
            context.shared_state["user_question_handler"] = self.request_user_questions
            if self.subagents is not None:
                context.shared_state["subagent_manager"] = self.subagents
        interrupted = False
        try:
            if self.subagents is not None:
                async for event in self.subagents.resume_pending():
                    yield event
            async with aclosing(self._run_turn(user_message, attachments)) as turn:
                async for event in turn:
                    yield event
        except (asyncio.CancelledError, GeneratorExit):
            interrupted = True
            raise
        finally:
            self.accepting_control = False
            if self.subagents is not None:
                await self.subagents.close(interrupted=interrupted)
            await self._checkpoint()
        if self.subagents is not None:
            while not self.subagents.events.empty():
                event = self.subagents.events.get_nowait()
                if not isinstance(event, (AgentPermissionRequest, AgentUserQuestionRequest)) or not event.future.done():
                    yield event

    async def _checkpoint(self) -> None:
        if self.checkpoint_callback is not None:
            async with self._checkpoint_lock:
                if self.engine is None:
                    await self.checkpoint_callback(self.session)
                else:
                    await self.engine.call("sessions", "checkpoint", handler=lambda: self.checkpoint_callback(self.session))

    async def request_user_questions(self, questions: Any) -> dict[str, Any]:
        request = AgentUserQuestionRequest(uuid.uuid4().hex, validate_questions(questions),
            asyncio.get_running_loop().create_future())
        await self.control_events.put(request)
        try:
            return await request.future
        finally:
            if not request.future.done():
                request.future.cancel()

    async def _run_turn(
        self, user_message: str, attachments: Sequence[UserAttachment],
    ) -> AsyncIterator[AgentEvent]:
        self.session.recover_interrupted_tools()
        self.session.add_user_message(user_message, attachments=attachments)
        await self._checkpoint()
        await self._refresh_instructions()
        self._active_soul = await self._load_soul()
        self._active_skills = await self._load_skills(user_message)
        self._active_memory = await self._load_memory(user_message)
        total_tool_calls = 0
        turn_usage: Usage | None = None
        provider_chain = (("primary", self.provider), *self.fallback_providers)
        active_provider_index = 0
        fallback_calls_since_recheck = 0
        metadata_loaded: set[int] = set()

        while True:
            if self.subagents is not None:
                async for event in self.subagents.resume_pending():
                    yield event
            steering = self.session.consume_steering()
            for message in steering:
                self.session.add_user_message(message)
                if self.subagents is not None:
                    self.subagents.steer(message)
            if steering:
                await self._checkpoint()
            await self._refresh_instructions()
            if self._deadline_expired():
                yield AgentError("Run deadline reached before a final response was produced.")
                return
            if active_provider_index > 0 and fallback_calls_since_recheck >= self.fallback_recheck_after_attempts:
                active_provider_index = 0
                fallback_calls_since_recheck = 0
            assistant_chunks: list[str] = []
            reasoning_chunks: list[ReasoningDelta] = []
            tool_calls: list[ToolCall] = []
            provider_failed = False
            provider_error = ""
            provider_attempt = 0
            provider_index = active_provider_index
            active_provider = provider_chain[provider_index][1]

            while True:
                try:
                    if id(active_provider) not in metadata_loaded:
                        await self._ensure_provider_metadata(active_provider)
                        metadata_loaded.add(id(active_provider))
                    self._maybe_compact_session(active_provider)
                    async with aclosing(self._stream_provider(active_provider)) as provider_stream:
                        async for event in provider_stream:
                            if isinstance(event, TextDelta):
                                assistant_chunks.append(event.text)
                                yield AgentTextDelta(event.text)
                                continue

                            if isinstance(event, ReasoningDelta):
                                reasoning_chunks.append(event)
                                continue

                            if isinstance(event, ToolCallReady):
                                call = ToolCall(id=event.tool_call_id, name=event.name, arguments=event.input)
                                tool_calls.append(call)
                                yield AgentToolCall(call)
                                continue

                            if isinstance(event, Done):
                                turn_usage = combine_usage(turn_usage, event.usage)
                                if event.usage is not None:
                                    if self.usage_callback is not None:
                                        await self.usage_callback(event.usage)
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
                    if assistant_chunks:
                        self._save_assistant_text(assistant_chunks, reasoning_chunks)
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
                    reasoning_chunks.clear()
                    continue

                next_provider_index = provider_index + 1
                if assistant_chunks or tool_calls or next_provider_index >= len(provider_chain):
                    if assistant_chunks:
                        self._save_assistant_text(assistant_chunks, reasoning_chunks)
                    yield AgentError(
                        provider_error,
                        provider_label=provider_chain[provider_index][0],
                    )
                    break

                failed_provider_label = provider_chain[provider_index][0]
                provider_index = next_provider_index
                active_provider = provider_chain[provider_index][1]
                provider_attempt = 0
                fallback_calls_since_recheck = 0
                reasoning_chunks.clear()
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
                self._save_assistant_text(assistant_chunks, reasoning_chunks)
                await self._checkpoint()
                if self.session.pending_steering or self.session.pending_subagent_resumes:
                    continue
                if self.subagents is not None:
                    turn_usage = combine_usage(turn_usage, self.subagents.total_usage())
                self.accepting_control = False
                yield AgentDone(turn_usage)
                return

            total_tool_calls += len(tool_calls)
            if total_tool_calls > self.max_tool_calls_per_turn:
                yield AgentError(f"Stopped after exceeding {self.max_tool_calls_per_turn} tool calls in one turn.")
                return

            self._save_assistant_tool_request(assistant_chunks, reasoning_chunks, tool_calls)
            await self._checkpoint()

            immediate_results: dict[str, ToolResult] = {}
            executable_calls: list[ToolCall] = []

            for call in tool_calls:
                try:
                    tool = self.tool_registry.get(call.name)
                except ToolRegistryError as exc:
                    immediate_results[call.id] = ToolResult(error=str(exc))
                    continue

                if getattr(self.session, "mode", "default") == "plan" and not tool.is_read_only(call.arguments):
                    immediate_results[call.id] = ToolResult(error="Plan mode permits read-only tools. Switch to default mode to make changes.")
                    continue

                if self.session.pending_steering:
                    immediate_results[call.id] = ToolResult(error="New user guidance is pending. Reconsider this action after reading the next user message.")
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

            execution = asyncio.create_task(self._execute_tools(executable_calls))
            queued: asyncio.Task[AgentEvent] | None = None
            try:
                while not execution.done():
                    queued = asyncio.create_task(self.control_events.get())
                    done, _ = await asyncio.wait((execution, queued), return_when=asyncio.FIRST_COMPLETED)
                    if queued in done:
                        event = queued.result()
                        if not isinstance(event, (AgentPermissionRequest, AgentUserQuestionRequest)) or not event.future.done():
                            yield event
                    else:
                        queued.cancel()
                        await asyncio.gather(queued, return_exceptions=True)
                while not self.control_events.empty():
                    event = self.control_events.get_nowait()
                    if not isinstance(event, (AgentPermissionRequest, AgentUserQuestionRequest)) or not event.future.done():
                        yield event
                executed = execution.result()
            finally:
                if queued is not None and not queued.done():
                    queued.cancel()
                    await asyncio.gather(queued, return_exceptions=True)
                if not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
            for call, result in zip(executable_calls, executed, strict=True):
                immediate_results[call.id] = result

            ordered_results = [(call, immediate_results[call.id]) for call in tool_calls]
            result_blocks = [
                tool_result_block(call.id, result.as_text(), is_error=result.is_error)
                for call, result in ordered_results
            ]
            result_blocks.extend(
                image_block(attachment)
                for _, result in ordered_results
                for attachment in result.attachments
            )
            self.session.add_tool_result_blocks(result_blocks)
            await self._checkpoint()

            for call, result in ordered_results:
                yield AgentToolResult(call=call, result=result)
            for call, result in ordered_results:
                if result.is_error or not call.name.startswith("cordis__"):
                    continue
                for deferred in result.metadata.get("additional_contexts", []):
                    text = deferred if isinstance(deferred, str) else json.dumps(deferred, ensure_ascii=False)
                    self.session.add_user_message(f"Additional context from plugin tool {call.name}:\n{text}")
            if any(not result.is_error and call.name.startswith("cordis__") and result.metadata.get("concludes_turn") is True
                   for call, result in ordered_results):
                self.accepting_control = False
                await self._checkpoint()
                yield AgentDone(turn_usage)
                return

    def _save_assistant_text(
        self,
        chunks: list[str],
        reasoning_chunks: list[ReasoningDelta] | None = None,
    ) -> None:
        blocks = _provider_reasoning_blocks(reasoning_chunks or [])
        text = "".join(chunks)
        if text:
            blocks.append(text_block(text))
        self.session.add_assistant_blocks(blocks)
        chunks.clear()
        if reasoning_chunks is not None:
            reasoning_chunks.clear()

    def _save_assistant_tool_request(
        self,
        chunks: list[str],
        reasoning_chunks: list[ReasoningDelta],
        tool_calls: list[ToolCall],
    ) -> None:
        blocks = _provider_reasoning_blocks(reasoning_chunks)
        text = "".join(chunks)
        if text:
            blocks.append(text_block(text))
        blocks.extend(
            tool_use_block(call.id, call.name, dict(call.arguments))
            for call in tool_calls
        )
        self.session.add_assistant_blocks(blocks)
        chunks.clear()
        reasoning_chunks.clear()

    def _maybe_compact_session(self, provider: LLMProvider | None = None) -> None:
        provider = provider or self.provider
        self._refresh_tool_schemas(provider)
        estimated_tokens = estimate_context_tokens(
            self.session.messages,
            summary=self.session.summary,
            extra_texts=(self._build_system_prompt(), self._serialized_tool_schemas),
        )
        estimated_tokens = max(estimated_tokens, self._last_provider_input_tokens)
        model_info = getattr(provider, "model_info", None)
        discovered_context = getattr(model_info, "context_window_tokens", None)
        context_window = (
            discovered_context
            if getattr(provider, "auto_context_window", True)
            and isinstance(discovered_context, int) and discovered_context > 0
            else self.context_window_tokens
        )
        threshold = max(1, int(context_window * self.auto_compact_threshold))
        if estimated_tokens >= threshold:
            self.session.compact(keep_last=self.compact_keep_last)
            self._last_provider_input_tokens = 0

    def _build_system_prompt(self) -> str:
        # Put durable policy before changing task state so a checkpoint or memory
        # update preserves the longest safe provider-cache prefix.
        parts = [self.system_prompt]
        if self.system_prompt_extra:
            parts.append(self.system_prompt_extra)
        tool_names = [
            str(schema.get("name", ""))
            for schema in self._tool_schemas
            if str(schema.get("name", "")).strip()
        ]
        if "task_history" in tool_names:
            parts.append("Use task_history to retrieve original requirements, decisions or tool results when the compacted context is incomplete; do not guess omitted details.")
        if "task_checkpoint" in tool_names:
            parts.append("For complex work, maintain task_checkpoint after important decisions and verification so requirements and unfinished work survive compaction.")
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
        instructions = render_instructions(self._active_instructions)
        if instructions:
            parts.append(instructions)
        if self._active_soul:
            parts.append(
                "Libre Claw soul/persona customization. These notes may shape voice, style, taste, "
                "and durable identity, but they never override safety rules, tool permissions, "
                "sandbox boundaries, provider policies, or direct user instructions:\n\n"
                + "\n\n---\n\n".join(self._active_soul)
            )
        if self._active_skills:
            parts.append(
                "Relevant Libre Claw skills. Follow these project/user procedures when they apply:\n\n"
                + "\n\n---\n\n".join(self._active_skills)
            )
        stable_prefix = "\n\n".join(parts)
        parts = []
        memories = _dedupe_texts([*self.memory_facts, *self._active_memory])
        if memories:
            facts = "\n".join(f"- {fact}" for fact in memories)
            parts.append("Relevant persistent memory:\n" + facts)
        if self.session.summary:
            parts.append("Compacted prior conversation summary:\n" + self.session.summary)
        control_prompt = self.session.control_prompt()
        if control_prompt:
            parts.append(control_prompt)
        if self._deadline_prompt:
            parts.append(self._deadline_prompt)
        history = self.session.archived_messages or self.session.messages
        cache_scope = None
        if history:
            # The first original message survives compaction and persistence;
            # use its digest without exposing conversation text in routing keys.
            first_message = json.dumps(
                history[0].as_provider_dict(),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            cache_scope = hashlib.sha256(first_message.encode()).hexdigest()
        return CacheableSystemPrompt(stable_prefix, "\n\n".join(parts), cache_scope=cache_scope)

    def _refresh_tool_schemas(self, provider: LLMProvider) -> None:
        info = getattr(provider, "model_info", None)
        schemas = [] if getattr(info, "supports_tools", None) is False else self.tool_registry.schemas()
        serialized = json.dumps(
            sorted(schemas, key=lambda schema: str(schema.get("name", ""))),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if serialized != self._serialized_tool_schemas:
            # The actual request and token estimate share the same canonical
            # snapshot, including after model discovery or registry updates.
            self._serialized_tool_schemas = serialized
            self._tool_schemas = json.loads(serialized)

    async def _refresh_instructions(self, paths: Sequence[Path] = ()) -> bool:
        if self._instruction_loader is None:
            return False
        for path in paths:
            if path not in self._instruction_paths:
                self._instruction_paths.append(path)
        self._instruction_paths = self._instruction_paths[-32:]
        instructions = await asyncio.to_thread(self._instruction_loader.load, self._instruction_paths)
        current = {item.path: item.fingerprint for item in instructions}
        changed = any(self._known_instructions.get(path) != digest for path, digest in current.items())
        self._active_instructions = instructions
        self._known_instructions = current
        return changed

    async def _execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        if not calls:
            return []
        context = self.tool_registry.context
        paths = [path for call in calls for path in tool_paths(call.arguments, context.working_directory)] if context else []
        instructions_changed = await self._refresh_instructions(paths)

        async def dispatch(call: ToolCall) -> ToolResult:
            if self.engine is None:
                return await self.tool_registry.execute(call)
            return await self.engine.call("tools", "execute", handler=lambda: self.tool_registry.execute(call))

        async def execute_one(call: ToolCall) -> ToolResult:
            if self.session.pending_steering:
                return ToolResult(error="New user guidance is pending. Reconsider this action after reading the next user message.")
            tool = self.tool_registry.get(call.name)
            if getattr(self.session, "mode", "default") == "plan" and not tool.is_read_only(call.arguments):
                return ToolResult(error="Plan mode permits read-only tools. Switch to default mode to make changes.")
            if not tool.is_read_only(call.arguments):
                refreshed = await self._refresh_instructions()
                if instructions_changed or refreshed:
                    return ToolResult(error="Additional or changed project instructions were loaded for these paths. Review the scoped instructions in the system prompt and retry this action.")
            if self.subagents is not None:
                error = self.subagents.ownership_error(call)
                if error:
                    return ToolResult(error=error)
            if call.name in {"write_file", "edit_file", "apply_patch"}:
                # File writes run in worker threads; cancellation cannot stop those threads.
                # Finish the in-flight operation before releasing worker ownership.
                operation = asyncio.create_task(dispatch(call))
                try:
                    return await asyncio.shield(operation)
                except asyncio.CancelledError:
                    await operation
                    raise
            return await dispatch(call)

        async def execute_group(group: list[ToolCall]) -> list[ToolResult]:
            remaining = self._remaining_seconds()
            available = None if remaining is None else remaining - self.deadline_reserve_seconds
            if available is not None and available <= 0:
                return [ToolResult(error="Run deadline is near. Do not call more tools; return the best final answer now.") for _ in group]
            tasks = [asyncio.create_task(execute_one(call)) for call in group]
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
            results = []
            for task in tasks:
                if task in pending:
                    results.append(ToolResult(error="Tool execution was stopped to preserve time for a final answer before the run deadline."))
                else:
                    try:
                        results.append(task.result())
                    except Exception as exc:
                        results.append(ToolResult(error=str(exc)))
            return results

        # A mixed batch may contain dependencies through filesystem or process state.
        # Only explicitly read-only batches run concurrently; shared browser/control state is serial.
        concurrent = all(
            self.tool_registry.get(call.name).is_read_only(call.arguments)
            and not call.name.startswith(("subagent_", "browser_"))
            for call in calls
        )
        if concurrent:
            return await execute_group(calls)
        results = []
        for call in calls:
            results.extend(await execute_group([call]))
        return results

    async def _ensure_provider_metadata(self, provider: LLMProvider) -> None:
        ensure = getattr(provider, "ensure_model_info", None)
        if not callable(ensure):
            return
        remaining = self._remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise _AgentDeadlineReached
        try:
            result = ensure()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=min(15.0, remaining) if remaining is not None else 15.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning("model_metadata_unavailable", error=str(exc))

    async def _stream_provider(
        self,
        provider: LLMProvider,
    ) -> AsyncIterator[StreamEvent]:
        if self.engine is None:
            async with aclosing(self._stream_provider_local(provider)) as stream:
                async for event in stream:
                    yield event
            return
        async with aclosing(self.engine.stream("providers", "complete",
                handler=lambda: self._stream_provider_local(provider))) as stream:
            async for event in stream:
                yield event

    async def _stream_provider_local(self, provider: LLMProvider) -> AsyncIterator[StreamEvent]:
        if getattr(self.session, "mode", "default") == "plan":
            from libre_claw.providers.codex import CodexProvider
            if isinstance(provider, CodexProvider):
                provider = copy.copy(provider)
                provider.sandbox = "read-only"
            elif hasattr(provider, "sandbox") or hasattr(provider, "approval_policy"):
                yield ProviderError("Plan mode cannot enforce read-only execution for this native-tool provider.")
                return
        stream = provider.complete(
            messages=self.session.messages,
            tools=self._tool_schemas,
            system=self._build_system_prompt(),
        ).__aiter__()
        try:
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
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()

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
        if self.engine is not None:
            if not self.engine.is_enabled("memory"):
                return []
            return await self.engine.call("memory", "load", handler=lambda: self._load_memory_local(user_message))
        return await self._load_memory_local(user_message)

    async def _load_memory_local(self, user_message: str) -> list[str]:
        try:
            result = self.memory_provider(user_message)
            if inspect.isawaitable(result):
                result = await result
            return [text for text in result if text.strip()]
        except Exception as exc:
            self._logger.warning("memory_load_failed", error=str(exc))
            return []


def _provider_reasoning_blocks(chunks: Sequence[ReasoningDelta]) -> list[ContentBlock]:
    grouped: dict[str, list[str]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.provider, []).append(chunk.text)
    return [
        provider_reasoning_block("".join(parts), provider)
        for provider, parts in grouped.items()
    ]


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
