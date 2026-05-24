# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib


def test_phase_one_modules_import() -> None:
    for module_name in (
        "libre_claw.__main__",
        "libre_claw.cli",
        "libre_claw.auth.api_keys",
        "libre_claw.auth.oauth",
        "libre_claw.auth.tokens",
        "libre_claw.core.agent",
        "libre_claw.core.memory",
        "libre_claw.core.permissions",
        "libre_claw.core.sandbox",
        "libre_claw.core.tools",
        "libre_claw.providers.anthropic",
        "libre_claw.providers.factory",
        "libre_claw.providers.local",
        "libre_claw.providers.openai",
        "libre_claw.telegram.auth",
        "libre_claw.telegram.bot",
        "libre_claw.telegram.bridge",
        "libre_claw.telegram.handlers",
        "libre_claw.tools_builtin.filesystem",
        "libre_claw.tools_builtin.shell",
        "libre_claw.tui.app",
    ):
        assert importlib.import_module(module_name)
