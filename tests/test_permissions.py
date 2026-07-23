# Copyright 2026 Kroonen AI (https://kroonen.ai)
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


def test_can_seed_session_tool_allowlist() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=False))

    manager.allow_tools_for_session(("ask_tool", ""))

    assert manager.check(ToolCall(id="1", name="ask_tool"), AskTool(context)) == "allow"


def test_read_tools_auto_allowed_by_config() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))

    for name in (
        "read_file",
        "list_directory",
        "glob",
        "search_files",
        "view_image",
        "git_status",
        "think",
        "browser_read",
        "browser_extract",
        "browser_wait",
        "browser_screenshot",
        "browser_dismiss_cookies",
        "web_search",
    ):
        assert manager.check(ToolCall(id=name, name=name), AskTool(context)) == "allow"


def test_http_request_auto_allows_safe_reads_only() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))

    safe_get = ToolCall(id="get", name="http_request", arguments={"url": "https://example.com"})
    safe_head = ToolCall(id="head", name="http_request", arguments={"url": "https://example.com", "method": "HEAD"})
    post = ToolCall(id="post", name="http_request", arguments={"url": "https://example.com", "method": "POST"})
    download = ToolCall(
        id="download",
        name="http_request",
        arguments={"url": "https://example.com/file", "output_path": "file.bin"},
    )

    assert manager.check(safe_get, AskTool(context)) == "allow"
    assert manager.check(safe_head, AskTool(context)) == "allow"
    assert manager.check(post, AskTool(context)) == "ask"
    assert manager.check(download, AskTool(context)) == "ask"


def test_process_auto_allows_observation_but_not_mutation() -> None:
    context = ToolContext(working_directory=Path.cwd())
    manager = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))

    for action in ("poll", "list"):
        call = ToolCall(id=action, name="process", arguments={"action": action})
        assert manager.check(call, AskTool(context)) == "allow"

    for action in ("start", "write", "stop"):
        call = ToolCall(id=action, name="process", arguments={"action": action})
        assert manager.check(call, AskTool(context)) == "ask"
