# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from libre_claw.config import ConfigError, global_config_path, set_global_default_model
from libre_claw.core.automations import AutomationError
from libre_claw.core.heartbeat import HeartbeatError, heartbeat_prompt, parse_heartbeat_interval
from libre_claw.core.permissions import PermissionResolution
from libre_claw.providers.anthropic_catalog import ANTHROPIC_MODEL_PRESETS
from libre_claw.providers.codex_catalog import CODEX_MODEL_PRESETS
from libre_claw.providers.ollama_catalog import OLLAMA_MODEL_PRESETS
from libre_claw.providers.openrouter_catalog import OPENROUTER_MODEL_PRESETS
from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramError,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
)
from libre_claw.telegram.formatting import (
    TELEGRAM_HARD_MESSAGE_LIMIT,
    TELEGRAM_SAFE_MESSAGE_LIMIT,
    TelegramFormattedChunk,
    markdown_to_telegram_html,
    plain_text_chunks,
    telegram_html_chunks,
    telegram_message_limit,
)

TELEGRAM_CONTINUED_SUFFIX = "\n\n...[continued]"
TELEGRAM_TYPING_INTERVAL_SECONDS = 4.0
TELEGRAM_TOOL_LOG_UPDATE_INTERVAL_SECONDS = 8.0
TELEGRAM_TOOL_LOG_UPDATE_EVENT_INTERVAL = 12
PERMISSION_CALLBACKS: dict[str, tuple[PermissionResolution, str, str]] = {
    "p:y:": ("allow_once", "Approved.", "✅ Approved"),
    "p:t:": (
        "always_allow_tool",
        "Always allowing this tool for this run.",
        "✅ Always allowing this tool for this run",
    ),
    "p:c:": (
        "always_allow_call",
        "Always allowing this exact call for this run.",
        "✅ Always allowing this exact call for this run",
    ),
    "p:n:": ("deny", "Denied.", "✖️ Denied"),
}


@dataclass(frozen=True)
class TelegramModelPreset:
    provider: str
    model: str
    label: str


@dataclass(frozen=True)
class TelegramExpandablePayload:
    title: str
    text: str
    clean_final: bool = False
    render_html: bool = True


TELEGRAM_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI API",
    "openrouter": "OpenRouter",
    "ollama": "Ollama Cloud/Local",
    "codex": "OpenAI Codex",
}

TELEGRAM_MODEL_PRESETS: dict[str, tuple[TelegramModelPreset, ...]] = {
    "ollama": tuple(TelegramModelPreset("ollama", preset.model, preset.label) for preset in OLLAMA_MODEL_PRESETS),
    "openrouter": (
        *(TelegramModelPreset("openrouter", preset.model, preset.label) for preset in OPENROUTER_MODEL_PRESETS),
    ),
    "openai": (
        TelegramModelPreset("openai", "gpt-5.5", "GPT-5.5"),
        TelegramModelPreset("openai", "gpt-4o", "GPT-4o"),
        TelegramModelPreset("openai", "gpt-4.1", "GPT-4.1"),
        TelegramModelPreset("openai", "o3", "o3"),
        TelegramModelPreset("openai", "o4-mini", "o4-mini"),
        TelegramModelPreset("openai", "codex-mini", "Codex Mini"),
    ),
    "codex": tuple(TelegramModelPreset("codex", preset.model, preset.label) for preset in CODEX_MODEL_PRESETS),
    "anthropic": tuple(
        TelegramModelPreset("anthropic", preset.model, preset.label) for preset in ANTHROPIC_MODEL_PRESETS
    ),
}

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.error import BadRequest
    from telegram.ext import ContextTypes
except ImportError:  # pragma: no cover - dependency availability is checked in integration.
    InlineKeyboardButton = InlineKeyboardMarkup = Update = ContextTypes = None  # type: ignore[assignment]
    BadRequest = None  # type: ignore[assignment]


