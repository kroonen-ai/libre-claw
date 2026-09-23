# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from libre_claw import __version__
from libre_claw.config import LibreClawConfig
from libre_claw.core.atif import RecordingSession, write_atif_trajectory
from libre_claw.core.agent import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
)
from libre_claw.core.memory import MemoryItem, MemoryStore
from libre_claw.core.cordis_engine_plugins import engine_for
from libre_claw.core.cordis_bindings import BoundStore, MEMORY_METHODS
from libre_claw.core.cordis import CordisManager
from libre_claw.core.orchestration_setup import (
    attach_orchestration, orchestration_provider_settings, orchestration_registry, prepare_orchestration,
)
from libre_claw.core.questions import AgentUserQuestionRequest
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session
from libre_claw.core.skills import SkillStore
from libre_claw.core.soul import SoulStore
from libre_claw.core.tools import ToolRegistry
from libre_claw.providers.base import LLMProvider, Usage
from libre_claw.providers.factory import create_fallback_providers, create_provider
from libre_claw.providers.moonshot_metadata import apply_moonshot_model_limits
from libre_claw.tools_builtin import create_builtin_registry, bind_cordis_manager


TextCallback = Callable[[str], None]


@dataclass(frozen=True)
class HeadlessRunResult:
    """Outcome of one noninteractive Libre Claw turn."""

    text: str
    usage: Usage | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


