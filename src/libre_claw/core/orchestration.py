# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Task-scoped orchestration routes and enforceable worker admission policy."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from libre_claw.providers.base import LLMProvider, StreamEvent, ToolSchema
from libre_claw.providers.model_catalog import ModelInfo

if TYPE_CHECKING:
    from libre_claw.core.agent import Agent
    from libre_claw.core.session import ChatMessage
    from libre_claw.core.subagents import SubagentState


_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_ROUTE_FIELDS = {"provider", "model", "reasoning_effort", "context_window_tokens", "max_output_tokens", "prompt"}
_WORKER_FIELDS = _ROUTE_FIELDS | {"id", "name", "role", "read_only", "max_concurrent", "max_tool_calls", "max_seconds"}
_PROFILE_FIELDS = {"orchestrator", "workers", "default_worker", "max_concurrent", "max_total_workers"}
_NATIVE_TASK_FIELDS = {"worker", "task", "scope", "read_only", "write_paths", "provider", "model", "max_tool_calls", "max_seconds"}
_DISPATCH_FIELDS = {"worker", "task", "scope", "read_only", "write_paths"}


def _text(value: Any, field: str, limit: int = 512, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > limit or "\0" in value
            or (field not in {"prompt", "task"} and any(char in value for char in "\r\n"))
            or (not empty and not value.strip())):
        raise ValueError(f"Invalid orchestration {field}.")
    return value if field == "prompt" else value.strip()


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Orchestration {field} must be between {minimum} and {maximum}.")
    return value


