# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

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
    AgentFallback,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    Session,
)
from libre_claw.core.memory import (
    MemoryItem,
    MemoryStore,
    extract_memories_with_provider,
    new_session_archive_id,
    redact_secrets,
    summarize_session_for_memory,
)
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.runs import RunRecord, RunStore
from libre_claw.core.session import UserAttachment, estimate_context_tokens, session_to_payload
from libre_claw.core.skills import SkillStore
from libre_claw.core.soul import SoulStore
from libre_claw.core.tools import ToolCall
from libre_claw.daemon import DaemonClient
from libre_claw.providers import (
    ProviderConfigurationError,
    Usage,
    combine_usage,
    create_fallback_providers,
    create_provider,
)
from libre_claw.providers.openrouter_metadata import apply_openrouter_model_limits, detect_openrouter_model_limits
from libre_claw.tools_builtin import create_builtin_registry


@dataclass(frozen=True)
class TelegramText:
    text: str


@dataclass(frozen=True)
class TelegramToolNotice:
    text: str
    tool_name: str = ""
    is_error: bool = False
    is_result: bool = False


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
TELEGRAM_NOTICE_LIMIT = 1200
TELEGRAM_ARGUMENT_LIMIT = 700
TELEGRAM_HTTP_ERROR_LIMIT = 500
TELEGRAM_SYSTEM_PROMPT_EXTRA = (
    "Telegram output policy: keep mobile replies compact. Do not narrate intermediate "
    "tool steps such as 'let me fetch' or 'now I will check'. Use tools silently and "
    "send only the final useful result, unless you need approval or hit an error. "
    "When a Telegram user asks for a file, create or download it inside the configured "
    "working directory and include its absolute path in the final answer; the Telegram "
    "bridge can upload safe existing workspace files from that path."
)


@dataclass
class TelegramChatState:
    chat_id: int
    session: Session = field(default_factory=Session)
    usage: Usage = field(default_factory=Usage)
    last_usage: Usage = field(default_factory=Usage)
    task: asyncio.Task[None] | None = None
    pending_permissions: dict[str, AgentPermissionRequest] = field(default_factory=dict)
    daemon_run_id: str | None = None
    daemon_event_id: int = 0
    archive_id: str = field(default_factory=lambda: new_session_archive_id("telegram"))


