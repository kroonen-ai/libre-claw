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
    AgentFallback,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    AutomationError,
    AutomationRecord,
    AutomationRoute,
    AutomationStore,
    RunEvent,
    RunRecord,
    RunState,
    RunStore,
    Session,
)
from libre_claw.core.memory import (
    MemoryItem,
    MemoryStore,
    extract_memories_with_provider,
    redact_secrets,
)
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.review import RUN_ARTIFACT_NAMES, browser_artifact_text, run_plan_text
from libre_claw.core.skills import SkillStore
from libre_claw.core.soul import SoulStore
from libre_claw.core.tools import ToolRegistry
from libre_claw.core.usage import (
    load_usage_records,
    openrouter_attribution_payload,
    usage_record_payload,
    usage_report_text,
    usage_summary_payload,
)
from libre_claw.core.session import session_from_payload
from libre_claw.providers import LLMProvider, Usage, create_fallback_providers, create_provider
from libre_claw.tools_builtin import create_builtin_registry


ProviderFactory = Callable[[LibreClawConfig], LLMProvider]
RegistryFactory = Callable[[LibreClawConfig, MemoryStore], ToolRegistry]
RunKind = Literal["chat", "goal"]
TELEGRAM_DAEMON_PROMPT_EXTRA = (
    "Telegram output policy: keep mobile replies compact. Do not narrate intermediate "
    "tool steps such as 'let me fetch' or 'now I will check'. Use tools silently and "
    "send only the final useful result, unless you need approval or hit an error."
)


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
        self.automation_store = AutomationStore(config.automations.root)
        self.active_runs: dict[str, ActiveRun] = {}
        self._app: web.Application | None = None
        self._automation_task: asyncio.Task[None] | None = None

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
                web.get("/usage", self.usage),
                web.get("/automations", self.list_automations),
                web.post("/automations", self.create_automation),
                web.get("/automations/{automation_id}", self.get_automation),
                web.delete("/automations/{automation_id}", self.delete_automation),
                web.post("/automations/{automation_id}/pause", self.pause_automation),
                web.post("/automations/{automation_id}/resume", self.resume_automation),
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
        if self.config.automations.enabled:
            self._automation_task = asyncio.create_task(self._automation_loop())

    async def _on_cleanup(self, _app: web.Application) -> None:
        if self._automation_task is not None and not self._automation_task.done():
            self._automation_task.cancel()
        for active in list(self.active_runs.values()):
            if not active.task.done():
                active.task.cancel()
        tasks = [active.task for active in self.active_runs.values()]
        if self._automation_task is not None:
            tasks.append(self._automation_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

    async def usage(self, request: web.Request) -> web.Response:
        provider = str(request.query.get("provider", "")).strip().lower() or None
        limit = _positive_int(request.query.get("limit"), default=250, maximum=1000)
        records = await load_usage_records(self.run_store, provider=provider, limit=limit)
        return web.json_response(
            {
                "summary": usage_summary_payload(records),
                "records": [usage_record_payload(record) for record in records[:100]],
                "attribution": openrouter_attribution_payload() if provider == "openrouter" else {},
                "text": usage_report_text(records, provider=provider or "all"),
            }
        )

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
        surface = str(payload.get("surface", "daemon")).strip() or "daemon"
        session = session_from_payload(payload.get("session"))
        task = asyncio.create_task(self._run_agent(run, message, run_config, surface=surface, session=session))
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
                browser=_read_artifact(run, "browser.md"),
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

    async def list_automations(self, request: web.Request) -> web.Response:
        limit = _positive_int(request.query.get("limit"), default=50, maximum=200)
        automations = await self.automation_store.list(limit=limit)
        return web.json_response({"automations": [_automation_payload(record) for record in automations]})

    async def get_automation(self, request: web.Request) -> web.Response:
        automation_id = request.match_info["automation_id"]
        automation = await self.automation_store.load(automation_id)
        if automation is None:
            return _json_error("Unknown automation.", status=404)
        return web.json_response({"automation": _automation_payload(automation)})

    async def create_automation(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return _json_error("Request body must be JSON.")
        if not isinstance(payload, Mapping):
            return _json_error("Request body must be a JSON object.")
        try:
            route = cast(AutomationRoute, str(payload.get("route", "report")).lower())
            telegram_chat_id = payload.get("telegram_chat_id")
            automation = await self.automation_store.create(
                name=str(payload.get("name", "")).strip(),
                prompt=str(payload.get("prompt", "")).strip(),
                schedule=str(payload.get("schedule", "")).strip(),
                route=route,
                provider=str(payload.get("provider") or self.config.general.default_provider),
                model=str(payload.get("model") or self.config.general.default_model),
                working_directory=self.config.general.working_directory,
                telegram_chat_id=telegram_chat_id if isinstance(telegram_chat_id, int) else None,
                metadata={"created_by": "daemon_api"},
            )
        except AutomationError as exc:
            return _json_error(str(exc))
        return web.json_response({"automation": _automation_payload(automation)}, status=201)

    async def pause_automation(self, request: web.Request) -> web.Response:
        automation = await self.automation_store.update_status(request.match_info["automation_id"], "paused")
        if automation is None:
            return _json_error("Unknown automation.", status=404)
        return web.json_response({"automation": _automation_payload(automation)})

    async def resume_automation(self, request: web.Request) -> web.Response:
        automation = await self.automation_store.update_status(request.match_info["automation_id"], "active")
        if automation is None:
            return _json_error("Unknown automation.", status=404)
        return web.json_response({"automation": _automation_payload(automation)})

    async def delete_automation(self, request: web.Request) -> web.Response:
        deleted = await self.automation_store.delete(request.match_info["automation_id"])
        if not deleted:
            return _json_error("Unknown automation.", status=404)
        return web.json_response({"deleted": True})

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
            fallback=self.config.fallback,
            heartbeat=self.config.heartbeat,
            memory=self.config.memory,
            daemon=self.config.daemon,
            automations=self.config.automations,
            browser=self.config.browser,
            mcp=self.config.mcp,
            providers=self.config.providers,
            source_paths=self.config.source_paths,
        )

    async def _run_agent(
        self,
        run: RunRecord,
        message: str,
        config: LibreClawConfig,
        *,
        surface: str = "daemon",
        hold_final_state: bool = False,
        session: Session | None = None,
    ) -> RunState:
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
                    "surface": surface,
                },
            )
            await self.run_store.append_event(run.run_id, "user_message", {"content": message})
            agent = await self._create_agent(config, session=session, surface=surface)
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
                        await self.run_store.append_event(
                            run.run_id,
                            "usage",
                            _usage_payload(event.usage, provider=run.provider, model=run.model, surface=surface),
                        )
                    continue
                if isinstance(event, AgentError):
                    state = "failed"
                    await self.run_store.append_event(run.run_id, "error", {"message": event.message})
                    break
                if isinstance(event, AgentFallback):
                    await self.run_store.append_event(
                        run.run_id,
                        "provider_fallback",
                        {"provider": event.provider_label, "reason": event.reason},
                    )
                    continue
        except asyncio.CancelledError:
            state = "cancelled"
            await self.run_store.append_event(run.run_id, "cancelled", {"reason": "Daemon task cancelled."})
        except Exception as exc:
            state = "failed"
            await self.run_store.append_event(run.run_id, "error", {"message": str(exc)})
        finally:
            persisted_state: RunState = "running" if hold_final_state and state in {"done", "failed", "cancelled"} else state
            await self.run_store.finish_run(
                run.run_id,
                persisted_state,
                plan=run_plan_text(await self.run_store.load_events(run.run_id)),
                summary="".join(assistant_chunks),
                verification=f"Daemon run finished with state: {state}\n",
                diff="",
                browser=browser_artifact_text(await self.run_store.load_events(run.run_id)),
            )
            if not hold_final_state:
                await self.run_store.append_event(run.run_id, "run_finished", {"state": state})
            if state == "done":
                await self._extract_run_memory(config, run, message, "".join(assistant_chunks))
        return state

    async def _automation_loop(self) -> None:
        try:
            while True:
                await self._tick_automations()
                await asyncio.sleep(max(1.0, self.config.automations.poll_interval))
        except asyncio.CancelledError:
            raise

    async def _tick_automations(self) -> None:
        due = await self.automation_store.due(limit=max(1, self.config.automations.max_due_per_tick))
        for automation in due:
            try:
                await self._start_automation_run(automation)
            except Exception as exc:
                await self._record_automation_error(automation, exc)

    async def _start_automation_run(self, automation: AutomationRecord) -> RunRecord:
        config = self._config_for_automation(automation)
        title = f"Scheduled: {automation.name}"
        run = await self.run_store.create_run(
            title,
            kind="chat",
            provider=config.general.default_provider,
            model=config.general.default_model,
            working_directory=config.general.working_directory,
            state="queued",
        )
        report_path = self.automation_store.report_path(automation.automation_id, run.run_id)
        await self.automation_store.mark_run(automation.automation_id, run.run_id, report_path=report_path)
        await self.run_store.append_event(
            run.run_id,
            "automation_triggered",
            {
                "automation_id": automation.automation_id,
                "name": automation.name,
                "schedule": automation.schedule,
                "route": automation.route,
                "telegram_chat_id": automation.telegram_chat_id,
                "report_path": str(report_path),
            },
        )
        task = asyncio.create_task(self._run_automation_agent(automation, run, config, report_path))
        active = ActiveRun(run_id=run.run_id, task=task)
        self.active_runs[run.run_id] = active
        task.add_done_callback(lambda _task, run_id=run.run_id: self.active_runs.pop(run_id, None))
        return run

    async def _run_automation_agent(
        self,
        automation: AutomationRecord,
        run: RunRecord,
        config: LibreClawConfig,
        report_path: Path,
    ) -> None:
        state = await self._run_agent(
            run,
            automation.prompt,
            config,
            surface=f"automation:{automation.route}",
            hold_final_state=True,
        )
        try:
            await asyncio.to_thread(_write_automation_report, automation, run, report_path)
            await self.run_store.append_event(
                run.run_id,
                "automation_report_written",
                {"automation_id": automation.automation_id, "report_path": str(report_path)},
            )
        except Exception as exc:
            state = "failed"
            await self.run_store.append_event(
                run.run_id,
                "error",
                {"message": f"Could not write automation report: {exc}"},
            )
        await self.run_store.update_state(run.run_id, state)
        await self.run_store.append_event(run.run_id, "run_finished", {"state": state})

    def _config_for_automation(self, automation: AutomationRecord) -> LibreClawConfig:
        provider = automation.provider or self.config.general.default_provider
        model = automation.model or self.config.general.default_model
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
            fallback=self.config.fallback,
            heartbeat=self.config.heartbeat,
            memory=self.config.memory,
            daemon=self.config.daemon,
            automations=self.config.automations,
            browser=self.config.browser,
            mcp=self.config.mcp,
            providers=self.config.providers,
            source_paths=self.config.source_paths,
        )

    async def _record_automation_error(self, automation: AutomationRecord, exc: Exception) -> None:
        run = await self.run_store.create_run(
            f"Scheduled failed: {automation.name}",
            kind="chat",
            provider=automation.provider or self.config.general.default_provider,
            model=automation.model or self.config.general.default_model,
            working_directory=self.config.general.working_directory,
            state="failed",
        )
        message = str(exc)
        await self.run_store.append_event(
            run.run_id,
            "automation_error",
            {"automation_id": automation.automation_id, "name": automation.name, "message": message},
        )
        await self.run_store.finish_run(
            run.run_id,
            "failed",
            plan="",
            summary=message,
            verification=f"Automation {automation.automation_id} failed before run start.\n",
            diff="",
            browser="",
        )

    async def _create_agent(self, config: LibreClawConfig, *, session: Session | None = None, surface: str = "daemon") -> Agent:
        provider = self.provider_factory(config)
        fallbacks = create_fallback_providers(config)
        memory_facts = await self.memory_store.list_always_injected_memories()
        skill_store = SkillStore(config.general.working_directory)
        soul_store = SoulStore(config.general.working_directory)
        return Agent(
            session=session or Session(),
            provider=provider,
            tool_registry=self.registry_factory(config, self.memory_store),
            permission_manager=PermissionManager(config.permissions),
            system_prompt=config.agent.system_prompt,
            max_tool_calls_per_turn=config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=config.agent.auto_compact_threshold,
            context_window_tokens=config.agent.context_window_tokens,
            memory_facts=memory_facts,
            system_prompt_extra=_surface_prompt_extra(config.agent.system_prompt_extra, surface),
            skill_provider=skill_store.relevant_skill_texts,
            soul_provider=soul_store.soul_texts,
            memory_provider=lambda user_message: self._relevant_memory_texts(config, user_message),
            fallback_providers=tuple((fallback.label, fallback.provider) for fallback in fallbacks),
        )

    async def _relevant_memory_texts(self, config: LibreClawConfig, user_message: str) -> list[str]:
        if not config.memory.enabled or not config.memory.inject_relevant:
            return []
        items = await self.memory_store.search_memory_items(
            user_message,
            project_root=config.general.working_directory,
            limit=max(1, config.memory.max_injected_items),
        )
        return _memory_texts_with_budget(items, config.memory.max_injected_tokens)

    async def _extract_run_memory(
        self,
        config: LibreClawConfig,
        run: RunRecord,
        user_message: str,
        assistant_text: str,
    ) -> None:
        if not config.memory.enabled or not assistant_text.strip():
            return
        if config.memory.auto_summarize:
            try:
                await self.memory_store.add_memory_item(
                    kind="summary",
                    scope="project",
                    text=_memory_summary_text(user_message, assistant_text),
                    source_type="run",
                    source_id=f"{run.run_id}:summary",
                    project_root=run.working_directory or config.general.working_directory,
                )
            except Exception:
                pass
        if not config.memory.auto_extract:
            return
        try:
            provider = self.provider_factory(config)
            existing = [
                item.text
                for item in await self.memory_store.search_memory_items(
                    user_message,
                    project_root=config.general.working_directory,
                    limit=8,
                )
            ]
            extracted = await extract_memories_with_provider(
                provider,
                user_message=user_message,
                assistant_text=assistant_text,
                existing_memories=existing,
            )
            for index, memory in enumerate(extracted):
                await self.memory_store.add_memory_item(
                    kind=memory.kind,
                    scope=memory.scope,
                    text=memory.text,
                    source_type="run",
                    source_id=f"{run.run_id}:memory:{index}",
                    project_root=config.general.working_directory if memory.scope == "project" else "",
                )
        except Exception:
            return


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

    async def usage(self, *, provider: str = "", limit: int = 250) -> dict[str, Any]:
        query = f"limit={limit}"
        if provider:
            query += f"&provider={provider}"
        return await self._request("GET", f"/usage?{query}")

    async def list_automations(self, limit: int = 50) -> dict[str, Any]:
        return await self._request("GET", f"/automations?limit={limit}")

    async def create_automation(self, **payload: Any) -> dict[str, Any]:
        return await self._request("POST", "/automations", json=payload)

    async def get_automation(self, automation_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/automations/{automation_id}")

    async def pause_automation(self, automation_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/automations/{automation_id}/pause")

    async def resume_automation(self, automation_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/automations/{automation_id}/resume")

    async def delete_automation(self, automation_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/automations/{automation_id}")

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


def _surface_prompt_extra(existing: str, surface: str) -> str:
    parts = [existing.strip()] if existing.strip() else []
    if surface.startswith("telegram") or surface == "automation:telegram":
        parts.append(TELEGRAM_DAEMON_PROMPT_EXTRA)
    return "\n\n".join(parts)


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


def _automation_payload(record: AutomationRecord) -> dict[str, Any]:
    return {
        "automation_id": record.automation_id,
        "name": record.name,
        "prompt": record.prompt,
        "schedule": record.schedule,
        "route": record.route,
        "status": record.status,
        "provider": record.provider,
        "model": record.model,
        "working_directory": record.working_directory,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "next_run_at": record.next_run_at,
        "last_run_at": record.last_run_at,
        "last_run_id": record.last_run_id,
        "telegram_chat_id": record.telegram_chat_id,
        "report_path": record.report_path,
        "metadata": record.metadata,
        "path": str(record.path),
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


def _usage_payload(usage: Usage, *, provider: str = "", model: str = "", surface: str = "") -> dict[str, Any]:
    payload = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost": usage.cost,
    }
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    if surface:
        payload["surface"] = surface
    return payload


def _memory_texts_with_budget(items: list[MemoryItem], max_tokens: int) -> list[str]:
    budget = max(1, max_tokens) * 4
    selected: list[str] = []
    used = 0
    for item in items:
        text = f"[{item.kind}/{item.scope}] {item.text}"
        cost = len(text)
        if selected and used + cost > budget:
            break
        selected.append(text[:budget])
        used += cost
    return selected


def _memory_summary_text(user_message: str, assistant_text: str) -> str:
    parts = []
    if user_message.strip():
        parts.append("User asked: " + user_message.strip()[:500])
    if assistant_text.strip():
        parts.append("Libre Claw response: " + assistant_text.strip()[:1500])
    return redact_secrets("\n".join(parts).strip())


def _write_automation_report(automation: AutomationRecord, run: RunRecord, report_path: Path) -> None:
    summary = _read_artifact(run, "summary.md").strip()
    verification = _read_artifact(run, "verification.md").strip()
    browser = _read_artifact(run, "browser.md").strip()
    diff_path = run.path / "diff.patch"
    try:
        diff_size = diff_path.stat().st_size
    except OSError:
        diff_size = 0
    lines = [
        f"# {automation.name}",
        "",
        f"- Automation: `{automation.automation_id}`",
        f"- Run: `{run.run_id}`",
        f"- Schedule: `{automation.schedule}`",
        f"- Route: `{automation.route}`",
        f"- Model: `{run.provider}:{run.model}`",
        f"- Working directory: `{run.working_directory or 'unknown'}`",
        "",
        "## Summary",
        "",
        summary or "No assistant summary was produced.",
        "",
        "## Verification",
        "",
        verification or "No verification artifact was produced.",
        "",
        "## Browser",
        "",
        browser or "No browser artifacts were recorded.",
        "",
        "## Artifacts",
        "",
        f"- Run directory: `{run.path}`",
        f"- Diff size: {diff_size} bytes",
    ]
    if automation.route == "telegram" and automation.telegram_chat_id is not None:
        lines.append(f"- Telegram chat: `{automation.telegram_chat_id}`")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_name(f".{report_path.name}.tmp")
    tmp_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp_path.replace(report_path)