def _route(raw: Any, *, worker: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - (_WORKER_FIELDS if worker else _ROUTE_FIELDS):
        raise ValueError("Orchestration routes contain unknown or invalid fields.")
    result = {key: _text(raw.get(key, ""), key, 16_000 if key == "prompt" else 512, empty=True)
              for key in ("provider", "model", "reasoning_effort", "prompt")}
    result["context_window_tokens"] = _integer(raw.get("context_window_tokens", 32_768 if worker else 98_304), "context_window_tokens", 1024, 16_777_216)
    result["max_output_tokens"] = _integer(raw.get("max_output_tokens", 4096 if worker else 16_384), "max_output_tokens", 1, 1_048_576)
    if result["max_output_tokens"] >= result["context_window_tokens"]:
        raise ValueError("Orchestration output limit must be smaller than its working context.")
    return result


def validate_profile(raw: Any) -> dict[str, Any]:
    """Normalize declarative settings without discovering models or using credentials."""
    if not isinstance(raw, dict) or set(raw) - _PROFILE_FIELDS:
        raise ValueError("Orchestration profile contains unknown or invalid fields.")
    workers = raw.get("workers")
    if not isinstance(workers, list) or not 1 <= len(workers) <= 16:
        raise ValueError("Configure between one and sixteen worker profiles.")
    result = {"orchestrator": _route(raw.get("orchestrator", {})), "workers": [],
              "max_concurrent": _integer(raw.get("max_concurrent", 3), "max_concurrent", 1, 8),
              "max_total_workers": _integer(raw.get("max_total_workers", 12), "max_total_workers", 1, 32)}
    seen = set()
    for raw_worker in workers:
        worker = _route(raw_worker, worker=True)
        identifier = _text(raw_worker.get("id"), "worker id", 48)
        if not _ID.fullmatch(identifier) or identifier in seen:
            raise ValueError("Worker IDs must be unique lowercase identifiers.")
        seen.add(identifier)
        role = raw_worker.get("role", "worker")
        if role not in ("scout", "worker", "reviewer"):
            raise ValueError("Worker role must be scout, worker, or reviewer.")
        read_only = raw_worker.get("read_only", True)
        if type(read_only) is not bool or (role in {"scout", "reviewer"} and not read_only):
            raise ValueError("Scouts and reviewers must be read-only; worker read_only must be boolean.")
        worker.update(id=identifier, name=_text(raw_worker.get("name", identifier), "worker name", 160), role=role, read_only=read_only,
                      max_concurrent=_integer(raw_worker.get("max_concurrent", 1), "worker max_concurrent", 1, 8),
                      max_tool_calls=_integer(raw_worker.get("max_tool_calls", 20), "max_tool_calls", 1, 100),
                      max_seconds=_integer(raw_worker.get("max_seconds", 180), "max_seconds", 1, 900))
        result["workers"].append(worker)
    default = raw.get("default_worker", result["workers"][0]["id"])
    if not isinstance(default, str) or default not in seen:
        raise ValueError("default_worker must name a configured worker profile.")
    result["default_worker"] = default
    if len(json.dumps(result, ensure_ascii=True).encode()) > 64 * 1024:
        raise ValueError("Orchestration profile exceeds 64 KiB.")
    return result


def resolve_profile(raw: Any, *, default_provider: str, default_model: str) -> dict[str, Any]:
    """Resolve inherited routes once, so a running task never silently changes models."""
    result = validate_profile(raw)
    orchestrator = result["orchestrator"]
    orchestrator["provider"] = orchestrator["provider"] or default_provider
    if not orchestrator["model"] and orchestrator["provider"] == default_provider:
        orchestrator["model"] = default_model
    _text(orchestrator["provider"], "orchestrator provider")
    _text(orchestrator["model"], "orchestrator model")
    for worker in result["workers"]:
        worker["provider"] = worker["provider"] or orchestrator["provider"]
        if not worker["model"] and worker["provider"] == orchestrator["provider"]:
            worker["model"] = orchestrator["model"]
        _text(worker["provider"], "worker provider")
        _text(worker["model"], "worker model")
    return result


class LimitedProvider(LLMProvider):
    """Delegate provider behavior while capping advertised context and request output."""

    auto_context_window = True

    def __init__(self, provider: LLMProvider, *, context_window_tokens: int, max_output_tokens: int,
                 authorize: Callable[[], Any] | None = None) -> None:
        self.provider = provider
        self.context_limit = context_window_tokens
        self.output_limit = max_output_tokens
        self.authorize = authorize

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    def _limited(self, info: Any) -> Any:
        if not isinstance(info, ModelInfo):
            return info
        context = min(info.context_window_tokens or self.context_limit, self.context_limit)
        return replace(info, context_window_tokens=context,
                       max_completion_tokens=min(info.max_completion_tokens or self.output_limit, self.output_limit, context))

    @property
    def model_info(self) -> ModelInfo | None:
        return self._limited(self.provider.model_info)

    @model_info.setter
    def model_info(self, value: ModelInfo | None) -> None:
        self.provider.model_info = value

    def _metadata(self, name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.provider, name, None)
        if not callable(method):
            return self.model_info
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            async def resolved() -> Any:
                return self._limited(await result)
            return resolved()
        return self._limited(result)

    def ensure_model_info(self, *args: Any, **kwargs: Any) -> Any:
        return self._metadata("ensure_model_info", *args, **kwargs)

    def get_model_info(self, *args: Any, **kwargs: Any) -> Any:
        return self._metadata("get_model_info", *args, **kwargs)

    def refresh_model_info(self, *args: Any, **kwargs: Any) -> Any:
        return self._metadata("refresh_model_info", *args, **kwargs)

    async def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema] | None = None,
                       system: str | None = None, stream: bool = True, temperature: float = 0.7,
                       max_tokens: int | None = None) -> AsyncIterator[StreamEvent]:
        if self.authorize is not None:
            self.authorize()
        info = self.model_info
        output_limit = min(self.output_limit, info.max_completion_tokens or self.output_limit) if info is not None else self.output_limit
        source = self.provider.complete(messages, tools=tools, system=system, stream=stream, temperature=temperature,
                                        max_tokens=min(max_tokens, output_limit) if max_tokens is not None else output_limit)
        try:
            async for event in source:
                if self.authorize is not None:
                    self.authorize()
                yield event
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()