class TelegramHandlers:
    def __init__(self, bridge: TelegramBridge, auth: TelegramAuth) -> None:
        self.bridge = bridge
        self.auth = auth
        self._heartbeat_tasks: dict[int, asyncio.Task[None]] = {}
        self._heartbeat_intervals: dict[int, int] = {}
        self._permission_callback_ids: dict[str, str] = {}
        self._permission_callback_counter = 0
        self._expand_callback_ids: dict[str, TelegramExpandablePayload] = {}
        self._expand_callback_counter = 0

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text("Libre Claw is ready.")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await _reply_text_chunks(update.effective_message, _telegram_help_text(), self.bridge.config.telegram.max_message_length)

    async def new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        self.bridge.new_session(update.effective_chat.id)
        await update.effective_message.reply_text("Started a new Libre Claw session.")

    async def cost(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await _reply_text_chunks(
            update.effective_message,
            self.bridge.status_text(update.effective_chat.id),
            self.bridge.config.telegram.max_message_length,
        )

    async def usage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.usage_command_text(update.effective_chat.id, text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def daemon(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        response = await self.bridge.daemon_command_text(update.effective_chat.id)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def runs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.runs_command_text(text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def run(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.run_command_text(text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def compact(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        state = self.bridge.state_for(update.effective_chat.id)
        before = len(state.session.messages)
        state.session.compact(keep_last=8)
        await update.effective_message.reply_text(f"Compacted context from {before} to {len(state.session.messages)} messages.")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        cancelled = await self.bridge.cancel_async(update.effective_chat.id)
        await update.effective_message.reply_text("Cancelled." if cancelled else "No active generation.")

    async def shutdown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if self.bridge.daemon_client is not None:
            await update.effective_message.reply_text("Shutdown requested. Libre Claw will stop if daemon mode is active.")
            await self.bridge.shutdown_command_text()
            return
        response = await self.bridge.shutdown_command_text()
        await update.effective_message.reply_text(response)

    async def btw(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.add_steering_note(update.effective_chat.id, "btw", text)
        await update.effective_message.reply_text(response)

    async def steer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.add_steering_note(update.effective_chat.id, "steer", text)
        await update.effective_message.reply_text(response)

    async def model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        model = " ".join(context.args or [])
        if not model:
            await update.effective_message.reply_text(
                _model_configuration_text(self.bridge.config),
                reply_markup=_provider_keyboard(self.bridge.config),
            )
            return
        provider, selected_model, persist_global = _parse_telegram_model_argument(
            model,
            self.bridge.config.general.default_provider,
        )
        if not selected_model:
            await update.effective_message.reply_text("Usage: /model <provider>:<name> [--global]")
            return
        self.bridge.config = _replace_general(self.bridge.config, default_provider=provider, default_model=selected_model)
        response = f"Model set to {provider}:{selected_model}."
        daemon_note = await self._sync_daemon_model(provider, selected_model, persist_global=persist_global)
        if persist_global:
            try:
                path = set_global_default_model(
                    provider,
                    selected_model,
                    config_path=global_config_path(self.bridge.config),
                )
                response += f"\nSaved as global default in {path}."
                if self.bridge.daemon_client is None:
                    automations_updated = await self.bridge.automation_store.update_global_model(provider, selected_model)
                    if automations_updated:
                        response += f"\nUpdated {automations_updated} scheduled automation(s)."
            except ConfigError as exc:
                response += f"\nModel set for this Telegram session, but global config was not updated: {exc}"
            except AutomationError as exc:
                response += f"\nGlobal config was updated, but scheduled automations were not: {exc}"
        elif self.bridge.daemon_client is None:
            response += "\nTelegram session only. Add --global to update the TUI/default config."
        if daemon_note:
            response += f"\n{daemon_note}"
        await update.effective_message.reply_text(response)

    async def provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        provider = " ".join(context.args or [])
        if not provider:
            await update.effective_message.reply_text(
                _model_configuration_text(self.bridge.config),
                reply_markup=_provider_keyboard(self.bridge.config),
            )
            return
        provider = _canonical_telegram_provider(provider)
        if provider not in TELEGRAM_PROVIDER_LABELS:
            await update.effective_message.reply_text(_provider_usage_text())
            return
        self.bridge.config = _replace_general(self.bridge.config, default_provider=provider)
        await update.effective_message.reply_text(f"Provider set to {provider}.")

    async def schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.schedule_text(update.effective_chat.id, text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.memory_command_text(update.effective_chat.id, text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def heartbeat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat_id = update.effective_chat.id
        text = " ".join(context.args or [])
        parts = text.split(maxsplit=1)
        action = parts[0].lower() if parts else "status"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in {"status", ""}:
            active = chat_id in self._heartbeat_tasks and not self._heartbeat_tasks[chat_id].done()
            interval = self._heartbeat_intervals.get(chat_id, self.bridge.config.heartbeat.interval_minutes)
            await update.effective_message.reply_text(
                f"Heartbeat active: {active}\n"
                f"Interval: every {interval} minutes\n"
                "Use /heartbeat once, /heartbeat start every 30 minutes, or /heartbeat stop."
            )
            return

        if action in {"once", "run", "now"}:
            await update.effective_message.reply_text("Heartbeat check started.")
            await self._run_telegram_heartbeat_once(context, chat_id)
            return

        if action in {"start", "on", "every"}:
            interval_text = rest if action != "every" else text
            try:
                minutes = parse_heartbeat_interval(interval_text, self.bridge.config.heartbeat.interval_minutes)
            except HeartbeatError as exc:
                await update.effective_message.reply_text(str(exc))
                return
            self._start_telegram_heartbeat(context, chat_id, minutes)
            await update.effective_message.reply_text(f"Heartbeat started: every {minutes} minutes.")
            return

        if action in {"stop", "off", "pause"}:
            task = self._heartbeat_tasks.pop(chat_id, None)
            if task is not None and not task.done():
                task.cancel()
            self._heartbeat_intervals.pop(chat_id, None)
            await update.effective_message.reply_text("Heartbeat stopped.")
            return

        await update.effective_message.reply_text("Usage: /heartbeat status|once|start [every 30 minutes|1h]|stop")

    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = update.effective_message.text or ""
        if text.strip() == "/":
            await _reply_text_chunks(update.effective_message, _telegram_help_text(), self.bridge.config.telegram.max_message_length)
            return
        chat_id = update.effective_chat.id
        state = self.bridge.state_for(chat_id)
        if state.task is not None and not state.task.done():
            await self.bridge.cancel_async(chat_id)
            await update.effective_message.reply_text("↻ Previous run cancelled. Starting the new message.")
        placeholder = await update.effective_message.reply_text("Libre Claw is thinking...")
        accumulated = ""
        last_update = time.monotonic()
        saw_tool_notice = False
        tool_log_message: Any | None = None
        tool_notices: list[str] = []
        tool_event_count = 0
        http_started = 0
        http_done = 0
        tool_log_dirty = False
        tool_log_last_update = 0.0
        typing_task: asyncio.Task[None] | None = self._start_typing_indicator(context.bot, chat_id)

        async def stop_typing() -> None:
            nonlocal typing_task
            if typing_task is not None:
                await _cancel_task(typing_task)
                typing_task = None

        async def runner() -> None:
            nonlocal accumulated, http_done, http_started, last_update, saw_tool_notice, tool_event_count, tool_log_dirty, tool_log_last_update, tool_log_message
            try:
                async for event in self.bridge.stream_message(chat_id, text):
                    if isinstance(event, TelegramText):
                        accumulated += event.text
                        if saw_tool_notice:
                            continue
                        should_update = len(accumulated) % 100 == 0 or time.monotonic() - last_update >= self.bridge.config.telegram.stream_update_interval
                        if should_update:
                            await self._edit_expandable_preview(
                                placeholder,
                                accumulated,
                                title="Full response",
                                clean_final=True,
                            )
                            last_update = time.monotonic()
                        continue
                    if isinstance(event, TelegramToolNotice):
                        saw_tool_notice = True
                        accumulated = ""
                        tool_event_count += 1
                        if event.tool_name == "http_request" and not event.is_error:
                            if event.is_result:
                                http_done += 1
                            else:
                                http_started += 1
                        else:
                            tool_notices.append(event.text)
                        if tool_log_message is None:
                            await _edit_text_preview(
                                placeholder,
                                "🧰 Working through tools. Final answer will arrive below.",
                                self.bridge.config.telegram.max_message_length,
                            )
                            tool_log_message = await _reply_tool_log_preview(
                                update.effective_message,
                                _tool_log_preview(
                                    _visible_tool_notices(tool_notices, http_started, http_done),
                                    self.bridge.config.telegram.max_message_length,
                                    total_count=_tool_activity_count(tool_notices, http_started, http_done),
                                ),
                                self.bridge.config.telegram.max_message_length,
                                reply_markup=self._tool_log_expand_markup(tool_notices, http_started, http_done),
                            )
                            tool_log_last_update = time.monotonic()
                        else:
                            tool_log_dirty = True
                            now = time.monotonic()
                            should_update_tool_log = (
                                now - tool_log_last_update >= TELEGRAM_TOOL_LOG_UPDATE_INTERVAL_SECONDS
                                or tool_event_count % TELEGRAM_TOOL_LOG_UPDATE_EVENT_INTERVAL == 0
                            )
                            if should_update_tool_log:
                                await _safe_edit_tool_log_preview(
                                    tool_log_message,
                                    _tool_log_preview(
                                        _visible_tool_notices(tool_notices, http_started, http_done),
                                        self.bridge.config.telegram.max_message_length,
                                        total_count=_tool_activity_count(tool_notices, http_started, http_done),
                                    ),
                                    self.bridge.config.telegram.max_message_length,
                                    reply_markup=self._tool_log_expand_markup(tool_notices, http_started, http_done),
                                )
                                tool_log_last_update = now
                                tool_log_dirty = False
                        continue
                    if isinstance(event, TelegramPermissionPrompt):
                        await stop_typing()
                        await self._reply_permission_prompt(update.effective_message, event)
                        continue
                    if isinstance(event, TelegramDone):
                        await stop_typing()
                        if tool_log_message is not None and tool_log_dirty:
                            await _safe_edit_tool_log_preview(
                                tool_log_message,
                                _tool_log_preview(
                                    _visible_tool_notices(tool_notices, http_started, http_done),
                                    self.bridge.config.telegram.max_message_length,
                                    total_count=_tool_activity_count(tool_notices, http_started, http_done),
                                ),
                                self.bridge.config.telegram.max_message_length,
                                reply_markup=self._tool_log_expand_markup(tool_notices, http_started, http_done),
                            )
                            tool_log_dirty = False
                        if accumulated:
                            if saw_tool_notice:
                                await _edit_text_preview(
                                    placeholder,
                                    "✅ Run complete. Final answer below.",
                                    self.bridge.config.telegram.max_message_length,
                                )
                                await _reply_text_chunks(
                                    update.effective_message,
                                    accumulated,
                                    self.bridge.config.telegram.max_message_length,
                                    clean_final=True,
                                )
                            else:
                                await _finish_text_response(
                                    placeholder,
                                    update.effective_message,
                                    accumulated,
                                    self.bridge.config.telegram.max_message_length,
                                )
                        else:
                            await _edit_text_preview(placeholder, "Done.", self.bridge.config.telegram.max_message_length)
                        continue
                    if isinstance(event, TelegramError):
                        await stop_typing()
                        if tool_log_message is not None and tool_log_dirty:
                            await _safe_edit_tool_log_preview(
                                tool_log_message,
                                _tool_log_preview(
                                    _visible_tool_notices(tool_notices, http_started, http_done),
                                    self.bridge.config.telegram.max_message_length,
                                    total_count=_tool_activity_count(tool_notices, http_started, http_done),
                                ),
                                self.bridge.config.telegram.max_message_length,
                                reply_markup=self._tool_log_expand_markup(tool_notices, http_started, http_done),
                            )
                            tool_log_dirty = False
                        if saw_tool_notice:
                            await _edit_text_preview(
                                placeholder,
                                "⚠️ Run stopped. Error below.",
                                self.bridge.config.telegram.max_message_length,
                            )
                            await _reply_text_chunks(
                                update.effective_message,
                                event.text,
                                self.bridge.config.telegram.max_message_length,
                            )
                        else:
                            await _finish_text_response(
                                placeholder,
                                update.effective_message,
                                event.text,
                                self.bridge.config.telegram.max_message_length,
                            )
                        continue
            except Exception as exc:
                await stop_typing()
                await _finish_text_response(
                    placeholder,
                    update.effective_message,
                    f"Telegram bridge error: {exc}",
                    self.bridge.config.telegram.max_message_length,
                )
            finally:
                await stop_typing()

        task = asyncio.create_task(runner())
        self.bridge.state_for(chat_id).task = task

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = update.effective_message.text or "that command"
        command = text.split(maxsplit=1)[0]
        await _reply_text_chunks(
            update.effective_message,
            f"Unknown Telegram command: {command}\n\n{_telegram_help_text()}",
            self.bridge.config.telegram.max_message_length,
        )

    def _start_telegram_heartbeat(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, interval_minutes: int) -> None:
        existing = self._heartbeat_tasks.get(chat_id)
        if existing is not None and not existing.done():
            existing.cancel()
        self._heartbeat_intervals[chat_id] = max(1, interval_minutes)
        self._heartbeat_tasks[chat_id] = asyncio.create_task(
            self._telegram_heartbeat_loop(context, chat_id),
            name=f"libre-claw-telegram-heartbeat-{chat_id}",
        )

    async def _telegram_heartbeat_loop(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_intervals.get(chat_id, 60) * 60)
                await context.bot.send_message(chat_id=chat_id, text="Heartbeat check started.")
                await self._run_telegram_heartbeat_once(context, chat_id)
        except asyncio.CancelledError:
            raise

    async def _run_telegram_heartbeat_once(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        prompt = heartbeat_prompt(self.bridge.config, surface="telegram")
        accumulated = ""
        typing_task: asyncio.Task[None] | None = self._start_typing_indicator(context.bot, chat_id)

        async def stop_typing() -> None:
            nonlocal typing_task
            if typing_task is not None:
                await _cancel_task(typing_task)
                typing_task = None

        try:
            async for event in self.bridge.stream_message(chat_id, prompt):
                if isinstance(event, TelegramText):
                    accumulated += event.text
                    continue
                if isinstance(event, TelegramToolNotice):
                    await _send_text_chunks(context.bot, chat_id, event.text, self.bridge.config.telegram.max_message_length)
                    continue
                if isinstance(event, TelegramPermissionPrompt):
                    await stop_typing()
                    await self._send_permission_prompt(context.bot, chat_id, event)
                    continue
                if isinstance(event, TelegramDone):
                    await stop_typing()
                    await _send_text_chunks(
                        context.bot,
                        chat_id,
                        accumulated or "Heartbeat done.",
                        self.bridge.config.telegram.max_message_length,
                        clean_final=True,
                    )
                    continue
                if isinstance(event, TelegramError):
                    await stop_typing()
                    await _send_text_chunks(context.bot, chat_id, event.text, self.bridge.config.telegram.max_message_length)
                    return
        finally:
            await stop_typing()

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not self.auth.is_allowed(query.from_user.id if query.from_user else None):
            await query.answer("Not authorized.", show_alert=True)
            return
        data = query.data or ""
        for prefix, (resolution, answer_text, edit_text) in PERMISSION_CALLBACKS.items():
            if data.startswith(prefix):
                prompt_id = self._permission_callback_ids.pop(data.removeprefix(prefix), "")
                resolved = await self.bridge.resolve_permission_async(prompt_id, resolution)
                await query.answer(answer_text if resolved else "Prompt expired.")
                if resolved:
                    with suppress(Exception):
                        await query.edit_message_text(edit_text)
                return
        if data.startswith("x:"):
            payload = self._expand_callback_ids.get(data.removeprefix("x:"))
            if payload is None:
                await query.answer("Expanded content expired.", show_alert=True)
                return
            await query.answer("Showing full content.")
            with suppress(Exception):
                await query.edit_message_reply_markup(reply_markup=None)
            message = getattr(query, "message", None)
            if message is not None:
                await _reply_text_chunks(
                    message,
                    f"{payload.title}\n\n{payload.text}",
                    self.bridge.config.telegram.max_message_length,
                    clean_final=payload.clean_final,
                    render_html=payload.render_html,
                )
            return
        if data == "cfg:cancel":
            await query.answer("Cancelled.")
            await query.edit_message_text("Model configuration cancelled.")
            return
        if data == "cfg:providers":
            await query.answer("Providers")
            await query.edit_message_text(
                _model_configuration_text(self.bridge.config),
                reply_markup=_provider_keyboard(self.bridge.config),
            )
            return
        if data.startswith("cfg:provider:"):
            provider = _canonical_telegram_provider(data.removeprefix("cfg:provider:"))
            if provider not in TELEGRAM_PROVIDER_LABELS:
                await query.answer("Unknown provider.", show_alert=True)
                return
            await query.answer(TELEGRAM_PROVIDER_LABELS[provider])
            await query.edit_message_text(
                _provider_model_text(self.bridge.config, provider),
                reply_markup=_model_keyboard(self.bridge.config, provider),
            )
            return
        if data.startswith("cfg:model:"):
            parts = data.split(":")
            if len(parts) != 4:
                await query.answer("Invalid model selection.", show_alert=True)
                return
            _, _, provider, index_text = parts
            provider = _canonical_telegram_provider(provider)
            preset = _model_preset_at(provider, index_text)
            if preset is None:
                await query.answer("Unknown model.", show_alert=True)
                return
            self.bridge.config = _replace_general(
                self.bridge.config,
                default_provider=preset.provider,
                default_model=preset.model,
            )
            daemon_note = await self._sync_daemon_model(preset.provider, preset.model)
            await query.answer("Model selected.")
            text = _model_selected_text(preset)
            if daemon_note:
                text += f"\n\n{daemon_note}"
            elif self.bridge.daemon_client is None:
                text += "\n\nTelegram session only. Use /model <provider>:<model> --global to update the TUI/default config."
            await query.edit_message_text(text)

    async def _sync_daemon_model(self, provider: str, model: str, *, persist_global: bool = False) -> str:
        if self.bridge.daemon_client is None:
            return ""
        try:
            payload = await self.bridge.daemon_client.update_model(provider, model, persist_global=persist_global)
        except Exception as exc:
            return f"Daemon default was not updated: {exc}"
        note = "Daemon default updated for new daemon-backed runs."
        automations_updated = int(payload.get("automations_updated") or 0)
        if persist_global and automations_updated:
            note += f" Updated {automations_updated} scheduled automation(s)."
        return note

    async def _authorized(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else None
        if self.auth.is_allowed(user_id):
            return True
        if update.effective_message is not None:
            username = update.effective_user.username if update.effective_user else None
            await update.effective_message.reply_text(_unauthorized_text(user_id, username))
        return False

    def _start_typing_indicator(self, bot: Any, chat_id: int) -> asyncio.Task[None]:
        return asyncio.create_task(
            _typing_indicator_loop(bot, chat_id),
            name=f"libre-claw-telegram-typing-{chat_id}",
        )

    async def _reply_permission_prompt(self, message: Any, event: TelegramPermissionPrompt) -> None:
        try:
            await _reply_text_chunks(
                message,
                event.text,
                self.bridge.config.telegram.max_message_length,
                reply_markup=self._permission_reply_markup(event.prompt_id),
            )
        except Exception as exc:
            await self._deny_unrenderable_permission(event.prompt_id, exc, message=message)

    async def _send_permission_prompt(self, bot: Any, chat_id: int, event: TelegramPermissionPrompt) -> None:
        chunks = _message_chunks(event.text, self.bridge.config.telegram.max_message_length)
        try:
            for index, chunk in enumerate(chunks):
                markup = self._permission_reply_markup(event.prompt_id) if index == len(chunks) - 1 else None
                await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=markup)
        except Exception as exc:
            await self._deny_unrenderable_permission(event.prompt_id, exc, bot=bot, chat_id=chat_id)

    def _permission_reply_markup(self, prompt_id: str) -> Any:
        token = self._register_permission_prompt(prompt_id)
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"p:y:{token}"),
                    InlineKeyboardButton("✖️ Deny", callback_data=f"p:n:{token}"),
                ],
                [
                    InlineKeyboardButton("Always tool", callback_data=f"p:t:{token}"),
                    InlineKeyboardButton("Always exact", callback_data=f"p:c:{token}"),
                ]
            ]
        )

    async def _edit_expandable_preview(
        self,
        message: Any,
        text: str,
        *,
        title: str,
        clean_final: bool = False,
        render_html: bool = True,
    ) -> None:
        reply_markup = (
            self._expand_reply_markup(
                TelegramExpandablePayload(title=title, text=text, clean_final=clean_final, render_html=render_html)
            )
            if _stream_preview_is_truncated(text, self.bridge.config.telegram.max_message_length)
            else None
        )
        await _edit_text_preview(message, text, self.bridge.config.telegram.max_message_length, reply_markup=reply_markup)

    def _tool_log_expand_markup(self, notices: Sequence[str], http_started: int, http_done: int) -> Any | None:
        visible_notices = _visible_tool_notices(notices, http_started, http_done)
        if len(visible_notices) <= 8 and not any("truncated" in notice.lower() for notice in visible_notices):
            return None
        return self._expand_reply_markup(
            TelegramExpandablePayload(
                title="Full tool activity",
                text=_tool_log_full(visible_notices, total_count=_tool_activity_count(notices, http_started, http_done)),
            )
        )

    def _expand_reply_markup(self, payload: TelegramExpandablePayload) -> Any:
        token = self._register_expand_payload(payload)
        return InlineKeyboardMarkup([[InlineKeyboardButton("Show full", callback_data=f"x:{token}")]])

    def _register_permission_prompt(self, prompt_id: str) -> str:
        self._permission_callback_counter += 1
        token = _base36(self._permission_callback_counter)
        self._permission_callback_ids[token] = prompt_id
        if len(self._permission_callback_ids) > 500:
            stale_tokens = list(self._permission_callback_ids)[:100]
            for stale_token in stale_tokens:
                self._permission_callback_ids.pop(stale_token, None)
        return token

    def _register_expand_payload(self, payload: TelegramExpandablePayload) -> str:
        self._expand_callback_counter += 1
        token = _base36(self._expand_callback_counter)
        self._expand_callback_ids[token] = payload
        if len(self._expand_callback_ids) > 500:
            stale_tokens = list(self._expand_callback_ids)[:100]
            for stale_token in stale_tokens:
                self._expand_callback_ids.pop(stale_token, None)
        return token

    async def _deny_unrenderable_permission(
        self,
        prompt_id: str,
        exc: Exception,
        *,
        message: Any | None = None,
        bot: Any | None = None,
        chat_id: int | None = None,
    ) -> None:
        await self.bridge.resolve_permission_async(prompt_id, "deny")
        notice = f"Telegram could not render the permission prompt, so Libre Claw denied it safely: {exc}"
        if message is not None:
            await _reply_text_chunks(message, notice, self.bridge.config.telegram.max_message_length)
            return
        if bot is not None and chat_id is not None:
            await _send_text_chunks(bot, chat_id, notice, self.bridge.config.telegram.max_message_length)


def _truncate(text: str, limit: int) -> str:
    limit = _telegram_message_limit(limit)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "\n...[truncated]"


def _stream_preview(text: str, configured_limit: int) -> str:
    limit = _telegram_message_limit(configured_limit)
    if len(text) <= limit:
        return text or " "
    available = max(1, limit - len(TELEGRAM_CONTINUED_SUFFIX))
    return text[:available].rstrip() + TELEGRAM_CONTINUED_SUFFIX


def _stream_preview_is_truncated(text: str, configured_limit: int) -> bool:
    return len(text) > _telegram_message_limit(configured_limit)


def _telegram_message_limit(configured_limit: int) -> int:
    return telegram_message_limit(configured_limit)


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    digits: list[str] = []
    remaining = value
    while remaining:
        remaining, index = divmod(remaining, 36)
        digits.append(alphabet[index])
    return "".join(reversed(digits))


async def _typing_indicator_loop(bot: Any, chat_id: int) -> None:
    send_chat_action = getattr(bot, "send_chat_action", None)
    if send_chat_action is None:
        return
    try:
        while True:
            try:
                await send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                return
            await asyncio.sleep(TELEGRAM_TYPING_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise


async def _cancel_task(task: asyncio.Task[Any]) -> None:
    if task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _visible_tool_notices(notices: Sequence[str], http_started: int, http_done: int) -> list[str]:
    visible: list[str] = []
    if http_started or http_done:
        pieces = [f"{http_started} requested", f"{http_done} done"]
        visible.append("🌐 HTTP requests: " + ", ".join(pieces))
    visible.extend(notices)
    return visible


def _tool_activity_count(notices: Sequence[str], http_started: int, http_done: int) -> int:
    return max(http_started, http_done) + len(notices)


def _tool_log_preview(notices: Sequence[str], configured_limit: int, *, total_count: int | None = None) -> str:
    limit = _telegram_message_limit(configured_limit)
    visible = list(notices[-8:])
    event_count = total_count if total_count is not None else len(notices)
    hidden_count = max(0, len(notices) - len(visible))
    sections = [f"🧰 Tool activity ({event_count})"]
    if hidden_count:
        sections.append(f"… {hidden_count} earlier events hidden")
    sections.extend(_format_tool_notice_for_log(notice, body_limit=420) for notice in visible)
    preview = "\n\n".join(sections)
    if len(preview) <= limit:
        return preview
    return _truncate(preview, configured_limit)


def _tool_log_full(notices: Sequence[str], *, total_count: int) -> str:
    sections = [f"🧰 Tool activity ({total_count})"]
    sections.extend(_format_tool_notice_for_log(notice, body_limit=None) for notice in notices)
    return "\n\n".join(sections)


def _format_tool_notice_for_log(notice: str, *, body_limit: int | None) -> str:
    lines = [line.rstrip() for line in notice.strip().splitlines() if line.strip()]
    if not lines:
        return "Tool event"
    head = lines[0]
    body = "\n".join(lines[1:])
    if not body:
        return head
    body_text = _truncate(body, body_limit) if body_limit is not None else body
    return f"{head}\n```text\n{body_text}\n```"


async def _edit_text_preview(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
) -> None:
    preview = _stream_preview(text, configured_limit)
    try:
        await message.edit_text(preview, reply_markup=reply_markup)
    except TypeError:
        await message.edit_text(preview)
    except Exception as exc:
        if _is_telegram_error(exc, "message is not modified"):
            return
        if _is_telegram_error(exc, "message is too long"):
            await message.edit_text(_truncate(preview, configured_limit), reply_markup=reply_markup)
            return
        raise


async def _edit_formatted_text(message: Any, chunk: TelegramFormattedChunk) -> None:
    try:
        await message.edit_text(
            chunk.text,
            parse_mode=chunk.parse_mode,
            disable_web_page_preview=True,
        )
    except TypeError:
        await message.edit_text(chunk.text)
    except Exception as exc:
        if _is_telegram_error(exc, "message is not modified"):
            return
        if chunk.parse_mode is not None and _is_telegram_parse_error(exc):
            await message.edit_text(_strip_html_tags(chunk.text), disable_web_page_preview=True)
            return
        raise


async def _safe_edit_text_preview(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
) -> bool:
    try:
        await _edit_text_preview(message, text, configured_limit, reply_markup=reply_markup)
        return True
    except Exception as exc:
        if _is_telegram_retry_after(exc):
            return False
        raise


async def _finish_text_response(message: Any, reply_to: Any, text: str, configured_limit: int) -> None:
    chunks = telegram_html_chunks(text, configured_limit, clean_final=True)
    first = chunks[0] if chunks else TelegramFormattedChunk(" ")
    await _edit_formatted_text(message, first)
    for chunk in chunks[1:]:
        await _reply_formatted_chunk(reply_to, chunk)


async def _reply_tool_log_preview(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
) -> Any:
    chunk = _tool_log_formatted_chunk(text, configured_limit)
    try:
        return await message.reply_text(
            chunk.text,
            reply_markup=reply_markup,
            parse_mode=chunk.parse_mode,
            disable_web_page_preview=True,
        )
    except TypeError:
        return await message.reply_text(_strip_html_tags(chunk.text), reply_markup=reply_markup)
    except Exception as exc:
        if chunk.parse_mode is not None and _is_telegram_parse_error(exc):
            return await message.reply_text(
                _strip_html_tags(chunk.text),
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        if _is_telegram_error(exc, "message is too long") and len(text) > 1:
            return await _reply_tool_log_preview(
                message,
                _truncate(text, configured_limit),
                configured_limit,
                reply_markup=reply_markup,
            )
        raise


async def _safe_edit_tool_log_preview(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
) -> bool:
    chunk = _tool_log_formatted_chunk(text, configured_limit)
    try:
        await message.edit_text(
            chunk.text,
            reply_markup=reply_markup,
            parse_mode=chunk.parse_mode,
            disable_web_page_preview=True,
        )
        return True
    except TypeError:
        try:
            await message.edit_text(_strip_html_tags(chunk.text), reply_markup=reply_markup)
        except TypeError:
            await message.edit_text(_strip_html_tags(chunk.text))
        return True
    except Exception as exc:
        if _is_telegram_error(exc, "message is not modified"):
            return True
        if _is_telegram_retry_after(exc):
            return False
        if chunk.parse_mode is not None and _is_telegram_parse_error(exc):
            await message.edit_text(
                _strip_html_tags(chunk.text),
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True
        if _is_telegram_error(exc, "message is too long"):
            return await _safe_edit_text_preview(message, _strip_html_tags(chunk.text), configured_limit, reply_markup=reply_markup)
        raise


def _tool_log_formatted_chunk(text: str, configured_limit: int) -> TelegramFormattedChunk:
    limit = _telegram_message_limit(configured_limit)
    source = text
    html = markdown_to_telegram_html(source)
    if len(html) > limit:
        source = _truncate(source, configured_limit)
        html = markdown_to_telegram_html(source)
    return TelegramFormattedChunk(html, parse_mode="HTML")


async def _reply_text_chunks(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
    clean_final: bool = False,
    render_html: bool = True,
) -> None:
    chunks = (
        telegram_html_chunks(text, configured_limit, clean_final=clean_final)
        if render_html
        else [TelegramFormattedChunk(chunk, parse_mode=None) for chunk in _message_chunks(text, configured_limit)]
    )
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        try:
            await _reply_formatted_chunk(message, chunk, reply_markup=markup)
        except Exception as exc:
            if not _is_telegram_error(exc, "message is too long") or len(chunk.text) <= 1:
                raise
            smaller_limit = max(1, min(_telegram_message_limit(configured_limit) // 2, len(chunk.text) - 1))
            await _reply_text_chunks(
                message,
                chunk.text,
                smaller_limit,
                reply_markup=markup,
                render_html=chunk.parse_mode is not None,
            )


async def _reply_text_chunk(message: Any, chunk: str, configured_limit: int, *, reply_markup: Any | None = None) -> None:
    formatted = TelegramFormattedChunk(chunk, parse_mode=None)
    try:
        await _reply_formatted_chunk(message, formatted, reply_markup=reply_markup)
    except Exception as exc:
        if not _is_telegram_error(exc, "message is too long") or len(chunk) <= 1:
            raise
        smaller_limit = max(1, min(_telegram_message_limit(configured_limit) // 2, len(chunk) - 1))
        smaller_chunks = _message_chunks(chunk, smaller_limit)
        for index, smaller_chunk in enumerate(smaller_chunks):
            markup = reply_markup if index == len(smaller_chunks) - 1 else None
            await _reply_text_chunk(message, smaller_chunk, smaller_limit, reply_markup=markup)


async def _reply_formatted_chunk(
    message: Any,
    chunk: TelegramFormattedChunk,
    *,
    reply_markup: Any | None = None,
) -> None:
    try:
        await message.reply_text(
            chunk.text,
            reply_markup=reply_markup,
            parse_mode=chunk.parse_mode,
            disable_web_page_preview=True,
        )
    except TypeError:
        await message.reply_text(chunk.text, reply_markup=reply_markup)
    except Exception as exc:
        if chunk.parse_mode is not None and _is_telegram_parse_error(exc):
            await message.reply_text(
                _strip_html_tags(chunk.text),
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return
        if not _is_telegram_error(exc, "message is too long") or len(chunk.text) <= 1:
            raise
        raise


async def _send_text_chunks(
    bot: Any,
    chat_id: int,
    text: str,
    configured_limit: int,
    *,
    clean_final: bool = False,
) -> None:
    for chunk in telegram_html_chunks(text, configured_limit, clean_final=clean_final):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk.text,
                parse_mode=chunk.parse_mode,
                disable_web_page_preview=True,
            )
        except TypeError:
            await bot.send_message(chat_id=chat_id, text=chunk.text)
        except Exception as exc:
            if chunk.parse_mode is not None and _is_telegram_parse_error(exc):
                await bot.send_message(
                    chat_id=chat_id,
                    text=_strip_html_tags(chunk.text),
                    disable_web_page_preview=True,
                )
                continue
            raise


def _message_chunks(text: str, configured_limit: int) -> list[str]:
    return plain_text_chunks(text, configured_limit)


def _is_telegram_error(exc: Exception, message: str) -> bool:
    if BadRequest is not None and isinstance(exc, BadRequest):
        return message in str(exc).lower()
    return exc.__class__.__name__ == "BadRequest" and message in str(exc).lower()


def _is_telegram_retry_after(exc: Exception) -> bool:
    if hasattr(exc, "retry_after"):
        return True
    text = str(exc).lower()
    return "flood control exceeded" in text or "retry after" in text


def _is_telegram_parse_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return _is_telegram_error(exc, "can't parse entities") or "unsupported start tag" in text


def _strip_html_tags(text: str) -> str:
    return re.sub(r"</?(?:b|i|code|pre|a)(?:\s+[^>]*)?>", "", text)


def _unauthorized_text(user_id: int | None, username: str | None) -> str:
    lines = ["You are not authorized to use this Libre Claw bot."]
    if username:
        lines.append(f"Telegram username: @{username}")
    if user_id is None:
        lines.append("Telegram did not provide a numeric user ID for this update.")
        return "\n".join(lines)
    lines.extend(
        [
            f"Telegram user ID: {user_id}",
            "Allow this user on the machine running Libre Claw:",
            f"libre-claw telegram allow {user_id}",
            "Then restart `libre-claw telegram up`.",
        ]
    )
    return "\n".join(lines)


def _telegram_help_text() -> str:
    return "\n".join(
        [
            "Libre Claw Telegram commands:",
            "/start - Check that the bot is ready",
            "/help - Show this command list",
            "/new - Start a fresh chat session",
            "/restart - Start a fresh chat session",
            "/model - Open provider/model buttons",
            "/model <provider>:<name> - Switch model by text",
            "/models - Open provider/model buttons",
            "/provider - Open provider buttons",
            "/cost - Show model, context, token, and cost usage",
            "/usage [provider] - Show provider usage analytics",
            "/status - Show model, context, token, and cost usage",
            "/daemon - Show daemon connection health",
            "/runs [N] - List recent daemon runs",
            "/run <id> - Inspect a daemon run",
            "/compact - Compact the current context",
            "/schedule examples|list|add ... - Manage recurring runs",
            "/heartbeat status|once|start|stop - Recurring check-ins",
            "/memory status|list|search|add|forget - Manage persistent memory",
            "/cancel - Cancel the active generation",
            "/stop - Cancel the active generation",
            "/shutdown - Shut down the daemon/bridge",
            "/btw <note> - Add a side note for future turns",
            "/steer <instruction> - Steer future agent turns",
            "",
            "Send a normal message to start an agent run.",
        ]
    )


def telegram_command_specs() -> Sequence[tuple[str, str]]:
    return (
        ("start", "Check that Libre Claw is ready"),
        ("help", "Show Telegram slash commands"),
        ("new", "Start a fresh chat session"),
        ("restart", "Start a fresh chat session"),
        ("model", "Open model configuration"),
        ("models", "Open model configuration"),
        ("provider", "Open provider selector"),
        ("cost", "Show model, context, tokens, and cost"),
        ("usage", "Show provider usage analytics"),
        ("status", "Show model, context, tokens, and cost"),
        ("daemon", "Show daemon health"),
        ("runs", "List recent daemon runs"),
        ("run", "Inspect one daemon run"),
        ("compact", "Compact the current context"),
        ("schedule", "Manage recurring runs"),
        ("heartbeat", "Recurring check-ins"),
        ("memory", "Manage persistent memory"),
        ("cancel", "Cancel active generation"),
        ("stop", "Cancel active generation"),
        ("shutdown", "Shut down Libre Claw"),
        ("btw", "Add a side note"),
        ("steer", "Steer future turns"),
    )


def _replace_general(config, **changes):
    from libre_claw.tui.app import _replace_general as replace_general

    return replace_general(config, **changes)


def _canonical_telegram_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized in {"local", "ollama-cloud", "ollama_cloud"}:
        return "ollama"
    if normalized in {"openai-codex", "openai_codex"}:
        return "codex"
    return normalized


def _parse_telegram_model_argument(argument: str, current_provider: str) -> tuple[str, str, bool]:
    cleaned, persist_global = _strip_telegram_global_flag(argument)
    provider = _canonical_telegram_provider(current_provider)
    if not cleaned:
        return provider, "", persist_global
    prefix, separator, rest = cleaned.partition(":")
    canonical_prefix = _canonical_telegram_provider(prefix)
    if separator and canonical_prefix in TELEGRAM_PROVIDER_LABELS and rest.strip():
        return canonical_prefix, rest.strip(), persist_global
    return provider, cleaned, persist_global


def _strip_telegram_global_flag(argument: str) -> tuple[str, bool]:
    parts = argument.strip().split()
    if "--global" not in parts:
        return argument.strip(), False
    cleaned = " ".join(part for part in parts if part != "--global")
    return cleaned, True


def _provider_usage_text() -> str:
    providers = "|".join(TELEGRAM_PROVIDER_LABELS)
    return f"Usage: /provider {providers}\nOr send /provider to use buttons."


def _model_configuration_text(config: Any) -> str:
    provider = _canonical_telegram_provider(str(config.general.default_provider))
    model = str(config.general.default_model)
    label = TELEGRAM_PROVIDER_LABELS.get(provider, provider)
    return "\n".join(
        [
            "Model Configuration",
            "",
            f"Current model: {model}",
            f"Provider: {label}",
            "",
            "Select a provider:",
        ]
    )


def _provider_model_text(config: Any, provider: str) -> str:
    current_provider = _canonical_telegram_provider(str(config.general.default_provider))
    current_model = str(config.general.default_model)
    label = TELEGRAM_PROVIDER_LABELS.get(provider, provider)
    lines = [
        "Model Configuration",
        "",
        f"Provider: {label}",
        f"Current model: {current_provider}:{current_model}",
        "",
        "Select a model:",
    ]
    return "\n".join(lines)


def _model_selected_text(preset: TelegramModelPreset) -> str:
    label = TELEGRAM_PROVIDER_LABELS.get(preset.provider, preset.provider)
    return "\n".join(
        [
            "Model selected",
            "",
            f"Provider: {label}",
            f"Model: {preset.model}",
            "",
            "Your next Telegram message will use this model.",
        ]
    )


def _provider_keyboard(config: Any) -> Any:
    provider = _canonical_telegram_provider(str(config.general.default_provider))
    buttons: list[list[Any]] = []
    row: list[Any] = []
    for provider_name in TELEGRAM_MODEL_PRESETS:
        label = TELEGRAM_PROVIDER_LABELS[provider_name]
        count = len(TELEGRAM_MODEL_PRESETS.get(provider_name, ()))
        prefix = "✓ " if provider_name == provider else ""
        row.append(InlineKeyboardButton(f"{prefix}{label} ({count})", callback_data=f"cfg:provider:{provider_name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("× Cancel", callback_data="cfg:cancel")])
    return InlineKeyboardMarkup(buttons)


def _model_keyboard(config: Any, provider: str) -> Any:
    current_provider = _canonical_telegram_provider(str(config.general.default_provider))
    current_model = str(config.general.default_model)
    presets = TELEGRAM_MODEL_PRESETS.get(provider, ())
    indexed_presets = list(enumerate(presets))
    indexed_presets.sort(
        key=lambda item: (
            not (provider == current_provider and item[1].model == current_model),
            item[0],
        )
    )
    buttons: list[list[Any]] = []
    row: list[Any] = []
    for index, preset in indexed_presets:
        selected = provider == current_provider and preset.model == current_model
        prefix = "✓ " if selected else ""
        row.append(InlineKeyboardButton(f"{prefix}{preset.label}", callback_data=f"cfg:model:{provider}:{index}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(
        [
            InlineKeyboardButton("‹ Providers", callback_data="cfg:providers"),
            InlineKeyboardButton("× Cancel", callback_data="cfg:cancel"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def _model_preset_at(provider: str, index_text: str) -> TelegramModelPreset | None:
    if not index_text.isdigit():
        return None
    presets = TELEGRAM_MODEL_PRESETS.get(provider, ())
    index = int(index_text)
    if index < 0 or index >= len(presets):
        return None
    return presets[index]
