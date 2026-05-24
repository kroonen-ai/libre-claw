# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

from libre_claw.config import LibreClawConfig
from libre_claw.telegram.auth import TelegramAuth
from libre_claw.telegram.bridge import TelegramBridge
from libre_claw.telegram.handlers import TelegramHandlers

try:
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
except ImportError:  # pragma: no cover - dependency availability is tested by imports after install.
    Application = CallbackQueryHandler = CommandHandler = MessageHandler = filters = None  # type: ignore[assignment]


class TelegramBot:
    def __init__(self, config: LibreClawConfig, bridge: TelegramBridge | None = None) -> None:
        self.config = config
        self.bridge = bridge or TelegramBridge(config)
        self.auth = TelegramAuth.from_config(config.telegram)

    async def run(self) -> None:
        token = os.getenv(self.config.telegram.bot_token_env)
        if not token:
            msg = f"Missing Telegram bot token. Set {self.config.telegram.bot_token_env}."
            raise RuntimeError(msg)
        if Application is None:
            raise RuntimeError("The python-telegram-bot package is not installed.")

        await self.bridge.initialize()
        handlers = TelegramHandlers(self.bridge, self.auth)
        application = Application.builder().token(token).build()
        application.add_handler(CommandHandler("start", handlers.start))
        application.add_handler(CommandHandler("new", handlers.new))
        application.add_handler(CommandHandler("cost", handlers.cost))
        application.add_handler(CommandHandler("compact", handlers.compact))
        application.add_handler(CommandHandler("cancel", handlers.cancel))
        application.add_handler(CommandHandler("model", handlers.model))
        application.add_handler(CommandHandler("provider", handlers.provider))
        application.add_handler(CallbackQueryHandler(handlers.callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message))

        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        try:
            await application.updater.wait_until_closed()
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