async def run_headless(
    config: LibreClawConfig,
    user_message: str,
    *,
    auto_approve: bool = False,
    system_prompt_extra: str = "",
    on_text: TextCallback | None = None,
    provider: LLMProvider | None = None,
    tool_registry: ToolRegistry | None = None,
    memory_store: MemoryStore | None = None,
    trajectory_path: Path | None = None,
    trajectory_agent_version: str | None = None,
    trajectory_reasoning_effort: str | None = None,
    deadline_seconds: float | None = None,
    deadline_reserve_seconds: float = 0.0,
    orchestration_plugin: str = "",
) -> HeadlessRunResult:
    """Run one complete agent turn without a TUI or daemon."""
    if deadline_seconds is not None and deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be greater than zero")
    if deadline_reserve_seconds < 0:
        raise ValueError("deadline_reserve_seconds must not be negative")
    if deadline_seconds is not None and deadline_reserve_seconds >= deadline_seconds:
        raise ValueError("deadline_reserve_seconds must be smaller than deadline_seconds")

    if config.general.default_provider.lower() == "moonshot":
        config = apply_moonshot_model_limits(config)

    cordis = CordisManager(config=config, tool_timeout=config.cordis.tool_timeout, persistent=True)
    engine = engine_for(lambda: config)
    started = False
    try:
        session = RecordingSession() if trajectory_path is not None else Session()
        if orchestration_plugin:
            config = await asyncio.to_thread(prepare_orchestration, config, cordis, session, selected=orchestration_plugin)
        cordis.config = config
        store = BoundStore(memory_store or MemoryStore(), lambda: engine, MEMORY_METHODS)
        registry = orchestration_registry(tool_registry or create_builtin_registry(config, store), session)
        bind_cordis_manager(registry, cordis)
        permissions = PermissionManager(config.permissions)
        if auto_approve:
            permissions.allow_tools_for_session(_tool_names(registry))

        memory_facts: list[str] = []
        memory_provider = None
        if config.memory.enabled:
            memory_facts = await store.list_always_injected_memories()
            if config.memory.inject_relevant:
                memory_provider = lambda message: _relevant_memory_texts(config, store, message)

        skill_provider = None
        if config.skills.enabled:
            skill_store = SkillStore(config.general.working_directory, skills_config=config.skills)
            skill_provider = skill_store.relevant_skill_texts

        soul_store = SoulStore(config.general.working_directory)
        fallback_providers = create_fallback_providers(config) if provider is None else ()
        extra = "\n\n".join(
            part.strip()
            for part in (config.agent.system_prompt_extra, system_prompt_extra)
            if part.strip()
        )
        agent = Agent(
            engine=engine,
            session=session,
            provider=provider or create_provider(orchestration_provider_settings(config, session)),
            tool_registry=registry,
            permission_manager=permissions,
            system_prompt=config.agent.system_prompt,
            max_tool_calls_per_turn=config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=config.agent.auto_compact_threshold,
            context_window_tokens=config.agent.context_window_tokens,
            compact_keep_last=config.agent.compact_keep_last,
            provider_retry_attempts=config.agent.provider_retry_attempts,
            provider_retry_initial_delay=config.agent.provider_retry_initial_delay,
            memory_facts=memory_facts,
            system_prompt_extra=extra,
            skill_provider=skill_provider,
            soul_provider=soul_store.soul_texts,
            memory_provider=memory_provider,
            fallback_providers=tuple(
                (fallback.label, fallback.provider) for fallback in fallback_providers
            ),
            fallback_recheck_after_attempts=config.fallback.recheck_after_attempts,
            deadline_monotonic=(
                time.monotonic() + deadline_seconds
                if deadline_seconds is not None
                else None
            ),
            deadline_reserve_seconds=deadline_reserve_seconds,
        )
        cordis.engine = lambda: agent.engine
        attach_orchestration(agent, config, cordis)

        chunks: list[str] = []
        usage: Usage | None = None
        error: str | None = None
        trajectory_id = f"libre-claw-{uuid.uuid4().hex}"

        def checkpoint(checkpoint_error: str | None, *, strict: bool = True) -> None:
            if trajectory_path is None:
                return
            if not isinstance(session, RecordingSession):
                raise RuntimeError("ATIF export requires a recording session.")
            try:
                write_atif_trajectory(
                    trajectory_path,
                    session=session,
                    system_prompt=agent.resolved_system_prompt(),
                    agent_version=trajectory_agent_version or __version__,
                    model_name=config.general.default_model,
                    tool_schemas=registry.schemas(),
                    usage=usage,
                    error=checkpoint_error,
                    reasoning_effort=trajectory_reasoning_effort,
                    trajectory_id=trajectory_id,
                )
            except OSError:
                if strict:
                    raise

        if isinstance(session, RecordingSession):
            async def checkpoint_trajectory(_: Session) -> None:
                checkpoint(
                    "Run is still in progress; this is the latest durable checkpoint.",
                    strict=False,
                )
            # Agent checkpoints are already dispatched through sessions; keep
            # the recording class itself free of an unawaited async callback.
            agent.checkpoint_callback = checkpoint_trajectory
            await engine.call("sessions", "checkpoint", handler=lambda: checkpoint_trajectory(session))

        started = True
        try:
            async with aclosing(agent.run(user_message)) as stream:
                async for event in stream:
                    if isinstance(event, AgentUserQuestionRequest):
                        if not event.future.done():
                            event.future.set_exception(ValueError("User questions require an interactive dashboard or TUI."))
                    elif isinstance(event, AgentTextDelta):
                        chunks.append(event.text)
                        if on_text is not None:
                            on_text(event.text)
                    elif isinstance(event, AgentPermissionRequest):
                        resolution = "always_allow_tool" if auto_approve else "deny"
                        if not event.future.done():
                            event.future.set_result(resolution)
                    elif isinstance(event, AgentDone):
                        usage = event.usage
                    elif isinstance(event, AgentError):
                        error = event.message
        except asyncio.CancelledError:
            checkpoint("Run was cancelled before completion.", strict=False)
            raise
        except Exception as exc:
            checkpoint(f"Run stopped unexpectedly: {exc}", strict=False)
            raise

        if isinstance(session, RecordingSession):
            session.set_checkpoint_callback(None)
        if trajectory_path is not None:
            if not isinstance(session, RecordingSession):
                raise RuntimeError("ATIF export requires a recording session.")
            await engine.call("sessions", "export", handler=lambda: checkpoint(error))

        return HeadlessRunResult(text="".join(chunks).strip(), usage=usage, error=error)
    except (ValueError, RuntimeError, OSError) as exc:
        if started:
            raise
        return HeadlessRunResult(text="", error=f"Could not start task: {exc}")
    finally:
        try:
            await cordis.aclose()
        finally:
            await engine.aclose()


def _tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    return tuple(
        name
        for schema in registry.schemas()
        if isinstance((name := schema.get("name")), str) and name
    )


async def _relevant_memory_texts(
    config: LibreClawConfig,
    store: MemoryStore,
    user_message: str,
) -> list[str]:
    items = await store.search_memory_items(
        user_message,
        project_root=config.general.working_directory,
        limit=max(1, config.memory.max_injected_items),
    )
    return _memory_texts_with_budget(items, config.memory.max_injected_tokens)


def _memory_texts_with_budget(items: Sequence[MemoryItem], max_tokens: int) -> list[str]:
    budget = max(1, max_tokens) * 4
    selected: list[str] = []
    used = 0
    for item in items:
        text = f"[{item.kind}/{item.scope}] {item.text}"
        if selected and used + len(text) > budget:
            break
        remaining = max(0, budget - used)
        if not remaining:
            break
        selected.append(text[:remaining])
        used += min(len(text), remaining)
    return selected
