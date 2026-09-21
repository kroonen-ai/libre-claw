# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from libre_claw.core.instructions import tool_paths
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session
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
    task_handle: asyncio.Task[None] | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id, "task": self.task, "scope": str(self.scope),
            "read_only": self.read_only, "write_paths": [str(path) for path in self.write_paths],
            "provider": self.provider, "model": self.model, "status": self.status,
            "output": self.output, "error": self.error, "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls, "max_seconds": self.max_seconds,
            "created_at": self.created_at,
            "usage": asdict(self.usage) if self.usage is not None else None,
        }


class ScopedTool(BaseTool):
    """Keep child tools inside their scope and declared file ownership."""

    def __init__(self, wrapped: BaseTool, state: SubagentState, context: ToolContext) -> None:
        super().__init__(context)
        self.wrapped = copy.copy(wrapped)
        self.wrapped.context = context
        self.state = state
        self.name = wrapped.name
        self.description = wrapped.description
        self.parameters = wrapped.parameters
        self.required = wrapped.required
        self.permission_level = wrapped.permission_level

    def is_read_only(self, arguments: Mapping[str, Any]) -> bool:
        return self.wrapped.is_read_only(arguments)

    async def execute(self, **kwargs: Any) -> ToolResult:
        if not self.is_read_only(kwargs):
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
        self._turn_ids: set[str] = set()
        self._spawn_lock = asyncio.Lock()

    def begin_turn(self) -> None:
        self._spawn_count = 0
        self._turn_ids.clear()
        if len(self.states) > 24:
            self.states = dict(list(self.states.items())[-24:])

    def total_usage(self) -> Usage | None:
        usage = None
        for agent_id in self._turn_ids:
            usage = combine_usage(usage, self.states[agent_id].usage)
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
        if self._spawn_count >= max(1, context.subagent_max_total):
            raise ValueError("The subagent budget for this task has been reached.")
        if sum(state.status in ACTIVE_STATES for state in self.states.values()) >= max(1, context.subagent_max_concurrent):
            raise ValueError("All subagent slots are busy. Wait for or cancel a worker first.")
        if getattr(self.parent.session, "mode", "default") == "plan" and not read_only:
            raise ValueError("Plan mode permits only read-only subagents.")
        root = context.working_directory.resolve()
        resolved_scope = context.sandbox_policy().resolve_path(scope)
        if not resolved_scope.is_relative_to(root) or not resolved_scope.is_dir():
            raise ValueError("scope must be an existing directory inside the parent workspace")
        owners: list[Path] = []
        for value in (write_paths or []):
            path = (resolved_scope / Path(value).expanduser()).resolve()
            if not path.is_relative_to(resolved_scope):
                raise ValueError("write_paths must stay inside the assigned scope")
            owners.append(path)
        if read_only and owners:
            raise ValueError("read_only workers cannot declare write_paths")
        if not read_only and not owners:
            raise ValueError("Writing workers require explicit write_paths")
        for state in self.states.values():
            if state.status in ACTIVE_STATES and any(
                left.is_relative_to(right) or right.is_relative_to(left)
                for left in owners for right in state.write_paths
            ):
                raise ValueError(f"Write scope overlaps with active subagent {state.id}")
        provider_name = provider or context.default_provider
        model_name = model or (context.default_model if not provider or provider == context.default_provider else "")
        if context.subagent_provider_factory is None:
            if provider or model:
                raise ValueError("This runtime has no subagent provider factory for model overrides.")
            child_provider = copy.copy(self.parent.provider)
        else:
            child_provider = await asyncio.to_thread(
                context.subagent_provider_factory, provider_name, model_name, resolved_scope, read_only,
            )
        # Providers with their own native tool execution cannot enforce per-path ownership.
        if hasattr(child_provider, "sandbox") or hasattr(child_provider, "approval_policy"):
            raise ValueError("This provider runs its own tools; use a client-tool provider for scoped subagents.")
        state = SubagentState(
            id=uuid.uuid4().hex[:12], task=task, scope=resolved_scope, read_only=read_only,
            write_paths=tuple(owners), provider=provider_name, model=model_name,
            max_tool_calls=min(max_tool_calls, self.parent.max_tool_calls_per_turn), max_seconds=float(max_seconds),
        )
        child_context = replace(context, working_directory=resolved_scope, restrict_to_working_dir=True, shared_state={})
        child_tools = []
        for tool in self.parent.tool_registry.tools():
            if tool.name.startswith("subagent_"):
                continue
            # Dynamic tools are allowed only if each actual invocation passes the scope guard.
            if tool.is_read_only({}) or (not read_only and tool.name in FILE_WRITE_TOOLS):
                child_tools.append(ScopedTool(tool, state, child_context))
        permissions = PermissionManager(
            config=self.parent.permission_manager.config,
            always_allowed_tools=set(self.parent.permission_manager.always_allowed_tools),
            # Relative call fingerprints are only equivalent in the same workspace.
            always_allowed_calls=(set(self.parent.permission_manager.always_allowed_calls) if resolved_scope == root else set()),
        )
        from libre_claw.core.agent import Agent
        deadline = time.monotonic() + max_seconds
        if self.parent.deadline_monotonic is not None:
            deadline = min(deadline, self.parent.deadline_monotonic)
        child = Agent(
            session=state.session, provider=child_provider, tool_registry=ToolRegistry(child_tools),
            permission_manager=permissions, system_prompt=self.parent.system_prompt,
            system_prompt_extra=(
                f"You are a delegated worker. Complete only this assigned task. Workspace scope: {resolved_scope}. "
                f"Mode: {'read only' if read_only else 'write only to declared paths ' + ', '.join(map(str, owners))}. "
                "Return findings, verification and unresolved issues to the parent. Do not delegate further."
            ), max_tool_calls_per_turn=state.max_tool_calls,
            context_window_tokens=self.parent.context_window_tokens,
            deadline_monotonic=deadline, deadline_reserve_seconds=min(5.0, max_seconds / 10),
        )
        self.states[state.id] = state
        self._spawn_count += 1
        self._turn_ids.add(state.id)
        self._publish(state)
        state.task_handle = asyncio.create_task(self._run(state, child), name=f"subagent-{state.id}")
        return state.snapshot()

    async def _run(self, state: SubagentState, child: Agent) -> None:
        from libre_claw.core.agent import AgentDone, AgentError, AgentPermissionRequest, AgentTextDelta, AgentToolCall
        try:
            async for event in child.run(state.task):
                if isinstance(event, AgentPermissionRequest):
                    state.status = "blocked"
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
                    self._publish(state)
                elif isinstance(event, AgentError):
                    state.status = "failed"
                    state.error = event.message
                elif isinstance(event, AgentDone):
                    state.usage = event.usage
                    state.status = "done"
            if state.status in ACTIVE_STATES:
                state.status = "failed"
                state.error = "Worker ended without a final response."
        except asyncio.CancelledError:
            state.status = "cancelled"
        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
        finally:
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
        if state.task_handle and not state.task_handle.done():
            state.task_handle.cancel()
            await asyncio.gather(state.task_handle, return_exceptions=True)
            if state.status in ACTIVE_STATES:
                state.status = "cancelled"
                self._publish(state)
        return state.snapshot()

    async def close(self) -> None:
        for agent_id in tuple(self.states):
            await self.cancel(agent_id)

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
