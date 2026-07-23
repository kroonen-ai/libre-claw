# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib


def test_phase_one_modules_import() -> None:
    for module_name in (
        "libre_claw.__main__",
        "libre_claw.cli",
        "libre_claw.auth.api_keys",
        "libre_claw.auth.codex",
        "libre_claw.auth.oauth",
        "libre_claw.auth.tokens",
        "libre_claw.daemon",
        "libre_claw.core.agent",
        "libre_claw.core.automations",
        "libre_claw.core.heartbeat",
        "libre_claw.core.memory",
        "libre_claw.core.mcp",
        "libre_claw.core.permissions",
        "libre_claw.core.review",
        "libre_claw.core.runs",
        "libre_claw.core.sandbox",
        "libre_claw.core.searxng",
        "libre_claw.core.skills",
        "libre_claw.core.soul",
        "libre_claw.core.tools",
        "libre_claw.core.usage",
        "libre_claw.core.workspace",
        "libre_claw.providers.anthropic",
        "libre_claw.providers.codex",
        "libre_claw.providers.factory",
        "libre_claw.providers.local",
        "libre_claw.providers.moonshot",
        "libre_claw.providers.moonshot_catalog",
        "libre_claw.providers.moonshot_metadata",
        "libre_claw.providers.ollama",
        "libre_claw.providers.openai",
        "libre_claw.providers.openrouter",
        "libre_claw.providers.openrouter_catalog",
        "libre_claw.release",
        "libre_claw.telegram.auth",
        "libre_claw.telegram.bot",
        "libre_claw.telegram.bridge",
        "libre_claw.telegram.handlers",
        "libre_claw.tools_builtin.browser",
        "libre_claw.tools_builtin.filesystem",
        "libre_claw.tools_builtin.git",
        "libre_claw.tools_builtin.http",
        "libre_claw.tools_builtin.mcp",
        "libre_claw.tools_builtin.search",
        "libre_claw.tools_builtin.shell",
        "libre_claw.tools_builtin.think",
        "libre_claw.tools_builtin.web_search",
        "libre_claw.tui.app",
    ):
        assert importlib.import_module(module_name)
