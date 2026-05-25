# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.core.automations import (
    AutomationError,
    AutomationRecord,
    AutomationRoute,
    AutomationStore,
    automation_examples,
)
from libre_claw.core import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    Session,
)
from libre_claw.core.memory import MemoryStore
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.session import session_to_payload
from libre_claw.core.skills import SkillStore
from libre_claw.core.soul import SoulStore
from libre_claw.core.tools import ToolCall
from libre_claw.daemon import DaemonClient
from libre_claw.providers import ProviderConfigurationError, Usage, combine_usage, create_provider
from libre_claw.tools_builtin import create_builtin_registry


@dataclass(frozen=True)
class TelegramText:
    text: str


@dataclass(frozen=True)
class TelegramToolNotice:
    text: str


@dataclass(frozen=True)
class TelegramPermissionPrompt:
    prompt_id: str
    call: ToolCall
    text: str


@dataclass(frozen=True)
class TelegramDone:
    usage: Usage | None = None


@dataclass(frozen=True)
class TelegramError:
    text: str


TelegramEvent = TelegramText | TelegramToolNotice | TelegramPermissionPrompt | TelegramDone | TelegramError


@dataclass
class TelegramChatState:
    chat_id: int
    session: Session = field(default_factory=Session)
    usage: Usage = field(default_factory=Usage)
    task: asyncio.Task[None] | None = None
    pending_permissions: dict[str, AgentPermissionRequest] = field(default_factory=dict)
    daemon_run_id: str | None = None
    daemon_event_id: int = 0


