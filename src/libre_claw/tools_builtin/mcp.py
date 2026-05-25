# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from libre_claw.config import LibreClawConfig
from libre_claw.core.mcp import create_mcp_tools
from libre_claw.core.tools import BaseTool, ToolContext, ToolRegistryError


def mcp_tools(config: LibreClawConfig, context: ToolContext) -> list[BaseTool]:
    try:
        tools: list[BaseTool] = list(create_mcp_tools(config.mcp, context))
        return tools
    except Exception as exc:
        raise ToolRegistryError(str(exc)) from exc
