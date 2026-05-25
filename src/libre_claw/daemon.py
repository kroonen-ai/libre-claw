# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import httpx
from aiohttp import web

from libre_claw.config import GeneralConfig, LibreClawConfig
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    RunEvent,
    RunRecord,
    RunState,
    RunStore,
    Session,
)
from libre_claw.core.memory import MemoryStore
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.review import RUN_ARTIFACT_NAMES, run_plan_text
from libre_claw.core.skills import SkillStore
from libre_claw.core.tools import ToolRegistry
from libre_claw.providers import LLMProvider, Usage, create_provider
from libre_claw.tools_builtin import create_builtin_registry


ProviderFactory = Callable[[LibreClawConfig], LLMProvider]
RegistryFactory = Callable[[LibreClawConfig, MemoryStore], ToolRegistry]
RunKind = Literal["chat", "goal"]


@dataclass
class ActiveRun:
    run_id: str
    task: asyncio.Task[None]
    pending_permissions: dict[str, AgentPermissionRequest] = field(default_factory=dict)


class DaemonServer:
    """Local background runner API for durable Libre Claw runs."""

    def __init__(
        self,
        config: LibreClawConfig,
        *,
        run_store: RunStore | None = None,
        provider_factory: ProviderFactory = create_provider,
        registry_factory: RegistryFactory = create_builtin_registry,
    ) -> None:
        self.config = config
        self.run_store = run_store or RunStore()
        self.provider_factory = provider_factory
        self.registry_factory = registry_factory
        self.memory_store = MemoryStore()
        self.active_runs: dict[str, ActiveRun] = {}
        self._app: web.Application | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.add_routes(
            [
                web.get("/health", self.health),
                web.get("/runs", self.list_runs),
                web.post("/runs", self.start_run),
                web.get("/runs/{run_id}", self.get_run),
                web.get("/runs/{run_id}/events", self.get_events),
                web.post("/runs/{run_id}/cancel", self.cancel_run),
                web.post("/runs/{run_id}/permissions/{tool_call_id}", self.resolve_permission),
            ]
        )
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)
        self._app = app
        return app

    async def run(self, host: str | None = None, port: int | None = None) -> None:
        runner = web.AppRunner(self.app())
        await runner.setup()
        site = web.TCPSite(runner, host or self.config.daemon.host, port or self.config.daemon.port)
        try:
            await site.start()
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _on_startup(self, _app: web.Application) -> None:
        await self.memory_store.initialize()

    async def _on_cleanup(self, _app: web.Application) -> None:
        for active in list(self.active_runs.values()):
            if not active.task.done():
                active.task.cancel()
        if self.active_runs:
            await asyncio.gather(*(active.task for active in self.active_runs.values()), return_exceptions=True)

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "active_runs": len(self.active_runs),
                "host": self.config.daemon.host,
                "port": self.config.daemon.port,
            }
        )

    async def list_runs(self, request: web.Request) -> web.Response:
        limit = _positive_int(request.query.get("limit"), default=20, maximum=100)
        runs = await self.run_store.list_runs(limit=limit)
        return web.json_response({"runs": [_run_payload(run) for run in runs]})

    async def get_run(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        run = await self.run_store.load_run(run_id)
        if run is None:
            return _json_error("Unknown run.", status=404)
        active = self.active_runs.get(run.run_id)
        return web.json_response(
            {
                "run": _run_payload(run),
                "active": active is not None and not active.task.done(),
                "pending_permissions": list(active.pending_permissions) if active is not None else [],
                "artifacts": _artifact_payload(run),
            }
        )

    async def get_events(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        after = _positive_int(request.query.get("after"), default=0, maximum=10_000_000)
        try:
            events = await self.run_store.load_events(run_id)
        except ValueError:
            return _json_error("Unknown run.", status=404)
        filtered = [event for event in events if event.event_id > after]
        return web.json_response({"events": [_event_payload(event) for event in filtered]})

    async def start_run(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return _json_error("Request body must be JSON.")
        if not isinstance(payload, Mapping):
            return _json_error("Request body must be a JSON object.")
        message = str(payload.get("message", "")).strip()
        if not message:
            return _json_error("Field 'message' is required.")
        kind = str(payload.get("kind", "chat"))
        if kind not in {"chat", "goal"}:
            return _json_error("Field 'kind' must be 'chat' or 'goal'.")

        try:
            run_config = self._config_for_payload(payload)
        except ValueError as exc:
            return _json_error(str(exc), status=403)
        run = await self.run_store.create_run(
            message,
            kind=cast(RunKind, kind),
            provider=run_config.general.default_provider,
            model=run_config.general.default_model,
            working_directory=run_config.general.working_directory,
            state="queued",
        )
        task = asyncio.create_task(self._run_agent(run, message, run_config))
        active = ActiveRun(run_id=run.run_id, task=task)
        self.active_runs[run.run_id] = active
        task.add_done_callback(lambda _task, run_id=run.run_id: self.active_runs.pop(run_id, None))
        return web.json_response({"run": _run_payload(run)}, status=202)

    async def cancel_run(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        run = await self.run_store.load_run(run_id)
        if run is None:
            return _json_error("Unknown run.", status=404)
        active = self.active_runs.get(run_id)
        if active is not None and not active.task.done():
            active.task.cancel()
            await self.run_store.append_event(run_id, "cancel_requested", {"source": "daemon_api"})
            return web.json_response({"run_id": run_id, "cancelled": True, "active": True})
        await self.run_store.append_event(run_id, "cancelled", {"reason": "Cancelled through daemon API."})
        await self.run_store.finish_run(
            run_id,
            "cancelled",
            plan=_read_artifact(run, "plan.md"),
            summary=_read_artifact(run, "summary.md"),
            verification="Run cancelled through daemon API.\n",
            diff=_read_artifact(run, "diff.patch"),
        )
        return web.json_response({"run_id": run_id, "cancelled": True, "active": False})

    async def resolve_permission(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        tool_call_id = request.match_info["tool_call_id"]
        active = self.active_runs.get(run_id)
        if active is None:
            return _json_error("Run is not active.", status=409)
        permission = active.pending_permissions.get(tool_call_id)
        if permission is None:
            return _json_error("No pending permission for this tool call.", status=404)
        try:
            payload = await request.json()
        except ValueError:
            return _json_error("Request body must be JSON.")
        resolution = str(payload.get("resolution", "deny"))
        if resolution not in {"allow_once", "deny", "always_allow_tool", "always_allow_call"}:
            return _json_error("Invalid permission resolution.")
        if not permission.future.done():
            permission.future.set_result(cast(PermissionResolution, resolution))
        active.pending_permissions.pop(tool_call_id, None)
        await self.run_store.append_event(
            run_id,
            "permission_response",
            {"tool_call_id": tool_call_id, "name": permission.call.name, "resolution": resolution},
        )
        await self.run_store.update_state(run_id, "running")
        return web.json_response({"run_id": run_id, "tool_call_id": tool_call_id, "resolution": resolution})

    def _config_for_payload(self, payload: Mapping[str, Any]) -> LibreClawConfig:
        provider = str(payload.get("provider") or self.config.general.default_provider)
        model = str(payload.get("model") or self.config.general.default_model)
        _reject_working_directory_override(self.config, payload)
        general = GeneralConfig(
            default_provider="ollama" if provider == "local" else provider,
            default_model=model,
            working_directory=self.config.general.working_directory,
            theme=self.config.general.theme,
            log_level=self.config.general.log_level,
        )
        return LibreClawConfig(
            general=general,
            agent=self.config.agent,
            permissions=self.config.permissions,
            sandbox=self.config.sandbox,
            auth=self.config.auth,
            tui=self.config.tui,
            telegram=self.config.telegram,
            goal=self.config.goal,
            daemon=self.config.daemon,
            mcp=self.config.mcp,
            providers=self.config.providers,
            source_paths=self.config.source_paths,
        )

    async def _run_agent(self, run: RunRecord, message: str, config: LibreClawConfig) -> None:
        assistant_chunks: list[str] = []
        state: RunState = "done"
        try:
            await self.run_store.update_state(run.run_id, "running")
            await self.run_store.append_event(
                run.run_id,
                "run_started",
                {
                    "kind": run.kind,
                    "provider": run.provider,
                    "model": run.model,
                    "working_directory": run.working_directory,
                    "surface": "daemon",
                },
            )
            await self.run_store.append_event(run.run_id, "user_message", {"content": message})
            agent = await self._create_agent(config)
            async for event in agent.run(message):
                if isinstance(event, AgentTextDelta):
                    assistant_chunks.append(event.text)
                    await self.run_store.append_event(run.run_id, "assistant_delta", {"text": event.text})
                    continue
                if isinstance(event, AgentToolCall):
                    await self.run_store.append_event(
                        run.run_id,
                        "tool_call",
                        {"id": event.call.id, "name": event.call.name, "arguments": dict(event.call.arguments)},
                    )
                    continue
                if isinstance(event, AgentPermissionRequest):
                    active = self.active_runs.get(run.run_id)
                    if active is not None:
                        active.pending_permissions[event.call.id] = event
                    await self.run_store.append_event(
                        run.run_id,
                        "permission_request",
                        {
                            "tool_call_id": event.call.id,
                            "name": event.call.name,
                            "arguments": dict(event.call.arguments),
                        },
                    )
                    await self.run_store.update_state(run.run_id, "blocked")
                    continue
                if isinstance(event, AgentToolResult):
                    await self.run_store.append_event(
                        run.run_id,
                        "tool_result",
                        {
                            "tool_call_id": event.call.id,
                            "name": event.call.name,
                            "arguments": dict(event.call.arguments),
                            "is_error": event.result.is_error,
                            "content": event.result.as_text(),
                            "metadata": dict(event.result.metadata),
                        },
                    )
                    continue
                if isinstance(event, AgentDone):
                    if event.usage is not None:
                        await self.run_store.append_event(run.run_id, "usage", _usage_payload(event.usage))
                    continue
                if isinstance(event, AgentError):
                    state = "failed"
                    await self.run_store.append_event(run.run_id, "error", {"message": event.message})
                    break
        except asyncio.CancelledError:
            state = "cancelled"
            await self.run_store.append_event(run.run_id, "cancelled", {"reason": "Daemon task cancelled."})
        except Exception as exc:
            state = "failed"
            await self.run_store.append_event(run.run_id, "error", {"message": str(exc)})
        finally:
            await self.run_store.finish_run(
                run.run_id,
                state,
                plan=run_plan_text(await self.run_store.load_events(run.run_id)),
                summary="".join(assistant_chunks),
                verification=f"Daemon run finished with state: {state}\n",
                diff="",
            )
            await self.run_store.append_event(run.run_id, "run_finished", {"state": state})

    async def _create_agent(self, config: LibreClawConfig) -> Agent:
        provider = self.provider_factory(config)
        facts = await self.memory_store.list_facts()
        skill_store = SkillStore(config.general.working_directory)
        return Agent(
            session=Session(),
            provider=provider,
            tool_registry=self.registry_factory(config, self.memory_store),
            permission_manager=PermissionManager(config.permissions),
            system_prompt=config.agent.system_prompt,
            max_tool_calls_per_turn=config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=config.agent.auto_compact_threshold,
            context_window_tokens=config.agent.context_window_tokens,
            memory_facts=[fact.fact for fact in facts],
            system_prompt_extra=config.agent.system_prompt_extra,
            skill_provider=skill_store.relevant_skill_texts,
        )


class DaemonClient:
    """Small HTTP client used by future TUI and Telegram daemon adapters."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def start_run(self, message: str, **payload: Any) -> dict[str, Any]:
        return await self._request("POST", "/runs", json={"message": message, **payload})

    async def list_runs(self, limit: int = 20) -> dict[str, Any]:
        return await self._request("GET", f"/runs?limit={limit}")

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/runs/{run_id}")

    async def get_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        return await self._request("GET", f"/runs/{run_id}/events?after={after}")

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/runs/{run_id}/cancel")

    async def resolve_permission(
        self,
        run_id: str,
        tool_call_id: str,
        resolution: PermissionResolution,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/runs/{run_id}/permissions/{tool_call_id}",
            json={"resolution": resolution},
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Daemon returned a non-object JSON payload.")
            return payload


def daemon_base_url(config: LibreClawConfig, *, host: str | None = None, port: int | None = None) -> str:
    return f"http://{host or config.daemon.host}:{port or config.daemon.port}"


def _reject_working_directory_override(config: LibreClawConfig, payload: Mapping[str, Any]) -> None:
    """Reject request-scoped working-directory changes for daemon-owned runs."""
    root = config.general.working_directory
    requested = payload.get("working_directory")
    if requested is None or requested == "":
        return
    requested_text = str(requested)
    if requested_text == str(root):
        return
    raise ValueError(
        "Daemon run payload cannot override working_directory. "
        "Start the daemon with --working-directory or update config instead."
    )


def _json_error(message: str, *, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _positive_int(value: str | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, min(maximum, parsed))


def _run_payload(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "state": run.state,
        "title": run.title,
        "kind": run.kind,
        "provider": run.provider,
        "model": run.model,
        "working_directory": run.working_directory,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "path": str(run.path),
    }


def _event_payload(event: RunEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "type": event.type,
        "data": event.data,
    }


def _artifact_payload(run: RunRecord) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for name in RUN_ARTIFACT_NAMES:
        path = run.path / name
        try:
            stat = path.stat()
        except OSError:
            payload[name] = {"exists": False, "size": 0}
        else:
            payload[name] = {"exists": True, "size": stat.st_size}
    return payload


def _read_artifact(run: RunRecord, name: str) -> str:
    path = run.path / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _usage_payload(usage: Usage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost": usage.cost,
    }