class TelegramBridge:
    """Bridge Telegram chats to the same Libre Claw agent core."""

    def __init__(
        self,
        config: LibreClawConfig,
        memory_store: MemoryStore | None = None,
        daemon_client: DaemonClient | None = None,
    ) -> None:
        self.config = config
        self.memory_store = memory_store or MemoryStore()
        self.daemon_client = daemon_client
        self.skill_store = SkillStore(config.general.working_directory)
        self.soul_store = SoulStore(config.general.working_directory)
        self.automation_store = AutomationStore(config.automations.root)
        self._states: dict[int, TelegramChatState] = {}
        self._memory_facts: list[str] = []

    async def initialize(self) -> None:
        await self.memory_store.initialize()
        facts = await self.memory_store.list_facts()
        self._memory_facts = [fact.fact for fact in facts]

    def state_for(self, chat_id: int) -> TelegramChatState:
        return self._states.setdefault(chat_id, TelegramChatState(chat_id=chat_id))

    def new_session(self, chat_id: int) -> TelegramChatState:
        state = TelegramChatState(chat_id=chat_id)
        self._states[chat_id] = state
        return state

    async def stream_message(self, chat_id: int, text: str):
        if self.daemon_client is not None:
            async for event in self._stream_daemon_message(chat_id, text):
                yield event
            return

        state = self.state_for(chat_id)
        try:
            agent = self._create_agent(state)
        except ProviderConfigurationError as exc:
            yield TelegramError(str(exc))
            return

        async for event in agent.run(text):
            if isinstance(event, AgentTextDelta):
                yield TelegramText(event.text)
                continue
            if isinstance(event, AgentToolCall):
                yield TelegramToolNotice(f"Calling {event.call.name} with {dict(event.call.arguments)}")
                continue
            if isinstance(event, AgentPermissionRequest):
                prompt_id = f"{chat_id}:{event.call.id}"
                state.pending_permissions[prompt_id] = event
                yield TelegramPermissionPrompt(
                    prompt_id=prompt_id,
                    call=event.call,
                    text=f"Approve {event.call.name} with {dict(event.call.arguments)}?",
                )
                continue
            if isinstance(event, AgentToolResult):
                status = "error" if event.result.is_error else "result"
                yield TelegramToolNotice(f"{event.call.name} {status}: {event.result.as_text()}")
                continue
            if isinstance(event, AgentDone):
                if event.usage is not None:
                    state.usage = combine_usage(state.usage, event.usage) or state.usage
                yield TelegramDone(event.usage)
                continue
            if isinstance(event, AgentError):
                yield TelegramError(event.message)
                return

    def resolve_permission(self, prompt_id: str, resolution: PermissionResolution) -> bool:
        if prompt_id.startswith("daemon:"):
            return False
        chat_id_text, _, _ = prompt_id.partition(":")
        if not chat_id_text.isdigit():
            return False
        state = self._states.get(int(chat_id_text))
        if state is None:
            return False
        request = state.pending_permissions.pop(prompt_id, None)
        if request is None or request.future.done():
            return False
        request.future.set_result(resolution)
        return True

    async def resolve_permission_async(self, prompt_id: str, resolution: PermissionResolution) -> bool:
        if not prompt_id.startswith("daemon:"):
            return self.resolve_permission(prompt_id, resolution)
        if self.daemon_client is None:
            return False
        parts = prompt_id.split(":", 2)
        if len(parts) != 3:
            return False
        _, run_id, tool_call_id = parts
        try:
            await self.daemon_client.resolve_permission(run_id, tool_call_id, resolution)
        except Exception:
            return False
        return True

    def cancel(self, chat_id: int) -> bool:
        state = self.state_for(chat_id)
        if state.task is None or state.task.done():
            return False
        state.task.cancel()
        return True

    async def cancel_async(self, chat_id: int) -> bool:
        state = self.state_for(chat_id)
        cancelled = self.cancel(chat_id)
        if self.daemon_client is None or state.daemon_run_id is None:
            return cancelled
        try:
            await self.daemon_client.cancel_run(state.daemon_run_id)
        except Exception:
            return cancelled
        return True

    def status_text(self, chat_id: int) -> str:
        state = self.state_for(chat_id)
        return (
            f"Tokens: {state.usage.total_tokens} total "
            f"({state.usage.input_tokens} input, {state.usage.output_tokens} output). "
            f"Cost: {_format_usage_cost(state.usage)}."
        )

    async def schedule_text(self, chat_id: int, argument: str) -> str:
        try:
            parsed = _parse_schedule_text(argument, default_route="telegram")
        except AutomationError as exc:
            return str(exc)

        action = str(parsed["action"])
        if action == "examples":
            return _schedule_examples_text()
        if action == "list":
            return await self._schedule_list_text()
        if action == "add":
            payload = {
                "name": str(parsed["name"]),
                "prompt": str(parsed["prompt"]),
                "schedule": str(parsed["schedule"]),
                "route": str(parsed.get("route", "telegram")),
                "provider": self.config.general.default_provider,
                "model": self.config.general.default_model,
                "telegram_chat_id": chat_id,
            }
            try:
                if self.daemon_client is not None:
                    created = await self.daemon_client.create_automation(**payload)
                    record = _object_payload(created.get("automation", created))
                else:
                    stored = await self.automation_store.create(
                        name=payload["name"],
                        prompt=payload["prompt"],
                        schedule=payload["schedule"],
                        route=cast_automation_route(payload["route"]),
                        provider=payload["provider"],
                        model=payload["model"],
                        working_directory=self.config.general.working_directory,
                        telegram_chat_id=chat_id,
                        metadata={"created_by": "telegram"},
                    )
                    record = _automation_record_payload(stored)
            except Exception as exc:
                return f"Could not create schedule: {exc}"
            return "Scheduled:\n" + _automation_line(record)
        automation_id = str(parsed.get("automation_id", ""))
        try:
            if action == "pause":
                result = await self._update_schedule_status(automation_id, "paused")
                return "Updated schedule:\n" + _automation_line(_object_payload(result.get("automation", result)))
            if action == "resume":
                result = await self._update_schedule_status(automation_id, "active")
                return "Updated schedule:\n" + _automation_line(_object_payload(result.get("automation", result)))
            if action == "delete":
                if self.daemon_client is not None:
                    await self.daemon_client.delete_automation(automation_id)
                elif not await self.automation_store.delete(automation_id):
                    raise AutomationError("Unknown automation.")
                return f"Deleted schedule {automation_id}."
        except Exception as exc:
            return f"Could not {action} schedule: {exc}"
        return _schedule_help_text()

    async def _schedule_list_text(self) -> str:
        if self.daemon_client is not None:
            payload = await self.daemon_client.list_automations()
            records = [dict(item) for item in payload.get("automations", []) if isinstance(item, dict)]
        else:
            records = [_automation_record_payload(record) for record in await self.automation_store.list()]
        if not records:
            return "No schedules yet. Try /schedule examples."
        return "Schedules:\n" + "\n".join(_automation_line(record) for record in records)

    async def _update_schedule_status(self, automation_id: str, status: str) -> dict[str, Any]:
        if self.daemon_client is not None:
            if status == "paused":
                return await self.daemon_client.pause_automation(automation_id)
            return await self.daemon_client.resume_automation(automation_id)
        if status == "paused":
            record = await self.automation_store.update_status(automation_id, "paused")
        else:
            record = await self.automation_store.update_status(automation_id, "active")
        return {"automation": _automation_record_payload(_require_record(record))}

    def _create_agent(self, state: TelegramChatState) -> Agent:
        provider = create_provider(self.config)
        return Agent(
            session=state.session,
            provider=provider,
            tool_registry=create_builtin_registry(self.config, memory_store=self.memory_store),
            permission_manager=PermissionManager(self.config.permissions),
            system_prompt=self.config.agent.system_prompt,
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            context_window_tokens=self.config.agent.context_window_tokens,
            memory_facts=self._memory_facts,
            system_prompt_extra=self.config.agent.system_prompt_extra,
            skill_provider=self.skill_store.relevant_skill_texts,
            soul_provider=self.soul_store.soul_texts,
        )

    async def _stream_daemon_message(self, chat_id: int, text: str):
        if self.daemon_client is None:
            yield TelegramError("Daemon client is not configured.")
            return

        state = self.state_for(chat_id)
        try:
            started = await self.daemon_client.start_run(
                text,
                kind="chat",
                provider=self.config.general.default_provider,
                model=self.config.general.default_model,
                working_directory=str(self.config.general.working_directory),
                surface="telegram:daemon",
                telegram_chat_id=chat_id,
                session=session_to_payload(state.session),
            )
        except Exception as exc:
            yield TelegramError(f"Could not start daemon run: {exc}")
            return

        run = _object_payload(started.get("run"))
        run_id = str(run.get("run_id", ""))
        if not run_id:
            yield TelegramError("Daemon did not return a run id.")
            return
        state.daemon_run_id = run_id
        state.daemon_event_id = 0
        assistant_chunks: list[str] = []
        yielded_done = False
        yield TelegramToolNotice(f"Daemon run {run_id} started.")

        while True:
            try:
                payload = await self.daemon_client.get_events(run_id, after=state.daemon_event_id)
            except Exception as exc:
                yield TelegramError(f"Daemon event polling failed: {exc}")
                return

            events = payload.get("events", [])
            if not isinstance(events, list):
                events = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                state.daemon_event_id = max(state.daemon_event_id, int(event.get("event_id", 0) or 0))
                data = _object_payload(event.get("data"))
                if str(event.get("type", "")) == "usage":
                    usage = _usage_from_payload(data)
                    state.usage = combine_usage(state.usage, usage) or state.usage
                    continue
                async for mapped in _telegram_events_from_daemon_event(run_id, event):
                    if isinstance(mapped, TelegramText):
                        assistant_chunks.append(mapped.text)
                    yielded_done = yielded_done or isinstance(mapped, TelegramDone)
                    yield mapped

            try:
                detail = await self.daemon_client.get_run(run_id)
            except Exception as exc:
                yield TelegramError(f"Daemon run lookup failed: {exc}")
                return
            run = _object_payload(detail.get("run"))
            run_state = str(run.get("state", ""))
            if run_state in {"done", "failed", "cancelled"}:
                if run_state == "done":
                    state.session.add_user_message(text)
                    assistant_text = "".join(assistant_chunks)
                    if assistant_text:
                        state.session.add_assistant_message(assistant_text)
                if not yielded_done:
                    yield TelegramDone(None)
                return
            await asyncio.sleep(max(0.1, self.config.daemon.poll_interval))


