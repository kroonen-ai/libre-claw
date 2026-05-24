# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import html
import time

from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import (
    TelegramBridge,
    TelegramDone,
    TelegramError,
    TelegramPermissionPrompt,
    TelegramText,
    TelegramToolNotice,
)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import ContextTypes
except ImportError:  # pragma: no cover - dependency availability is checked in integration.
    InlineKeyboardButton = InlineKeyboardMarkup = Update = ContextTypes = None  # type: ignore[assignment]


class TelegramHandlers:
    def __init__(self, bridge: TelegramBridge, auth: TelegramAuth) -> None:
        self.bridge = bridge
        self.auth = auth

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text("Libre Claw is ready.")

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
        cancelled = self.bridge.cancel(update.effective_chat.id)
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
            await update.effective_message.reply_text("Usage: /provider anthropic|openai|local")
            return
        self.bridge.config = _replace_general(self.bridge.config, default_provider=provider)
        await update.effective_message.reply_text(f"Provider set to {provider}.")

    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = update.effective_message.text or ""
        chat_id = update.effective_chat.id
        placeholder = await update.effective_message.reply_text("Libre Claw is thinking...")
        accumulated = ""
        last_update = time.monotonic()

        async def runner() -> None:
            nonlocal accumulated, last_update
            async for event in self.bridge.stream_message(chat_id, text):
                if isinstance(event, TelegramText):
                    accumulated += event.text
                    should_update = len(accumulated) % 100 == 0 or time.monotonic() - last_update >= self.bridge.config.telegram.stream_update_interval
                    if should_update:
                        await placeholder.edit_text(_truncate(accumulated, self.bridge.config.telegram.max_message_length))
                        last_update = time.monotonic()
                    continue
                if isinstance(event, TelegramToolNotice):
                    await update.effective_message.reply_text(event.text)
                    continue
                if isinstance(event, TelegramPermissionPrompt):
                    await update.effective_message.reply_text(
                        event.text,
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
                        await placeholder.edit_text(_truncate(accumulated, self.bridge.config.telegram.max_message_length))
                    else:
                        await placeholder.edit_text("Done.")
                    continue
                if isinstance(event, TelegramError):
                    await placeholder.edit_text(_truncate(event.text, self.bridge.config.telegram.max_message_length))
                    continue

        task = asyncio.create_task(runner())
        self.bridge.state_for(chat_id).task = task

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
            resolved = self.bridge.resolve_permission(prompt_id, "allow_once")
            await query.answer("Approved." if resolved else "Prompt expired.")
            return
        if data.startswith("perm:no:"):
            prompt_id = data.removeprefix("perm:no:")
            resolved = self.bridge.resolve_permission(prompt_id, "deny")
            await query.answer("Denied." if resolved else "Prompt expired.")

    async def _authorized(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else None
        if self.auth.is_allowed(user_id):
            return True
        if update.effective_message is not None:
            await update.effective_message.reply_text("You are not authorized to use this Libre Claw bot.")
        return False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "\n...[truncated]"


def _replace_general(config, **changes):
    from libre_claw.tui.app import _replace_general as replace_general

    return replace_general(config, **changes)