@dataclass(frozen=True)
class TelegramContextMeter:
    used_tokens: int
    context_window_tokens: int
    ratio: float
    source: str

    @property
    def percent(self) -> int:
        return min(999, int(round(self.ratio * 100)))

    @property
    def display_percent(self) -> str:
        if self.used_tokens > 0 and self.ratio < 0.01:
            return "<1%"
        return f"{self.percent}%"


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
        self.run_store = RunStore()
        self._states: dict[int, TelegramChatState] = {}
        self._memory_facts: list[str] = []
        self.memory_enabled = config.memory.enabled

    async def initialize(self) -> None:
        await self.memory_store.initialize()
        self._memory_facts = await self.memory_store.list_always_injected_memories()

    def state_for(self, chat_id: int) -> TelegramChatState:
        return self._states.setdefault(chat_id, TelegramChatState(chat_id=chat_id))

    def new_session(self, chat_id: int) -> TelegramChatState:
        state = TelegramChatState(chat_id=chat_id)
        self._states[chat_id] = state
        return state

    async def stream_message(
        self,
        chat_id: int,
        text: str,
        attachments: Sequence[UserAttachment] = (),
    ):
        await self._archive_event(
            chat_id,
            "user_message",
            {
                "content": text,
                "attachments": [_attachment_metadata(attachment) for attachment in attachments],
            },
        )
        if self.daemon_client is not None:
            async for event in self._stream_daemon_message(chat_id, text, attachments=tuple(attachments)):
                yield event
            return

        state = self.state_for(chat_id)
        self.config = await self._with_openrouter_model_limits(self.config)
        try:
            agent = self._create_agent(state)
        except ProviderConfigurationError as exc:
            yield TelegramError(str(exc))
            return

        async for event in agent.run(text, attachments=attachments):
            if isinstance(event, AgentTextDelta):
                yield TelegramText(event.text)
                continue
            if isinstance(event, AgentToolCall):
                await self._archive_event(chat_id, "tool_call", {"name": event.call.name, "arguments": dict(event.call.arguments)})
                yield TelegramToolNotice(
                    _tool_call_notice(event.call.name, dict(event.call.arguments)),
                    tool_name=event.call.name,
                )
                continue
            if isinstance(event, AgentPermissionRequest):
                prompt_id = f"{chat_id}:{event.call.id}"
                state.pending_permissions[prompt_id] = event
                await self._archive_event(chat_id, "permission_request", {"name": event.call.name, "arguments": dict(event.call.arguments)})
                yield TelegramPermissionPrompt(
                    prompt_id=prompt_id,
                    call=event.call,
                    text=_permission_notice(event.call.name, dict(event.call.arguments)),
                )
                continue
            if isinstance(event, AgentToolResult):
                status = "error" if event.result.is_error else "result"
                await self._archive_event(
                    chat_id,
                    "tool_result",
                    {
                        "name": event.call.name,
                        "is_error": event.result.is_error,
                        "content": event.result.as_text(),
                        "metadata": dict(event.result.metadata),
                    },
                )
                yield TelegramToolNotice(
                    _tool_result_notice(
                        event.call.name,
                        is_error=event.result.is_error,
                        content=event.result.as_text(),
                        metadata=dict(event.result.metadata),
                    ),
                    tool_name=event.call.name,
                    is_error=event.result.is_error,
                    is_result=True,
                )
                continue
            if isinstance(event, AgentDone):
                if event.usage is not None:
                    state.usage = combine_usage(state.usage, event.usage) or state.usage
                    state.last_usage = event.usage
                assistant_text = _latest_assistant_text(state.session)
                if assistant_text:
                    await self._archive_event(chat_id, "assistant_message", {"content": assistant_text})
                    await self._extract_turn_memory(chat_id, text, assistant_text)
                yield TelegramDone(event.usage)
                continue
            if isinstance(event, AgentError):
                await self._archive_event(chat_id, "error", {"message": event.message})
                yield TelegramError(event.message)
                return
            if isinstance(event, AgentFallback):
                await self._archive_event(chat_id, "provider_fallback", {"provider": event.provider_label, "reason": event.reason})
                yield TelegramToolNotice(f"🔁 Provider fallback: {event.provider_label}\n{_compact_text(event.reason)}")
                continue

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
        provider = _canonical_provider(self.config.general.default_provider)
        model = self.config.general.default_model or _provider_default_model(self.config, provider)
        meter = _telegram_context_meter(self.config, state, self.soul_store, self._memory_facts)
        usage = state.usage
        last_usage = state.last_usage
        lines = [
            "## Libre Claw Status",
            "",
            "**Model**",
            f"- Provider: `{provider}`",
            f"- Model: `{model}`",
            "",
            "**Context**",
            f"- Window: {_format_token_count(meter.context_window_tokens)} tokens",
            f"- Used: ~{_format_token_count(meter.used_tokens)} tokens ({meter.source})",
            f"- Fill: `{_context_bar(meter)}` {meter.display_percent}",
            "",
            "**Usage**",
            f"- Tokens: {usage.total_tokens} total ({usage.input_tokens} input, {usage.output_tokens} output)",
        ]
        if last_usage.total_tokens:
            lines.append(
                f"- Last turn: {last_usage.total_tokens} tokens "
                f"({last_usage.input_tokens} input, {last_usage.output_tokens} output)"
            )
        if usage.cached_tokens:
            lines.append(f"- Cached input: {usage.cached_tokens}")
        if usage.reasoning_tokens:
            lines.append(f"- Reasoning output: {usage.reasoning_tokens}")
        lines.append(f"- Cost: {_format_usage_cost(usage)}")
        if state.daemon_run_id:
            lines.extend(["", "**Run**", f"- Active daemon run: `{_short_run_id(state.daemon_run_id)}`"])
        return "\n".join(lines)

    async def status_text_async(self, chat_id: int) -> str:
        if self.daemon_client is not None:
            try:
                payload = await self.daemon_client.current_model()
            except Exception:
                payload = {}
            if payload:
                self.config = _config_with_model_payload(self.config, payload)
        else:
            self.config = await self._with_openrouter_model_limits(self.config)
        return self.status_text(chat_id)

    async def usage_command_text(self, chat_id: int, argument: str) -> str:
        provider = argument.strip().lower()
        if self.daemon_client is None:
            base = self.status_text(chat_id)
            return base + "\n\nDetailed provider usage is available when Telegram is using the daemon."
        try:
            payload = await self.daemon_client.usage(provider=provider)
        except Exception as exc:
            return f"Could not load usage from daemon: {exc}"
        text = str(payload.get("text", "")).strip()
        if text:
            return text
        summary = _object_payload(payload.get("summary"))
        return "Usage:\n" + json.dumps(summary, indent=2, sort_keys=True)

    async def daemon_command_text(self, chat_id: int) -> str:
        state = self.state_for(chat_id)
        if self.daemon_client is None:
            return "Daemon: not connected. Run Telegram with `libre-claw telegram up` for daemon-backed runs."
        try:
            payload = await self.daemon_client.health()
        except Exception as exc:
            return f"Daemon: unreachable\nEndpoint: {self.daemon_client.base_url}\nError: {exc}"
        active_run = state.daemon_run_id or "none"
        return "\n".join(
            [
                "Daemon: online" if payload.get("ok") else "Daemon: unhealthy",
                f"Endpoint: {self.daemon_client.base_url}",
                f"Active runs: {payload.get('active_runs', 0)}",
                f"Current chat run: {active_run}",
                f"Telegram bridge: {payload.get('telegram_bridge', 'unknown')}",
            ]
        )

    async def shutdown_command_text(self) -> str:
        if self.daemon_client is None:
            return "Daemon: not connected. Nothing to shut down from Telegram."
        try:
            await self.daemon_client.shutdown()
        except Exception as exc:
            return f"Could not shut down daemon: {exc}"
        return "Shutdown requested. Libre Claw will stop if daemon mode is active."

    async def add_steering_note(self, chat_id: int, kind: str, text: str) -> str:
        note = text.strip()
        if not note:
            return f"Usage: /{kind} <note>"
        state = self.state_for(chat_id)
        label = "Side note" if kind == "btw" else "Steering instruction"
        state.session.summary = _append_session_note(state.session.summary, f"{label}: {note}")
        await self._archive_event(chat_id, "steering_note", {"kind": kind, "content": note})
        return f"{label} saved for future turns."

    async def runs_command_text(self, argument: str) -> str:
        limit = _telegram_list_limit(argument, default=10, maximum=25)
        try:
            if self.daemon_client is not None:
                payload = await self.daemon_client.list_runs(limit=limit)
                runs = [dict(item) for item in payload.get("runs", []) if isinstance(item, dict)]
            else:
                runs = [_run_record_payload(record) for record in await self.run_store.list_runs(limit=limit)]
        except Exception as exc:
            return f"Could not list runs: {exc}"
        if not runs:
            return "No runs yet."
        return "Recent runs:\n" + "\n".join(_run_line(run) for run in runs)

    async def run_command_text(self, argument: str) -> str:
        run_id = argument.strip()
        if not run_id:
            return "Usage: /run <run_id>"
        try:
            if self.daemon_client is not None:
                payload = await self.daemon_client.get_run(run_id)
                run = _object_payload(payload.get("run"))
                artifacts = _object_payload(payload.get("artifacts"))
            else:
                record = await self.run_store.load_run(run_id)
                if record is None:
                    return "Unknown run."
                run = _run_record_payload(record)
                artifacts = {}
        except Exception as exc:
            return f"Could not load run: {exc}"
        if not run:
            return "Unknown run."
        lines = [
            f"Run {_short_run_id(str(run.get('run_id', run_id)))}",
            f"State: {run.get('state', 'unknown')}",
            f"Title: {run.get('title', 'Untitled')}",
            f"Provider: {run.get('provider', '?')}:{run.get('model', '?')}",
            f"Updated: {run.get('updated_at', 'unknown')}",
        ]
        if run.get("path"):
            lines.append(f"Path: {run['path']}")
        available_artifacts = [
            name
            for name, meta in artifacts.items()
            if isinstance(meta, dict) and meta.get("exists")
        ]
        if available_artifacts:
            lines.append("Artifacts: " + ", ".join(sorted(available_artifacts)))
        return "\n".join(lines)

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
        fallbacks = create_fallback_providers(self.config)
        return Agent(
            session=state.session,
            provider=provider,
            tool_registry=create_builtin_registry(self.config, memory_store=self.memory_store),
            permission_manager=PermissionManager(self.config.permissions),
            system_prompt=self.config.agent.system_prompt,
            max_tool_calls_per_turn=self.config.agent.max_tool_calls_per_turn,
            auto_compact_threshold=self.config.agent.auto_compact_threshold,
            context_window_tokens=self.config.agent.context_window_tokens,
            provider_retry_attempts=self.config.agent.provider_retry_attempts,
            provider_retry_initial_delay=self.config.agent.provider_retry_initial_delay,
            memory_facts=self._memory_facts,
            system_prompt_extra=_combine_prompt_extra(self.config.agent.system_prompt_extra, TELEGRAM_SYSTEM_PROMPT_EXTRA),
            skill_provider=self.skill_store.relevant_skill_texts,
            soul_provider=self.soul_store.soul_texts,
            memory_provider=lambda user_message: self.relevant_memory_texts(user_message),
            fallback_providers=tuple((fallback.label, fallback.provider) for fallback in fallbacks),
            fallback_recheck_after_attempts=self.config.fallback.recheck_after_attempts,
        )

    async def _with_openrouter_model_limits(self, config: LibreClawConfig) -> LibreClawConfig:
        if config.general.default_provider.lower() != "openrouter":
            return config
        try:
            limits = await detect_openrouter_model_limits(config, model=config.general.default_model)
        except Exception:
            return config
        return apply_openrouter_model_limits(config, limits, model=config.general.default_model)

    async def relevant_memory_texts(self, user_message: str) -> list[str]:
        if not self._memory_enabled() or not self.config.memory.inject_relevant:
            return []
        items = await self.memory_store.search_memory_items(
            user_message,
            project_root=self.config.general.working_directory,
            limit=max(1, self.config.memory.max_injected_items),
        )
        return _memory_texts_with_budget(items, self.config.memory.max_injected_tokens)

    async def memory_command_text(self, chat_id: int, argument: str) -> str:
        parts = argument.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        value = parts[1].strip() if len(parts) > 1 else ""
        state = self.state_for(chat_id)

        if action == "status":
            status = await self.memory_store.memory_status()
            return (
                "Memory status:\n"
                f"enabled: {self._memory_enabled()}\n"
                f"active items: {status['active']}\n"
                f"disabled items: {status['disabled']}\n"
                f"session archives: {status['session_archives']}"
            )
        if action == "on":
            self.memory_enabled = True
            return "Persistent memory enabled for Telegram."
        if action == "off":
            self.memory_enabled = False
            return "Persistent memory disabled for Telegram."
        if action == "list":
            items = await self.memory_store.list_memory_items(limit=50)
            return _memory_items_text(items) if items else "No active memories stored."
        if action == "search":
            if not value:
                return "Usage: /memory search <query>"
            items = await self.memory_store.search_memory_items(
                value,
                project_root=self.config.general.working_directory,
                limit=20,
            )
            return _memory_items_text(items) if items else "No matching memories."
        if action == "add":
            if not value:
                return "Usage: /memory add <memory>"
            item = await self.memory_store.add_memory_item(
                text=value,
                kind="fact",
                scope="global",
                source_type="manual",
                project_root=self.config.general.working_directory,
            )
            await self.initialize()
            return f"Added memory {item.id}."
        if action == "forget":
            if not value.isdigit():
                return "Usage: /memory forget <id>"
            removed = await self.memory_store.forget_memory_item(int(value))
            await self.initialize()
            return "Memory forgotten." if removed else f"No active memory with id {value}."
        if action == "summarize":
            summary = summarize_session_for_memory(state.session)
            if not summary:
                return "No Telegram session content to summarize into memory."
            item = await self.memory_store.add_memory_item(
                kind="summary",
                scope="session",
                text=summary,
                source_type="session",
                source_id=f"{state.archive_id}:summary",
                project_root=self.config.general.working_directory,
            )
            return f"Session summary saved as memory {item.id}."
        if action == "import-runs":
            count = await self._import_run_memories()
            return f"Imported {count} run summary memory item(s)."
        return "Usage: /memory status|on|off|list|search <query>|add <text>|forget <id>|summarize|import-runs"

    async def _archive_event(self, chat_id: int, event_type: str, data: dict[str, Any]) -> None:
        if not self.config.memory.archive_sessions:
            return
        try:
            await self.memory_store.append_session_event(self.state_for(chat_id).archive_id, event_type, data)
        except Exception:
            return

    async def _extract_turn_memory(self, chat_id: int, user_message: str, assistant_text: str) -> None:
        if not self._memory_enabled() or not assistant_text.strip():
            return
        source_id = f"{self.state_for(chat_id).archive_id}:turn:{uuid4().hex}"
        if self.config.memory.auto_summarize:
            try:
                await self.memory_store.add_memory_item(
                    kind="summary",
                    scope="session",
                    text=_memory_summary_text(user_message, assistant_text),
                    source_type="telegram",
                    source_id=source_id,
                    project_root=self.config.general.working_directory,
                )
            except Exception:
                pass
        if not self.config.memory.auto_extract:
            return
        try:
            provider = create_provider(self.config)
            existing = [item.text for item in await self.memory_store.search_memory_items(user_message, project_root=self.config.general.working_directory, limit=8)]
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
                    source_type="telegram",
                    source_id=f"{source_id}:memory:{index}",
                    project_root=self.config.general.working_directory if memory.scope == "project" else "",
                )
        except Exception:
            return

    async def _import_run_memories(self) -> int:
        count = 0
        runs = await self.run_store.list_runs(limit=200)
        for run in runs:
            summary_path = run.path / "summary.md"
            if not summary_path.exists():
                continue
            summary = redact_secrets(await asyncio.to_thread(summary_path.read_text, encoding="utf-8")).strip()
            if not summary:
                continue
            await self.memory_store.add_memory_item(
                kind="summary",
                scope="project",
                text=_memory_summary_text(run.title, summary),
                source_type="run",
                source_id=f"{run.run_id}:summary",
                project_root=run.working_directory or self.config.general.working_directory,
            )
            count += 1
        return count

    def _memory_enabled(self) -> bool:
        return self.memory_enabled and self.config.memory.enabled

    async def _stream_daemon_message(
        self,
        chat_id: int,
        text: str,
        attachments: tuple[UserAttachment, ...] = (),
    ):
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
                attachments=[attachment.as_payload() for attachment in attachments],
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
        event_cursor = 0
        assistant_chunks: list[str] = []
        yielded_done = False
        yield TelegramToolNotice(f"🚀 Run {_short_run_id(run_id)} started.")

        while True:
            if state.daemon_run_id != run_id:
                return
            try:
                payload = await self.daemon_client.get_events(run_id, after=event_cursor)
            except Exception as exc:
                yield TelegramError(f"Daemon event polling failed: {exc}")
                return

            events = payload.get("events", [])
            if not isinstance(events, list):
                events = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_cursor = max(event_cursor, int(event.get("event_id", 0) or 0))
                if state.daemon_run_id == run_id:
                    state.daemon_event_id = event_cursor
                data = _object_payload(event.get("data"))
                await self._archive_event(chat_id, f"daemon_{event.get('type', 'event')}", data)
                if str(event.get("type", "")) == "usage":
                    usage = _usage_from_payload(data)
                    state.usage = combine_usage(state.usage, usage) or state.usage
                    state.last_usage = usage
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
                    state.session.add_user_message(text, attachments=attachments)
                    assistant_text = "".join(assistant_chunks)
                    if assistant_text:
                        state.session.add_assistant_message(assistant_text)
                        await self._archive_event(chat_id, "assistant_message", {"content": assistant_text, "run_id": run_id})
                if not yielded_done:
                    yield TelegramDone(None)
                if state.daemon_run_id == run_id:
                    state.daemon_run_id = None
                    state.daemon_event_id = 0
                return
            await asyncio.sleep(max(0.1, self.config.daemon.poll_interval))