class OrchestrationController:
    """One immutable route snapshot bound to one parent Session and its grants."""

    plugin_id = "orchestration"

    def __init__(self, agent: Agent, profile: dict[str, Any], authorize_callback: Callable[[], bool]) -> None:
        self.agent = agent
        self.session = agent.session
        self._profile = copy.deepcopy(profile)
        self.fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self._workers = {worker["id"]: worker for worker in self._profile["workers"]}
        self._authorize = authorize_callback

    @property
    def profile(self) -> dict[str, Any]:
        return copy.deepcopy(self._profile)

    def authorize(self) -> bool:
        if (self.agent.session is not self.session or self.agent.subagents.policy is not self
                or self._authorize() is not True):
            raise PermissionError("This task's orchestration profile is no longer authorized.")
        return True

    def resolve_spawn(self, args: dict[str, Any]) -> dict[str, Any]:
        self.authorize()
        if set(args) - _NATIVE_TASK_FIELDS:
            raise ValueError("Orchestration dispatch contains unknown fields.")
        identifier = args["worker"] if "worker" in args else self._profile["default_worker"]
        if not isinstance(identifier, str) or identifier not in self._workers:
            raise ValueError("Choose a worker ID from this task's approved orchestration profile.")
        worker = self._workers[identifier]
        for key in ("provider", "model"):
            if args.get(key) not in (None, "", worker[key]):
                raise ValueError("Worker provider/model overrides cannot bypass the orchestration profile.")
        read_only = args.get("read_only", worker["read_only"])
        if type(read_only) is not bool or worker["read_only"] and not read_only:
            raise ValueError("A read-only worker profile cannot be granted write access.")
        calls = _integer(args.get("max_tool_calls", worker["max_tool_calls"]), "max_tool_calls", 1, 100)
        seconds = args.get("max_seconds", worker["max_seconds"])
        if type(seconds) not in {int, float} or not 1 <= seconds <= 900:
            raise ValueError("Worker max_seconds must be between 1 and 900.")
        return {**args, "provider": worker["provider"], "model": worker["model"], "read_only": read_only,
                "max_tool_calls": min(calls, worker["max_tool_calls"]), "max_seconds": min(seconds, worker["max_seconds"]),
                "worker_id": identifier, "role": worker["role"], "name": worker["name"],
                "reasoning_effort": worker["reasoning_effort"], "context_window_tokens": worker["context_window_tokens"],
                "max_output_tokens": worker["max_output_tokens"], "role_prompt": worker["prompt"],
                "orchestration_fingerprint": self.fingerprint}

    def tasks(self, tasks: Any) -> list[dict[str, Any]]:
        self.authorize()
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 16:
            raise ValueError("Dispatch between one and sixteen bounded assignments.")
        result = []
        for task in tasks:
            if not isinstance(task, dict) or set(task) - _DISPATCH_FIELDS or not {"worker", "task", "scope"} <= task.keys():
                raise ValueError("Each assignment requires only worker, task, scope and optional read_only/write_paths.")
            _text(task["task"], "task", 16_000)
            _text(task["scope"], "scope", 4096)
            paths = task.get("write_paths", [])
            if not isinstance(paths, list) or len(paths) > 128:
                raise ValueError("write_paths must be a bounded list of paths.")
            for path in paths:
                _text(path, "write path", 4096)
            result.append(copy.deepcopy(task))
            self.resolve_spawn(result[-1])
        try:
            size = len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode())
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise ValueError("Orchestration assignments must contain bounded JSON values.") from None
        if size > 64 * 1024:
            raise ValueError("Orchestration assignments exceed 64 KiB.")
        return result

    def is_read_only(self, tasks: Any) -> bool:
        return all(self.resolve_spawn(task)["read_only"] for task in self.tasks(tasks))

    tasks_read_only = is_read_only

    def admit(self, states: Sequence[SubagentState]) -> None:
        self.authorize()
        manager = self.agent.subagents
        active = [state for state in manager.states.values() if state.status in {"running", "blocked"}]
        if manager._spawn_count + len(states) > self._profile["max_total_workers"]:
            raise ValueError("The orchestration dispatch limit for this turn has been reached.")
        if len(active) + len(states) > self._profile["max_concurrent"]:
            raise ValueError("All orchestration worker slots are busy.")
        for identifier, worker in self._workers.items():
            if sum(state.worker_id == identifier for state in [*active, *states]) > worker["max_concurrent"]:
                raise ValueError(f"Worker profile {identifier} has reached its concurrency limit.")

    def validate_resume(self, state: SubagentState) -> None:
        self.authorize()
        worker = self._workers.get(state.worker_id)
        if worker is None or state.orchestration_fingerprint != self.fingerprint:
            raise ValueError("This worker can resume only under its original orchestration profile.")
        if ((state.provider, state.model) != (worker["provider"], worker["model"])
                or state.role != worker["role"] or state.name != worker["name"] or state.reasoning_effort != worker["reasoning_effort"]
                or state.context_window_tokens != worker["context_window_tokens"]
                or state.max_output_tokens != worker["max_output_tokens"] or state.role_prompt != worker["prompt"]
                or state.max_tool_calls > worker["max_tool_calls"] or state.max_seconds > worker["max_seconds"]
                or worker["read_only"] and not state.read_only):
            raise ValueError("Saved worker routing or permissions do not match its orchestration profile.")

    async def dispatch(self, tasks: Any) -> list[dict[str, Any]]:
        return self._reports(await self.agent.subagents.spawn_batch(self.tasks(tasks)))

    @staticmethod
    def _reports(workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep aggregate handoffs bounded; a selected-ID wait exposes more detail."""
        if len(json.dumps(workers, ensure_ascii=True).encode()) <= 180 * 1024:
            return workers
        result = copy.deepcopy(workers)
        for worker in result:
            worker["task"] = worker["task"][:400]
            worker["details_truncated"] = True
        if len(json.dumps(result, ensure_ascii=True).encode()) <= 180 * 1024:
            return result
        for worker in result:
            worker["output"] = worker["output"][:1000]
            if isinstance(worker.get("error"), str):
                worker["error"] = worker["error"][:1000]
        if len(json.dumps(result, ensure_ascii=True).encode()) > 180 * 1024:
            for worker in result:
                for field in ("task", "output", "scope", "write_paths", "provider", "model"):
                    worker.pop(field, None)
                if isinstance(worker.get("error"), str):
                    worker["error"] = worker["error"][:256]
        return result

    def _ids(self, ids: Any) -> list[str] | None:
        self.authorize()
        if ids is None:
            current = self.agent.subagents._turn_ids
            states = [state for state in self.agent.subagents.states.values()
                      if state.orchestration_fingerprint == self.fingerprint and (not current or state.id in current)]
            return [state.id for state in states[-32:]]
        if not isinstance(ids, list) or len(ids) > 32 or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Worker IDs must be a bounded list of unique strings.")
        for identifier in ids:
            self.validate_resume(self.agent.subagents._state(identifier))
        return ids

    async def wait(self, ids: list[str] | None = None, timeout: float = 0) -> list[dict[str, Any]]:
        selected = self._ids(ids)
        result = await self.agent.subagents.wait(selected, timeout) if selected else []
        self.authorize()
        return self._reports(result)

    async def cancel(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        self.authorize()
        selected = [state.id for state in self.agent.subagents.states.values()
                    if state.orchestration_fingerprint == self.fingerprint and state.status in {"running", "blocked", "interrupted"}] if ids is None else self._ids(ids)
        result = [await self.agent.subagents.cancel(identifier) for identifier in selected]
        self.authorize()
        return self._reports(result[-32:])

    def status(self) -> dict[str, Any]:
        self.authorize()
        states = [state for state in self.agent.subagents.states.values() if state.orchestration_fingerprint == self.fingerprint]
        active = [state for state in states if state.status in {"running", "blocked"}]
        recent = [state for state in states if state.status not in {"running", "blocked"}][-(32 - len(active)):]
        workers = [state.snapshot() for state in [*active, *recent]]
        public = self.profile
        public["orchestrator"].pop("prompt", None)
        for worker in public["workers"]:
            worker.pop("prompt", None)
        return {"profile": public, "workers": self._reports(workers), "active": sum(state["status"] in {"running", "blocked"} for state in workers),
                "total_dispatched": self.agent.subagents._spawn_count, "omitted_workers": len(states) - len(workers), "hard_dollar_cap": False}


def bind_orchestration(agent: Agent, profile: dict[str, Any], authorize_callback: Callable[[], bool]) -> OrchestrationController:
    """Bind the selected provider and workers to one authorized task profile."""
    from libre_claw.core.subagents import SubagentManager
    context = agent.tool_registry.context
    if context is None:
        raise ValueError("Orchestration requires a workspace tool context.")
    snapshot = resolve_profile(profile, default_provider=context.default_provider, default_model=context.default_model)
    provider = agent.provider.provider if isinstance(agent.provider, LimitedProvider) else agent.provider
    if hasattr(provider, "sandbox") or hasattr(provider, "approval_policy"):
        raise ValueError("Orchestration requires a provider using Libre Claw's approved client tools.")
    if authorize_callback() is not True:
        raise PermissionError("This task's orchestration profile is not authorized.")
    if agent.subagents is None:
        agent.subagents = SubagentManager(agent)
    controller = OrchestrationController(agent, snapshot, authorize_callback)
    if any(state.status in {"running", "blocked"} and state.orchestration_fingerprint != controller.fingerprint
           for state in agent.subagents.states.values()):
        raise ValueError("Wait for existing workers before changing orchestration profiles.")
    agent.subagents.policy = controller
    controller.authorize()
    agent.provider = LimitedProvider(provider, context_window_tokens=snapshot["orchestrator"]["context_window_tokens"],
                                     max_output_tokens=snapshot["orchestrator"]["max_output_tokens"], authorize=controller.authorize)
    agent.context_window_tokens = snapshot["orchestrator"]["context_window_tokens"]
    agent.fallback_providers = ()
    agent.provider_retry_attempts = 0
    context.shared_state["subagent_manager"] = agent.subagents
    context.shared_state["orchestration_controller"] = controller
    routes = "; ".join(
        f"{worker['id']} ({worker['role']}: {worker['provider']}/{worker['model']}; "
        f"{'read only' if worker['read_only'] else 'owned-path edits'}; "
        f"{worker['max_concurrent']} concurrent, {worker['max_tool_calls']} tools, {worker['max_seconds']}s)"
        for worker in snapshot["workers"]
    )
    instructions = (
        "You are the orchestrator. Own planning, bounded delegation, integration and final verification. "
        f"Approved worker profiles: {routes}. Default worker: {snapshot['default_worker']}. "
        f"At most {snapshot['max_concurrent']} workers may run concurrently; "
        f"at most {snapshot['max_total_workers']} may be launched in this turn. Wait for capacity before dispatching another batch. "
        "Use cordis__orchestration__delegate with approved worker IDs, and cordis__orchestration__wait, "
        "cordis__orchestration__status or cordis__orchestration__cancel to manage those workers. "
        "If an aggregate report has details_truncated, wait for selected individual IDs to retrieve their full results. "
        "Include the goal, exact scope, owned paths, relevant findings, existing user changes, "
        "acceptance criteria, checks and authorized operations. Workers cannot run shell commands; run integration checks after joining them. "
        "Do not dispatch needless scouting or review chains. Do not switch providers outside the approved routes or delegate recursively. "
        "Respect active file ownership. Report observed checks and unresolved issues. These limits are not a dollar budget."
    )
    base = getattr(agent, "_orchestration_prompt_base", agent.system_prompt_extra)
    agent._orchestration_prompt_base = base
    agent.system_prompt_extra = "\n\n".join(part for part in (base, instructions, snapshot["orchestrator"]["prompt"]) if part)
    agent._refresh_tool_schemas(agent.provider)
    return controller
