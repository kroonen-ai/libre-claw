# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import LibreClawConfig
from libre_claw.core.memory import MemoryStore
from libre_claw.core.tools import ToolContext, ToolRegistry, registered_tool_types

# Import modules for their @register_tool side effects.
from libre_claw.tools_builtin import filesystem as _filesystem  # noqa: F401
from libre_claw.tools_builtin import shell as _shell  # noqa: F401


def create_builtin_registry(config: LibreClawConfig, memory_store: MemoryStore | None = None) -> ToolRegistry:
    context = ToolContext(
        working_directory=Path(config.general.working_directory).resolve(),
        restrict_to_working_dir=config.sandbox.restrict_to_working_dir,
        command_timeout=config.sandbox.command_timeout,
        allow_sudo=config.sandbox.allow_sudo,
        blocked_patterns=config.sandbox.blocked_patterns,
        memory_store=memory_store,
    )
    return ToolRegistry([tool_type(context) for tool_type in registered_tool_types()])