def _format_usage_cost(usage: Usage) -> str:
    if usage.cost is None or usage.cost == 0:
        return "$0.00"
    if usage.cost < 0.01:
        return f"${usage.cost:.6f}"
    return f"${usage.cost:.2f}"


def _telegram_context_meter(
    config: LibreClawConfig,
    state: TelegramChatState,
    soul_store: SoulStore,
    memory_facts: list[str],
) -> TelegramContextMeter:
    extra_texts = tuple(
        text
        for text in (
            config.agent.system_prompt,
            config.agent.system_prompt_extra,
            TELEGRAM_SYSTEM_PROMPT_EXTRA,
            *soul_store.soul_texts(),
            *memory_facts,
        )
        if text
    )
    estimated_tokens = estimate_context_tokens(
        state.session.messages,
        summary=state.session.summary,
        extra_texts=extra_texts,
    )
    if state.last_usage.input_tokens:
        used_tokens = max(estimated_tokens, state.last_usage.input_tokens)
        source = "last provider input"
    else:
        used_tokens = estimated_tokens
        source = "estimated"
    context_window = max(1, config.agent.context_window_tokens)
    return TelegramContextMeter(
        used_tokens=used_tokens,
        context_window_tokens=context_window,
        ratio=used_tokens / context_window,
        source=source,
    )


