# Copyright 2026 Kroonen AI (https://kroonen.ai)
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
from libre_claw.tools_builtin import http as _http  # noqa: F401
from libre_claw.tools_builtin import mcp as _mcp
from libre_claw.tools_builtin import schedule as _schedule  # noqa: F401
from libre_claw.tools_builtin import search as _search  # noqa: F401
from libre_claw.tools_builtin import shell as _shell  # noqa: F401
from libre_claw.tools_builtin import skills as _skills  # noqa: F401
from libre_claw.tools_builtin import think as _think  # noqa: F401
from libre_claw.tools_builtin import web_search as _web_search  # noqa: F401


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
        web_search_enabled=config.web_search.enabled,
        web_search_provider=config.web_search.provider,
        web_search_base_url=config.web_search.base_url,
        web_search_timeout=config.web_search.timeout,
        web_search_max_results=config.web_search.max_results,
        web_search_default_language=config.web_search.default_language,
        web_search_default_safesearch=config.web_search.default_safesearch,
        web_search_default_categories=config.web_search.default_categories,
        web_search_default_engines=config.web_search.default_engines,
        automations_enabled=config.automations.enabled,
        automations_root=config.automations.root,
        default_provider=config.general.default_provider,
        default_model=config.general.default_model,
        skills_enabled=config.skills.enabled,
        skills_external_discovery_enabled=config.skills.external_discovery_enabled,
        skills_cli_enabled=config.skills.cli_enabled,
        skills_cli_command=config.skills.cli_command,
        skills_cli_timeout=config.skills.cli_timeout,
    )
    allowlist = set(config.agent.tool_allowlist)
    denylist = set(config.agent.tool_denylist)
    unavailable = _unavailable_tools(config)

    tools = [
        tool_type(context)
        for tool_type in registered_tool_types()
        if _tool_is_enabled(tool_type.name, allowlist, denylist, unavailable)
    ]
    tools.extend(
        tool
        for tool in _mcp.mcp_tools(config, context)
        if _tool_is_enabled(tool.name, allowlist, denylist, unavailable)
    )
    return ToolRegistry(tools)


def _unavailable_tools(config: LibreClawConfig) -> set[str]:
    unavailable: set[str] = set()
    if not config.web_search.enabled:
        unavailable.add("web_search")
    if not config.automations.enabled:
        unavailable.update({"schedule", "schedule_list"})
    if not (
        config.skills.enabled
        and config.skills.external_discovery_enabled
        and config.skills.cli_enabled
    ):
        unavailable.add("skills_search")
    return unavailable


def _tool_is_enabled(
    name: str,
    allowlist: set[str],
    denylist: set[str],
    unavailable: set[str],
) -> bool:
    if name in unavailable or name in denylist:
        return False
    return not allowlist or name in allowlist
