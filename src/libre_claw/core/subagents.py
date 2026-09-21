# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from libre_claw.core.instructions import tool_paths
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.session import Session, session_from_payload, session_to_payload
from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolRegistry, ToolResult
from libre_claw.providers.base import Usage, combine_usage

if TYPE_CHECKING:
    from libre_claw.core.agent import Agent, AgentEvent


FILE_WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch"})
ACTIVE_STATES = frozenset({"running", "blocked"})
MAX_OUTPUT_CHARS = 16_000


@dataclass
class SubagentState:
    id: str
    task: str
    scope: Path
    read_only: bool
    write_paths: tuple[Path, ...]
    provider: str
    model: str
    max_tool_calls: int
    max_seconds: float
    session: Session = field(default_factory=Session)
    status: str = "running"
    output: str = ""
    error: str | None = None
    tool_calls: int = 0
    usage: Usage | None = None
    created_at: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    started_monotonic: float | None = field(default=None, repr=False)
    parent_guidance: tuple[str, ...] = field(default=(), repr=False)
    task_handle: asyncio.Task[None] | None = field(default=None, repr=False)

    def elapsed(self) -> float:
        return self.elapsed_seconds + (max(0.0, time.monotonic() - self.started_monotonic) if self.started_monotonic is not None else 0.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id, "task": self.task, "scope": str(self.scope),
            "read_only": self.read_only, "write_paths": [str(path) for path in self.write_paths],
            "provider": self.provider, "model": self.model, "status": self.status,
            "output": self.output, "error": self.error, "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls, "max_seconds": self.max_seconds,
            "created_at": self.created_at, "elapsed_seconds": self.elapsed(),
            "usage": asdict(self.usage) if self.usage is not None else None,
        }

    def durable_snapshot(self) -> dict[str, Any]:
        return {**self.snapshot(), "session": copy.deepcopy(session_to_payload(self.session))}

    @classmethod
    def restore(cls, value: dict[str, Any]) -> SubagentState:
        status = str(value.get("status", "interrupted"))
        if status in ACTIVE_STATES:
            status = "interrupted"
        usage = value.get("usage")
        state = cls(
            id=str(value["id"]), task=str(value["task"]), scope=Path(value["scope"]),
            read_only=value["read_only"], write_paths=tuple(Path(path) for path in value.get("write_paths", [])),
            provider=str(value.get("provider", "")), model=str(value.get("model", "")),
            max_tool_calls=int(value["max_tool_calls"]), max_seconds=float(value["max_seconds"]),
            session=session_from_payload(value.get("session")), status=status,
            output=str(value.get("output", ""))[-MAX_OUTPUT_CHARS:], error=value.get("error"),
            tool_calls=max(0, int(value.get("tool_calls", 0))),
            elapsed_seconds=max(0.0, float(value.get("elapsed_seconds", 0))),
            created_at=float(value.get("created_at", time.time())),
            usage=Usage(**{key: usage[key] for key in Usage.__dataclass_fields__ if key in usage}) if isinstance(usage, dict) else None,
        )
        if status == "interrupted":
            state.session.recover_interrupted_tools()
            state.error = "Worker interrupted. Resume explicitly after inspecting any actions with unknown completion."
        return state


class ScopedTool(BaseTool):
    """Keep child tools inside their scope and declared file ownership."""

    def __init__(self, wrapped: BaseTool, state: SubagentState, context: ToolContext, parent: Agent) -> None:
        super().__init__(context)
        self.wrapped = copy.copy(wrapped)
        self.wrapped.context = context
        self.state = state
        self.parent = parent
        self.name = wrapped.name
        self.description = wrapped.description
        self.parameters = wrapped.parameters
        self.required = wrapped.required
        self.permission_level = wrapped.permission_level

    def is_read_only(self, arguments: Mapping[str, Any]) -> bool:
        return self.wrapped.is_read_only(arguments)

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self.state.scope.resolve() != self.state.scope:
            return ToolResult(error="The assigned scope changed on disk; stop and revalidate the worker workspace.")
        if not self.is_read_only(kwargs):
            if self.parent.session.mode == "plan":
                return ToolResult(error="Parent task is in plan mode; delegated edits are paused.")
            guidance = tuple(self.parent.session.pending_steering)
            if guidance and guidance != self.state.parent_guidance:
                self.state.parent_guidance = guidance
                for message in guidance:
                    if message not in self.state.session.pending_steering:
                        self.state.session.queue_steering(message)
                return ToolResult(error="New parent guidance is pending. Read it before making further edits.")
            if self.state.read_only or self.name not in FILE_WRITE_TOOLS:
                return ToolResult(error="This subagent can only read within its assigned scope.")
            paths = tool_paths(kwargs, self.context.working_directory)
            if not paths or any(not any(path.is_relative_to(owner) for owner in self.state.write_paths) for path in paths):
                return ToolResult(error="The requested edit is outside this subagent's declared write_paths.")
        return await self.wrapped.execute(**kwargs)