def _context_bar(meter: TelegramContextMeter, width: int = 10) -> str:
    filled = max(0, min(width, int(round(min(meter.ratio, 1.0) * width))))
    if meter.used_tokens > 0 and filled == 0:
        filled = 1
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_token_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(value)


def _canonical_provider(provider: str) -> str:
    cleaned = provider.strip().lower()
    return "ollama" if cleaned == "local" else cleaned


def _provider_default_model(config: LibreClawConfig, provider: str) -> str:
    provider_config = config.providers.get(provider)
    if isinstance(provider_config, dict):
        model = str(provider_config.get("default_model", "")).strip()
        if model:
            return model
    return config.general.default_model


def _config_with_model_payload(config: LibreClawConfig, payload: Mapping[str, Any]) -> LibreClawConfig:
    provider = _canonical_provider(str(payload.get("provider") or config.general.default_provider))
    model = str(payload.get("model") or config.general.default_model)
    general = replace(config.general, default_provider=provider, default_model=model)
    telegram = replace(config.telegram, default_provider=provider, default_model=model)
    providers: dict[str, Mapping[str, Any]] = {}
    for name, value in config.providers.items():
        providers[name] = dict(value) if isinstance(value, Mapping) else value
    openrouter_config = dict(providers.get("openrouter", {}))
    for key in (
        "detected_context_window_tokens",
        "detected_max_completion_tokens",
        "detected_context_source",
        "detected_context_model",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            openrouter_config[key] = value
    providers["openrouter"] = openrouter_config
    agent = config.agent
    context_window = _positive_int(payload.get("detected_context_window_tokens")) or _positive_int(
        payload.get("context_window_tokens")
    )
    if provider == "openrouter" and context_window is not None:
        agent = replace(agent, context_window_tokens=context_window)
    return replace(config, general=general, telegram=telegram, agent=agent, providers=providers)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        integer = int(value)
        return integer if integer > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        integer = int(value)
        return integer if integer > 0 else None
    return None


def _combine_prompt_extra(existing: str, addition: str) -> str:
    parts = [part.strip() for part in (existing, addition) if part.strip()]
    return "\n\n".join(parts)


def _tool_call_notice(name: str, arguments: dict[str, Any]) -> str:
    if name == "http_request":
        return _http_request_call_notice(arguments)
    if name == "web_search":
        query = str(arguments.get("query", "")).strip()
        return f"🔎 Search {_compact_text(query, 320)}" if query else "🔎 web_search"
    summary = _arguments_summary(arguments, limit=TELEGRAM_ARGUMENT_LIMIT)
    if not summary:
        return f"🔧 {name}"
    return f"🔧 {name}\n{summary}"


def _permission_notice(name: str, arguments: dict[str, Any], *, run_id: str = "") -> str:
    lines = [f"🔐 Approve {name}?"]
    if run_id:
        lines.append(f"run: {_short_run_id(run_id)}")
    summary = _arguments_summary(arguments, limit=TELEGRAM_ARGUMENT_LIMIT)
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def _tool_result_notice(
    name: str,
    *,
    is_error: bool,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    if name == "http_request":
        return _http_request_result_notice(is_error=is_error, content=content, metadata=metadata or {})
    if name == "web_search":
        return _web_search_result_notice(is_error=is_error, content=content, metadata=metadata or {})
    icon = "⚠️" if is_error else "✅"
    status = "failed" if is_error else "done"
    compact = _compact_text(content, TELEGRAM_NOTICE_LIMIT)
    if not compact:
        return f"{icon} {name} {status}"
    return f"{icon} {name} {status}\n{compact}"


def _http_request_call_notice(arguments: dict[str, Any]) -> str:
    method = str(arguments.get("method", "GET") or "GET").upper()
    url = str(arguments.get("url", "")).strip()
    if not url:
        return f"🌐 {method} http_request"
    return f"🌐 {method} {_compact_text(url, 320)}"


def _http_request_result_notice(*, is_error: bool, content: str, metadata: dict[str, Any]) -> str:
    if is_error:
        compact = _compact_text(content, TELEGRAM_HTTP_ERROR_LIMIT)
        return f"⚠️ http_request failed\n{compact}" if compact else "⚠️ http_request failed"

    lines = ["✅ http_request done"]
    method = str(metadata.get("method", "")).strip()
    url = str(metadata.get("url", "") or metadata.get("requested_url", "")).strip()
    if method or url:
        request_line = " ".join(part for part in (method, url) if part).strip()
        lines.append(_compact_text(request_line, 360))
    status_code = metadata.get("status_code")
    if status_code not in (None, ""):
        lines.append(f"status: {status_code}")
    content_type = str(metadata.get("content_type", "")).strip()
    if content_type:
        lines.append(f"content_type: {_compact_text(content_type, 160)}")
    byte_count = metadata.get("bytes")
    if byte_count not in (None, ""):
        lines.append(f"bytes: {byte_count}")
    saved_path = str(metadata.get("saved_path", "")).strip()
    if saved_path:
        lines.append(f"saved: {_compact_text(saved_path, 220)}")
    response_body = content.split("\n\n", 1)[1].strip() if "\n\n" in content else ""
    if metadata.get("truncated") or response_body:
        lines.append("body: hidden in Telegram preview, available to the model")
    if len(lines) > 1:
        return "\n".join(lines)

    header = content.split("\n\n", 1)[0].strip()
    if header:
        return "✅ http_request done\n" + _compact_text(header, TELEGRAM_HTTP_ERROR_LIMIT)
    return "✅ http_request done"


def _web_search_result_notice(*, is_error: bool, content: str, metadata: dict[str, Any]) -> str:
    if is_error:
        compact = _compact_text(content, TELEGRAM_HTTP_ERROR_LIMIT)
        return f"⚠️ web_search failed\n{compact}" if compact else "⚠️ web_search failed"
    query = str(metadata.get("query", "")).strip()
    count = metadata.get("returned_results")
    result_text = f"{count} result(s)" if count not in (None, "") else "done"
    if query:
        return f"✅ web_search {result_text}\n{_compact_text(query, 320)}"
    return f"✅ web_search {result_text}"


def _arguments_summary(arguments: dict[str, Any], *, limit: int) -> str:
    if not arguments:
        return ""
    preferred = ("url", "path", "command", "query", "pattern", "selector", "text")
    keys = [key for key in preferred if key in arguments]
    keys.extend(key for key in arguments if key not in keys)
    lines: list[str] = []
    remaining = limit
    for key in keys:
        if remaining <= 0:
            break
        value = _argument_value_text(arguments[key])
        line = f"{key}: {value}"
        if len(line) > remaining:
            line = _compact_text(line, remaining)
        lines.append(line)
        remaining -= len(line) + 1
    return "\n".join(lines)


def _argument_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _compact_text(text: str, limit: int = TELEGRAM_NOTICE_LIMIT) -> str:
    cleaned = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 18)].rstrip() + "\n… truncated"


