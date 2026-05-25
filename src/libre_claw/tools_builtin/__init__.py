# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import LibreClawConfig
from libre_claw.core.memory import MemoryStore
from libre_claw.core.tools import ToolContext, ToolRegistry, registered_tool_types

# Import modules for their @register_tool side effects.
from libre_claw.tools_builtin import browser as _browser  # noqa: F401
from libre_claw.tools_builtin import filesystem as _filesystem  # noqa: F401
from libre_claw.tools_builtin import git as _git  # noqa: F401
from libre_claw.tools_builtin import mcp as _mcp
from libre_claw.tools_builtin import search as _search  # noqa: F401
from libre_claw.tools_builtin import shell as _shell  # noqa: F401
from libre_claw.tools_builtin import think as _think  # noqa: F401


def create_builtin_registry(config: LibreClawConfig, memory_store: MemoryStore | None = None) -> ToolRegistry:
    context = ToolContext(
        working_directory=Path(config.general.working_directory).resolve(),
        restrict_to_working_dir=config.sandbox.restrict_to_working_dir,
        command_timeout=config.sandbox.command_timeout,
        allow_sudo=config.sandbox.allow_sudo,
        blocked_patterns=config.sandbox.blocked_patterns,
        memory_store=memory_store,
        browser_allowed_domains=config.browser.allowed_domains,
        browser_denied_domains=config.browser.denied_domains,
        browser_profile_dir=config.browser.profile_dir,
        browser_downloads_dir=config.browser.downloads_dir,
        browser_screenshots_dir=config.browser.screenshots_dir,
        browser_default_timeout_ms=config.browser.default_timeout_ms,
        browser_headless=config.browser.headless,
    )
    tools = [tool_type(context) for tool_type in registered_tool_types()]
    tools.extend(_mcp.mcp_tools(config, context))
    return ToolRegistry(tools)