def _format_usage_cost(usage: Usage) -> str:
    if usage.cost is None or usage.cost == 0:
        return "$0.00"
    if usage.cost < 0.01:
        return f"${usage.cost:.6f}"
    return f"${usage.cost:.2f}"


async def _telegram_events_from_daemon_event(run_id: str, event: dict[str, Any]):
    data = _object_payload(event.get("data"))
    event_type = str(event.get("type", ""))
    if event_type == "assistant_delta":
        yield TelegramText(str(data.get("text", "")))
        return
    if event_type == "tool_call":
        yield TelegramToolNotice(f"Calling {data.get('name', 'tool')} with {_object_payload(data.get('arguments'))}")
        return
    if event_type == "permission_request":
        call = ToolCall(
            id=str(data.get("tool_call_id", "")),
            name=str(data.get("name", "tool")),
            arguments=_object_payload(data.get("arguments")),
        )
        yield TelegramPermissionPrompt(
            prompt_id=f"daemon:{run_id}:{call.id}",
            call=call,
            text=f"Approve daemon run {run_id} tool {call.name} with {dict(call.arguments)}?",
        )
        return
    if event_type == "tool_result":
        status = "error" if data.get("is_error") else "result"
        yield TelegramToolNotice(f"{data.get('name', 'tool')} {status}: {data.get('content', '')}")
        return
    if event_type == "error":
        yield TelegramError(str(data.get("message", "Daemon run failed.")))
        return
    if event_type == "run_finished":
        yield TelegramDone(None)