def _short_run_id(run_id: str) -> str:
    if len(run_id) <= 18:
        return run_id
    return f"{run_id[:8]}…{run_id[-8:]}"


async def _telegram_events_from_daemon_event(run_id: str, event: dict[str, Any]):
    data = _object_payload(event.get("data"))
    event_type = str(event.get("type", ""))
    if event_type == "assistant_delta":
        yield TelegramText(str(data.get("text", "")))
        return
    if event_type == "tool_call":
        name = str(data.get("name", "tool"))
        yield TelegramToolNotice(
            _tool_call_notice(name, _object_payload(data.get("arguments"))),
            tool_name=name,
        )
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
            text=_permission_notice(call.name, _object_payload(call.arguments), run_id=run_id),
        )
        return
    if event_type == "tool_result":
        name = str(data.get("name", "tool"))
        is_error = bool(data.get("is_error"))
        yield TelegramToolNotice(
            _tool_result_notice(
                name,
                is_error=is_error,
                content=str(data.get("content", "")),
                metadata=_object_payload(data.get("metadata")),
            ),
            tool_name=name,
            is_error=is_error,
            is_result=True,
        )
        return
    if event_type == "error":
        yield TelegramError("⚠️ " + _compact_text(str(data.get("message", "Daemon run failed."))))
        return
    if event_type == "run_finished":
        yield TelegramDone(None)


