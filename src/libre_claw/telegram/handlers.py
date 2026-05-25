# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramError,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
)

TELEGRAM_HARD_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_MESSAGE_LIMIT = 3900
TELEGRAM_CONTINUED_SUFFIX = "\n\n...[continued]"

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
        await update.effective_message.reply_text(self.bridge.status_text(update.effective_chat.id))

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

    async def model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        model = " ".join(context.args or [])
        if not model:
            await update.effective_message.reply_text("Usage: /model <name>")
            return
        self.bridge.config = _replace_general(self.bridge.config, default_model=model)
        await update.effective_message.reply_text(f"Model set to {model}.")

    async def provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        provider = " ".join(context.args or [])
        if not provider:
            await update.effective_message.reply_text("Usage: /provider anthropic|openai|openrouter|ollama|codex")
            return
        if provider == "local":
            provider = "ollama"
        self.bridge.config = _replace_general(self.bridge.config, default_provider=provider)
        await update.effective_message.reply_text(f"Provider set to {provider}.")

    async def schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = " ".join(context.args or [])
        response = await self.bridge.schedule_text(update.effective_chat.id, text)
        await _reply_text_chunks(update.effective_message, response, self.bridge.config.telegram.max_message_length)

    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = update.effective_message.text or ""
        if text.strip() == "/":
            await _reply_text_chunks(update.effective_message, _telegram_help_text(), self.bridge.config.telegram.max_message_length)
            return
        chat_id = update.effective_chat.id
        placeholder = await update.effective_message.reply_text("Libre Claw is thinking...")
        accumulated = ""
        last_update = time.monotonic()

        async def runner() -> None:
            nonlocal accumulated, last_update
            try:
                async for event in self.bridge.stream_message(chat_id, text):
                    if isinstance(event, TelegramText):
                        accumulated += event.text
                        should_update = len(accumulated) % 100 == 0 or time.monotonic() - last_update >= self.bridge.config.telegram.stream_update_interval
                        if should_update:
                            await _edit_text_preview(placeholder, accumulated, self.bridge.config.telegram.max_message_length)
                            last_update = time.monotonic()
                        continue
                    if isinstance(event, TelegramToolNotice):
                        await _reply_text_chunks(update.effective_message, event.text, self.bridge.config.telegram.max_message_length)
                        continue
                    if isinstance(event, TelegramPermissionPrompt):
                        await _reply_text_chunks(
                            update.effective_message,
                            event.text,
                            self.bridge.config.telegram.max_message_length,
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton("Approve", callback_data=f"perm:yes:{event.prompt_id}"),
                                        InlineKeyboardButton("Deny", callback_data=f"perm:no:{event.prompt_id}"),
                                    ]
                                ]
                            ),
                        )
                        continue
                    if isinstance(event, TelegramDone):
                        if accumulated:
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
                        await _finish_text_response(
                            placeholder,
                            update.effective_message,
                            event.text,
                            self.bridge.config.telegram.max_message_length,
                        )
                        continue
            except Exception as exc:
                await _finish_text_response(
                    placeholder,
                    update.effective_message,
                    f"Telegram bridge error: {exc}",
                    self.bridge.config.telegram.max_message_length,
                )

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

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not self.auth.is_allowed(query.from_user.id if query.from_user else None):
            await query.answer("Not authorized.", show_alert=True)
            return
        data = query.data or ""
        if data.startswith("perm:yes:"):
            prompt_id = data.removeprefix("perm:yes:")
            resolved = await self.bridge.resolve_permission_async(prompt_id, "allow_once")
            await query.answer("Approved." if resolved else "Prompt expired.")
            return
        if data.startswith("perm:no:"):
            prompt_id = data.removeprefix("perm:no:")
            resolved = await self.bridge.resolve_permission_async(prompt_id, "deny")
            await query.answer("Denied." if resolved else "Prompt expired.")

    async def _authorized(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else None
        if self.auth.is_allowed(user_id):
            return True
        if update.effective_message is not None:
            username = update.effective_user.username if update.effective_user else None
            await update.effective_message.reply_text(_unauthorized_text(user_id, username))
        return False


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


def _telegram_message_limit(configured_limit: int) -> int:
    return max(1, min(configured_limit, TELEGRAM_HARD_MESSAGE_LIMIT, TELEGRAM_SAFE_MESSAGE_LIMIT))


async def _edit_text_preview(message: Any, text: str, configured_limit: int) -> None:
    preview = _stream_preview(text, configured_limit)
    try:
        await message.edit_text(preview)
    except Exception as exc:
        if _is_telegram_error(exc, "message is not modified"):
            return
        if _is_telegram_error(exc, "message is too long"):
            await message.edit_text(_truncate(preview, configured_limit))
            return
        raise


async def _finish_text_response(message: Any, reply_to: Any, text: str, configured_limit: int) -> None:
    chunks = _message_chunks(text, configured_limit)
    first = chunks[0] if chunks else " "
    await _edit_text_preview(message, first, configured_limit)
    for chunk in chunks[1:]:
        await _reply_text_chunks(reply_to, chunk, configured_limit)


async def _reply_text_chunks(
    message: Any,
    text: str,
    configured_limit: int,
    *,
    reply_markup: Any | None = None,
) -> None:
    chunks = _message_chunks(text, configured_limit)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await _reply_text_chunk(message, chunk, configured_limit, reply_markup=markup)


async def _reply_text_chunk(message: Any, chunk: str, configured_limit: int, *, reply_markup: Any | None = None) -> None:
    try:
        await message.reply_text(chunk, reply_markup=reply_markup)
    except Exception as exc:
        if not _is_telegram_error(exc, "message is too long") or len(chunk) <= 1:
            raise
        smaller_limit = max(1, min(_telegram_message_limit(configured_limit) // 2, len(chunk) - 1))
        smaller_chunks = _message_chunks(chunk, smaller_limit)
        for index, smaller_chunk in enumerate(smaller_chunks):
            markup = reply_markup if index == len(smaller_chunks) - 1 else None
            await _reply_text_chunk(message, smaller_chunk, smaller_limit, reply_markup=markup)


def _message_chunks(text: str, configured_limit: int) -> list[str]:
    limit = _telegram_message_limit(configured_limit)
    remaining = text or " "
    chunks: list[str] = []
    while len(remaining) > limit:
        chunk = remaining[:limit]
        cut = max(chunk.rfind("\n"), chunk.rfind(" "))
        if cut < max(1, limit // 2):
            split_at = limit
        else:
            split_at = cut + 1
        chunks.append(remaining[:split_at] or remaining[:limit])
        remaining = remaining[split_at:]
    chunks.append(remaining or " ")
    return chunks


def _is_telegram_error(exc: Exception, message: str) -> bool:
    if BadRequest is not None and isinstance(exc, BadRequest):
        return message in str(exc).lower()
    return exc.__class__.__name__ == "BadRequest" and message in str(exc).lower()


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
            "/model <name> - Switch the current model",
            "/provider anthropic|openai|openrouter|ollama|codex - Switch provider",
            "/cost - Show token and cost usage",
            "/compact - Compact the current context",
            "/schedule examples|list|add ... - Manage recurring runs",
            "/cancel - Cancel the active generation",
            "",
            "Send a normal message to start an agent run.",
        ]
    )


def telegram_command_specs() -> Sequence[tuple[str, str]]:
    return (
        ("start", "Check that Libre Claw is ready"),
        ("help", "Show Telegram slash commands"),
        ("new", "Start a fresh chat session"),
        ("model", "Switch the current model"),
        ("provider", "Switch provider"),
        ("cost", "Show token and cost usage"),
        ("compact", "Compact the current context"),
        ("schedule", "Manage recurring runs"),
        ("cancel", "Cancel active generation"),
    )


def _replace_general(config, **changes):
    from libre_claw.tui.app import _replace_general as replace_general

    return replace_general(config, **changes)