class SubagentManager:
    """Bounded workers with separate histories and inherited permission decisions."""

    def __init__(self, parent: Agent) -> None:
        self.parent = parent
        self.states: dict[str, SubagentState] = {}
        self.events: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._spawn_count = 0
        self._turn_usage: dict[str, Usage | None] = {}
        self._spawn_lock = asyncio.Lock()
        self._session = parent.session
        self._restore()

    def _restore(self) -> None:
        for agent_id, value in self.parent.session.subagents.items():
            try:
                state = SubagentState.restore(value)
                if state.id == agent_id:
                    self.states[agent_id] = state
            except (KeyError, TypeError, ValueError):
                continue
        self._save_states()

    def _save_states(self) -> None:
        self.parent.session.subagents = {agent_id: state.durable_snapshot() for agent_id, state in self.states.items()}

    async def _persist(self) -> None:
        self._save_states()
        await self.parent._checkpoint()

    def begin_turn(self) -> None:
        if self._session is not self.parent.session:
            self.states.clear()
            self._session = self.parent.session
            self._restore()
        self._spawn_count = 0
        self._turn_usage.clear()

    def total_usage(self) -> Usage | None:
        usage = None
        for worker_usage in self._turn_usage.values():
            usage = combine_usage(usage, worker_usage)
        return usage

    def snapshots(self) -> list[dict[str, Any]]:
        return [state.snapshot() for state in self.states.values()]

    def _publish(self, state: SubagentState) -> None:
        from libre_claw.core.agent import AgentSubagentUpdate
        self.events.put_nowait(AgentSubagentUpdate(state.snapshot()))

    async def spawn(self, **kwargs: Any) -> dict[str, Any]:
        async with self._spawn_lock:
            return await self._spawn(**kwargs)

    async def _spawn(
        self, *, task: str, scope: str, read_only: bool = True,
        write_paths: list[str] | None = None, provider: str = "", model: str = "",
        max_tool_calls: int = 20, max_seconds: float = 180,
    ) -> dict[str, Any]:
        context = self.parent.tool_registry.context
        if context is None:
            raise ValueError("Subagents require a workspace tool context.")
        if not task.strip() or len(task) > 16_000:
            raise ValueError("task must contain 1 to 16000 characters")
        if not scope.strip():
            raise ValueError("An explicit subagent scope is required.")
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        if not isinstance(max_tool_calls, int) or not 1 <= max_tool_calls <= 100:
            raise ValueError("max_tool_calls must be between 1 and 100")
        if not 1 <= max_seconds <= 900:
            raise ValueError("max_seconds must be between 1 and 900")
        resolved_scope, owners = self._validate_scope(scope, read_only, write_paths or [])
        self._check_available(read_only, owners)
        provider_name = provider or context.default_provider
        model_name = model or (context.default_model if not provider or provider == context.default_provider else "")
        state = SubagentState(
            id=uuid.uuid4().hex[:12], task=task, scope=resolved_scope, read_only=read_only,
            write_paths=tuple(owners), provider=provider_name, model=model_name,
            max_tool_calls=min(max_tool_calls, self.parent.max_tool_calls_per_turn), max_seconds=float(max_seconds),
        )
        child = await self._build_child(state)
        self.states[state.id] = state
        await self._start(state, child, task)
        return state.snapshot()

    def _validate_scope(self, scope: str, read_only: bool, write_paths: list[str]) -> tuple[Path, tuple[Path, ...]]:
        context = self.parent.tool_registry.context
        if context is None:
            raise ValueError("Subagents require a workspace tool context.")
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        root = context.working_directory.resolve()
        try:
            resolved_scope = context.sandbox_policy().resolve_path(scope)
        except SandboxViolation as exc:
            raise ValueError(str(exc)) from exc
        if not resolved_scope.is_relative_to(root) or not resolved_scope.is_dir():
            raise ValueError("scope must be an existing directory inside the parent workspace")
        owners = tuple((resolved_scope / Path(value).expanduser()).resolve() for value in write_paths)
        if any(not path.is_relative_to(resolved_scope) for path in owners):
            raise ValueError("write_paths must stay inside the assigned scope")
        if read_only and owners:
            raise ValueError("read_only workers cannot declare write_paths")
        if not read_only and not owners:
            raise ValueError("Writing workers require explicit write_paths")
        return resolved_scope, owners

    def _check_available(self, read_only: bool, owners: tuple[Path, ...]) -> None:
        context = self.parent.tool_registry.context
        if self._spawn_count >= max(1, context.subagent_max_total):
            raise ValueError("The subagent budget for this task has been reached.")
        if sum(state.status in ACTIVE_STATES for state in self.states.values()) >= max(1, context.subagent_max_concurrent):
            raise ValueError("All subagent slots are busy. Wait for or cancel a worker first.")
        if self.parent.session.mode == "plan" and not read_only:
            raise ValueError("Plan mode permits only read-only subagents.")
        for state in self.states.values():
            if state.status in ACTIVE_STATES and any(
                left.is_relative_to(right) or right.is_relative_to(left)
                for left in owners for right in state.write_paths
            ):
                raise ValueError(f"Write scope overlaps with active subagent {state.id}")

    async def _build_child(self, state: SubagentState) -> Agent:
        context = self.parent.tool_registry.context
        if context.subagent_provider_factory is None:
            if state.provider != context.default_provider or state.model != context.default_model:
                raise ValueError("This runtime has no subagent provider factory for model overrides.")
            child_provider = copy.copy(self.parent.provider)
        else:
            child_provider = await asyncio.to_thread(
                context.subagent_provider_factory, state.provider, state.model, state.scope, state.read_only,
            )
        if hasattr(child_provider, "sandbox") or hasattr(child_provider, "approval_policy"):
            raise ValueError("This provider runs its own tools; use a client-tool provider for scoped subagents.")
        effective_model = getattr(child_provider, "model", None)
        if isinstance(effective_model, str) and effective_model:
            state.model = effective_model
        child_context = replace(context, working_directory=state.scope, restrict_to_working_dir=True, shared_state={})
        child_tools = []
        for tool in self.parent.tool_registry.tools():
            if tool.name.startswith("subagent_"):
                continue
            if tool.is_read_only({}) or (not state.read_only and tool.name in FILE_WRITE_TOOLS):
                child_tools.append(ScopedTool(tool, state, child_context, self.parent))
        permissions = PermissionManager(
            config=self.parent.permission_manager.config,
            always_allowed_tools=set(self.parent.permission_manager.always_allowed_tools),
            always_allowed_calls=(set(self.parent.permission_manager.always_allowed_calls) if state.scope == context.working_directory.resolve() else set()),
        )
        remaining_seconds = state.max_seconds - state.elapsed()
        deadline = time.monotonic() + remaining_seconds
        if self.parent.deadline_monotonic is not None:
            deadline = min(deadline, self.parent.deadline_monotonic)

        async def checkpoint(_: Session) -> None:
            await self._persist()

        async def record_usage(usage: Usage) -> None:
            state.usage = combine_usage(state.usage, usage)
            self._turn_usage[state.id] = combine_usage(self._turn_usage.get(state.id), usage)
            await self._persist()

        from libre_claw.core.agent import Agent
        return Agent(
            session=state.session, provider=child_provider, tool_registry=ToolRegistry(child_tools),
            permission_manager=permissions, system_prompt=self.parent.system_prompt,
            system_prompt_extra=(
                f"You are a delegated worker. Complete only this assigned task. Workspace scope: {state.scope}. "
                f"Mode: {'read only' if state.read_only else 'write only to declared paths ' + ', '.join(map(str, state.write_paths))}. "
                "Return findings, verification and unresolved issues to the parent. Do not delegate further."
            ), max_tool_calls_per_turn=min(state.max_tool_calls - state.tool_calls, self.parent.max_tool_calls_per_turn),
            context_window_tokens=self.parent.context_window_tokens,
            deadline_monotonic=deadline, deadline_reserve_seconds=min(5.0, remaining_seconds / 10),
            checkpoint_callback=checkpoint, usage_callback=record_usage,
        )

    async def _start(self, state: SubagentState, child: Agent, prompt: str) -> None:
        state.status = "running"
        state.error = None
        state.started_monotonic = time.monotonic()
        self._spawn_count += 1
        try:
            await self._persist()
        except Exception:
            state.status = "interrupted"
            state.error = "Worker could not start because its checkpoint could not be saved."
            state.elapsed_seconds = state.elapsed()
            state.started_monotonic = None
            self._save_states()
            raise
        if state.status != "running":
            raise ValueError("Worker start was cancelled before execution.")
        self._publish(state)
        state.task_handle = asyncio.create_task(self._run(state, child, prompt), name=f"subagent-{state.id}")

    async def resume(self, agent_id: str, guidance: str = "") -> dict[str, Any]:
        async with self._spawn_lock:
            state = self._state(agent_id)
            if state.status != "interrupted":
                raise ValueError("Only interrupted workers can resume; completed, failed and cancelled workers are not replayed.")
            if not isinstance(guidance, str) or len(guidance) > 16_000:
                raise ValueError("guidance must contain at most 16000 characters")
            if state.tool_calls >= state.max_tool_calls:
                raise ValueError("This worker has exhausted its tool-call budget.")
            if not math.isfinite(state.max_seconds) or not math.isfinite(state.elapsed()) or state.elapsed() >= state.max_seconds:
                raise ValueError("This worker has exhausted its time budget.")
            scope, owners = self._validate_scope(str(state.scope), state.read_only, [str(path) for path in state.write_paths])
            self._check_available(state.read_only, owners)
            state.scope, state.write_paths = scope, owners
            child = await self._build_child(state)
            if state.status != "interrupted":
                raise ValueError("Worker resume was cancelled before execution.")
            state.session.recover_interrupted_tools()
            for message in state.session.consume_steering():
                state.session.add_user_message(message)
            prompt = (
                "Resume your interrupted assigned task from the saved conversation and checkpoint. "
                "Do not repeat completed work. Tool requests without a saved result have unknown completion; "
                "inspect current state before repeating any side effect. Retain the original scope and remaining budgets."
            )
            if guidance.strip():
                prompt += "\nCurrent user guidance: " + guidance.strip()
            await self._start(state, child, prompt)
            return state.snapshot()

    def steer(self, guidance: str) -> None:
        for state in self.states.values():
            if state.status in ACTIVE_STATES and guidance.strip() not in state.session.pending_steering:
                state.session.queue_steering(guidance)
        self._save_states()

    async def resume_pending(self) -> AsyncIterator[AgentEvent]:
        from libre_claw.core.agent import AgentPermissionRequest

        requests = list(self.parent.session.pending_subagent_resumes)
        if not requests:
            return
        self.parent.session.pending_subagent_resumes.clear()
        await self._persist()
        results: list[dict[str, Any]] = []
        for request in requests:
            try:
                snapshot = await self.resume(request["id"], request.get("guidance", ""))
            except ValueError as exc:
                results.append({"id": request["id"], "error": str(exc)})
                continue
            state = self._state(snapshot["id"])
            while state.task_handle is not None and not state.task_handle.done():
                queued = asyncio.create_task(self.events.get())
                try:
                    done, _ = await asyncio.wait((queued, state.task_handle), return_when=asyncio.FIRST_COMPLETED)
                    if queued in done:
                        event = queued.result()
                        if not isinstance(event, AgentPermissionRequest) or not event.future.done():
                            yield event
                finally:
                    if not queued.done():
                        queued.cancel()
                        await asyncio.gather(queued, return_exceptions=True)
            while not self.events.empty():
                event = self.events.get_nowait()
                if not isinstance(event, AgentPermissionRequest) or not event.future.done():
                    yield event
            results.append(state.snapshot())
        self.parent.session.add_user_message("Results from explicitly requested worker recovery:\n" + json.dumps(results, ensure_ascii=True))
        await self._persist()

    async def _heartbeat(self, state: SubagentState) -> None:
        while state.status in ACTIVE_STATES:
            await asyncio.sleep(1)
            await self._persist()

    async def _run(self, state: SubagentState, child: Agent, prompt: str) -> None:
        from libre_claw.core.agent import AgentDone, AgentError, AgentPermissionRequest, AgentTextDelta, AgentToolCall
        heartbeat = asyncio.create_task(self._heartbeat(state), name=f"subagent-checkpoint-{state.id}")
        permission_futures: list[asyncio.Future[Any]] = []
        try:
            async for event in child.run(prompt):
                if isinstance(event, AgentPermissionRequest):
                    permission_futures.append(event.future)
                    state.status = "blocked"
                    await self._persist()
                    self._publish(state)
                    arguments = copy.deepcopy(dict(event.call.arguments))
                    if isinstance(arguments.get("path"), str):
                        arguments["path"] = str((state.scope / arguments["path"]).resolve())
                    if isinstance(arguments.get("edits"), list):
                        for edit in arguments["edits"]:
                            if isinstance(edit, dict) and isinstance(edit.get("path"), str):
                                edit["path"] = str((state.scope / edit["path"]).resolve())
                    self.events.put_nowait(AgentPermissionRequest(
                        call=ToolCall(id=f"{state.id}:{event.call.id}", name=event.call.name, arguments=arguments),
                        future=event.future,
                    ))
                    # Child's next iteration performs the permission wait.
                elif isinstance(event, AgentTextDelta):
                    state.output = (state.output + event.text)[-MAX_OUTPUT_CHARS:]
                elif isinstance(event, AgentToolCall):
                    state.status = "running"
                    state.tool_calls += 1
                    await self._persist()
                    self._publish(state)
                elif isinstance(event, AgentError):
                    state.status = "failed"
                    state.error = event.message
                elif isinstance(event, AgentDone):
                    state.status = "done"
            if state.status in ACTIVE_STATES:
                state.status = "failed"
                state.error = "Worker ended without a final response."
        except asyncio.CancelledError:
            if state.status in ACTIVE_STATES:
                state.status = "cancelled"
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
        finally:
            for future in permission_futures:
                if not future.done():
                    future.cancel()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            state.elapsed_seconds = state.elapsed()
            state.started_monotonic = None
            try:
                await self._persist()
            except Exception as exc:
                state.status = "failed"
                state.error = f"Worker checkpoint failed: {exc}"
                self._save_states()
            self._publish(state)

    async def wait(self, ids: list[str] | None = None, timeout: float = 30) -> list[dict[str, Any]]:
        if not 0 <= timeout <= 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        states = [self._state(agent_id) for agent_id in ids] if ids else list(self.states.values())
        tasks = [state.task_handle for state in states if state.task_handle and not state.task_handle.done()]
        if tasks and timeout:
            await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        return [state.snapshot() for state in states]

    async def cancel(self, agent_id: str) -> dict[str, Any]:
        state = self._state(agent_id)
        pending = self.parent.session.pending_subagent_resumes
        removed = any(request.get("id") == agent_id for request in pending)
        self.parent.session.pending_subagent_resumes = [request for request in pending if request.get("id") != agent_id]
        if state.status in ACTIVE_STATES or state.status == "interrupted":
            state.status = "cancelled"
            if state.task_handle and not state.task_handle.done():
                state.task_handle.cancel()
                await asyncio.gather(state.task_handle, return_exceptions=True)
            state.elapsed_seconds = state.elapsed()
            state.started_monotonic = None
            await self._persist()
            self._publish(state)
        elif removed:
            await self._persist()
        return state.snapshot()

    async def close(self, *, interrupted: bool = False) -> None:
        active = [state for state in self.states.values() if state.status in ACTIVE_STATES]
        tasks = [state.task_handle for state in self.states.values() if state.task_handle and not state.task_handle.done()]
        for state in active:
            state.status = "interrupted" if interrupted else "cancelled"
            if interrupted:
                state.error = "Worker interrupted; resume explicitly to continue."
            if state.task_handle and not state.task_handle.done():
                state.task_handle.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in active:
            state.elapsed_seconds = state.elapsed()
            state.started_monotonic = None
            self._publish(state)
        if active:
            await self._persist()

    def _state(self, agent_id: str) -> SubagentState:
        if agent_id not in self.states:
            raise ValueError(f"Unknown subagent: {agent_id}")
        return self.states[agent_id]

    def ownership_error(self, call: ToolCall) -> str | None:
        context = self.parent.tool_registry.context
        if context is None or call.name.startswith("subagent_"):
            return None
        tool = self.parent.tool_registry.get(call.name)
        if tool.is_read_only(call.arguments):
            return None
        owned = [state for state in self.states.values() if state.status in ACTIVE_STATES and state.write_paths]
        if not owned:
            return None
        paths = tool_paths(call.arguments, context.working_directory)
        if call.name not in FILE_WRITE_TOOLS or not paths:
            return "Wait for writing subagents before running tools with unbounded write effects."
        for state in owned:
            if any(path.is_relative_to(owner) or owner.is_relative_to(path) for path in paths for owner in state.write_paths):
                return f"Path is owned by active subagent {state.id}; wait for or cancel it before editing."
        return None