def _object_payload(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _append_session_note(summary: str | None, note: str, *, limit: int = 4000) -> str:
    lines = [line for line in (summary or "").splitlines() if line.strip()]
    lines.append("User steering: " + note.strip())
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[-limit:].lstrip()


def _telegram_list_limit(argument: str, *, default: int, maximum: int) -> int:
    cleaned = argument.strip()
    if not cleaned:
        return default
    first = cleaned.split(maxsplit=1)[0]
    try:
        value = int(first)
    except ValueError:
        return default
    return max(1, min(maximum, value))


def _run_line(run: dict[str, Any]) -> str:
    run_id = str(run.get("run_id", ""))
    title = str(run.get("title", "Untitled")).strip() or "Untitled"
    provider = str(run.get("provider", "?"))
    model = str(run.get("model", "?"))
    state = str(run.get("state", "unknown"))
    return f"{_short_run_id(run_id)} [{state}] {provider}:{model} - {_compact_text(title, 90)}"


def _run_record_payload(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "state": record.state,
        "title": record.title,
        "kind": record.kind,
        "provider": record.provider,
        "model": record.model,
        "working_directory": record.working_directory,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "path": str(record.path),
    }


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


def _latest_assistant_text(session: Session) -> str:
    for message in reversed(session.messages):
        if message.role != "assistant":
            continue
        chunks = [
            str(block.get("text", ""))
            for block in message.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(chunk for chunk in chunks if chunk).strip()
    return ""


def _memory_items_text(items: list[MemoryItem]) -> str:
    return "\n".join(f"{item.id}: [{item.kind}/{item.scope}] {item.text}" for item in items)


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


def _attachment_metadata(attachment: UserAttachment) -> dict[str, str]:
    metadata = {"media_type": attachment.media_type}
    if attachment.filename:
        metadata["filename"] = attachment.filename
    if attachment.path:
        metadata["path"] = attachment.path
    return metadata
