# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import PermissionsConfig
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolResult


class AllowTool(BaseTool):
    name = "allow_tool"
    description = "Allow."
    parameters = {}
    permission_level = "allow"

    async def execute(self) -> ToolResult:
        return ToolResult(content="ok")


class AskTool(AllowTool):
    name = "ask_tool"
    permission_level = "ask"


class DenyTool(AllowTool):
    name = "deny_tool"
    permission_level = "deny"


def test_permission_decisions_and_overrides() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))
    call = ToolCall(id="1", name="ask_tool", arguments={"x": 1})

    assert manager.check(ToolCall(id="a", name="allow_tool"), AllowTool(context)) == "allow"
    assert manager.check(call, AskTool(context)) == "ask"
    assert manager.check(ToolCall(id="d", name="deny_tool"), DenyTool(context)) == "deny"

    assert manager.apply_resolution(call, "always_allow_tool") is True
    assert manager.check(call, AskTool(context)) == "allow"


def test_identical_call_cache() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=False))
    call = ToolCall(id="1", name="ask_tool", arguments={"x": 1})
    same_call = ToolCall(id="2", name="ask_tool", arguments={"x": 1})
    different_call = ToolCall(id="3", name="ask_tool", arguments={"x": 2})

    assert manager.apply_resolution(call, "always_allow_call") is True
    assert manager.check(same_call, AskTool(context)) == "allow"
    assert manager.check(different_call, AskTool(context)) == "ask"


def test_read_tools_auto_allowed_by_config() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))

    assert manager.check(ToolCall(id="1", name="read_file"), AskTool(context)) == "allow"
    assert manager.check(ToolCall(id="2", name="list_directory"), AskTool(context)) == "allow"
