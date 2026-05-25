# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os

import structlog

from libre_claw.auth.api_keys import ApiKeyStore, KeyStorageError
from libre_claw.config import LibreClawConfig
from libre_claw.daemon import DaemonClient, daemon_base_url
from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import TelegramBridge
from libre_claw.telegram.handlers import TelegramHandlers, telegram_command_specs

try:
    from telegram import (
        BotCommand,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
        MenuButtonCommands,
    )
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
except ImportError:  # pragma: no cover - dependency availability is tested by imports after install.
    BotCommand = BotCommandScopeAllPrivateChats = BotCommandScopeChat = BotCommandScopeDefault = MenuButtonCommands = None  # type: ignore[assignment]
    Application = CallbackQueryHandler = CommandHandler = MessageHandler = filters = None  # type: ignore[assignment]


LOGGER = structlog.get_logger(__name__)


class TelegramBot:
    def __init__(self, config: LibreClawConfig, bridge: TelegramBridge | None = None) -> None:
        self.config = config
        daemon_client = DaemonClient(daemon_base_url(config)) if config.telegram.use_daemon else None
        self.bridge = bridge or TelegramBridge(config, daemon_client=daemon_client)
        self.auth = TelegramAuth.from_config(config.telegram)

    async def run(self) -> None:
        token = self._bot_token()
        if not token:
            msg = (
                "Missing Telegram bot token. Run `libre-claw telegram setup` "
                f"or set {self.config.telegram.bot_token_env}."
            )
            raise RuntimeError(msg)
        if Application is None:
            raise RuntimeError("The python-telegram-bot package is not installed.")

        await self.bridge.initialize()
        handlers = TelegramHandlers(self.bridge, self.auth)
        application = Application.builder().token(token).build()
        application.add_handler(CommandHandler("start", handlers.start))
        application.add_handler(CommandHandler("help", handlers.help))
        application.add_handler(CommandHandler("new", handlers.new))
        application.add_handler(CommandHandler("cost", handlers.cost))
        application.add_handler(CommandHandler("status", handlers.cost))
        application.add_handler(CommandHandler("compact", handlers.compact))
        application.add_handler(CommandHandler("cancel", handlers.cancel))
        application.add_handler(CommandHandler("stop", handlers.cancel))
        application.add_handler(CommandHandler("model", handlers.model))
        application.add_handler(CommandHandler("models", handlers.model))
        application.add_handler(CommandHandler("provider", handlers.provider))
        application.add_handler(CommandHandler("schedule", handlers.schedule))
        application.add_handler(CallbackQueryHandler(handlers.callback))
        application.add_handler(MessageHandler(filters.COMMAND, handlers.unknown_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message))

        updater = application.updater
        if updater is None:
            raise RuntimeError("Telegram polling is unavailable for this application.")

        await application.initialize()
        await self._sync_command_menu(application)
        app_started = False
        polling_started = False
        try:
            await application.start()
            app_started = True
            await updater.start_polling()
            polling_started = True
            await self._wait_until_stopped()
        finally:
            if polling_started:
                await updater.stop()
            if app_started:
                await application.stop()
            await application.shutdown()

    def _bot_token(self) -> str | None:
        token = os.getenv(self.config.telegram.bot_token_env)
        if token:
            return token
        try:
            lookup = ApiKeyStore.from_config(self.config.auth).get_api_key("telegram", self.config.telegram.bot_token_env)
        except KeyStorageError:
            return None
        return lookup.value

    async def _wait_until_stopped(self) -> None:
        await asyncio.Event().wait()

    async def _sync_command_menu(self, application) -> None:
        if BotCommand is None:
            return
        commands = [BotCommand(command, description) for command, description in telegram_command_specs()]
        bot = application.bot
        scopes = []
        if BotCommandScopeDefault is not None:
            scopes.append(BotCommandScopeDefault())
        if BotCommandScopeAllPrivateChats is not None:
            scopes.append(BotCommandScopeAllPrivateChats())
        for scope in scopes:
            await bot.set_my_commands(commands, scope=scope)
        if not scopes:
            await bot.set_my_commands(commands)
        if MenuButtonCommands is not None:
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        if BotCommandScopeChat is None:
            return
        for user_id in sorted(self.auth.allowed_user_ids):
            try:
                await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
                if MenuButtonCommands is not None:
                    await bot.set_chat_menu_button(chat_id=user_id, menu_button=MenuButtonCommands())
            except Exception as exc:
                LOGGER.warning("telegram_command_scope_sync_failed", user_id=user_id, error=str(exc))