def _object_payload(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _usage_from_payload(data: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=_int_payload(data.get("input_tokens")),
        output_tokens=_int_payload(data.get("output_tokens")),
        cached_tokens=_int_payload(data.get("cached_tokens")),
        reasoning_tokens=_int_payload(data.get("reasoning_tokens")),
        cost=_float_payload(data.get("cost")),
    )


def _int_payload(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _float_payload(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_schedule_text(argument: str, *, default_route: AutomationRoute) -> dict[str, object]:
    stripped = argument.strip()
    if not stripped:
        return {"action": "list"}
    parts = stripped.split(maxsplit=1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if action in {"list", "ls"}:
        return {"action": "list"}
    if action == "examples":
        return {"action": "examples"}
    if action in {"pause", "resume", "delete", "del", "rm"}:
        if not rest:
            raise AutomationError(f"Usage: /schedule {action} <id>")
        return {"action": "delete" if action in {"del", "rm"} else action, "automation_id": rest.split()[0]}
    if action != "add":
        raise AutomationError(_schedule_help_text())
    fields = [field.strip() for field in rest.split("|", maxsplit=2)]
    if len(fields) != 3 or not all(fields):
        raise AutomationError("Usage: /schedule add [--route report|tui|telegram] <schedule> | <name> | <prompt>")
    route = default_route
    tokens = shlex.split(fields[0])
    schedule_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] == "--route":
            if index + 1 >= len(tokens):
                raise AutomationError("--route requires report, tui, or telegram.")
            route = cast_automation_route(tokens[index + 1])
            index += 2
            continue
        schedule_tokens.append(tokens[index])
        index += 1
    return {
        "action": "add",
        "route": route,
        "schedule": " ".join(schedule_tokens),
        "name": fields[1],
        "prompt": fields[2],
    }


def cast_automation_route(value: object) -> AutomationRoute:
    route = str(value).lower()
    if route not in {"report", "tui", "telegram"}:
        raise AutomationError("Automation route must be report, tui, or telegram.")
    return route  # type: ignore[return-value]


def _require_record(record: AutomationRecord | None) -> AutomationRecord:
    if record is None:
        raise AutomationError("Unknown automation.")
    return record


def _schedule_help_text() -> str:
    return "\n".join(
        [
            "Usage:",
            "/schedule list",
            "/schedule examples",
            "/schedule add [--route report|tui|telegram] <schedule> | <name> | <prompt>",
            "/schedule pause <id>",
            "/schedule resume <id>",
            "/schedule delete <id>",
        ]
    )


def _schedule_examples_text() -> str:
    lines = ["Schedule examples:"]
    for name, schedule, prompt in automation_examples():
        lines.append(f"/schedule add {schedule} | {name} | {prompt}")
    return "\n".join(lines)


def _automation_line(record: dict[str, Any]) -> str:
    last_run = record.get("last_run_id") or "never"
    return (
        f"{record.get('automation_id', '')} [{record.get('status', 'unknown')}] "
        f"{record.get('schedule', '')} -> {record.get('route', 'report')} "
        f"next={record.get('next_run_at', 'unknown')} last={last_run} "
        f"{record.get('name', 'Untitled')}"
    ).strip()


def _automation_record_payload(record: AutomationRecord) -> dict[str, Any]:
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
