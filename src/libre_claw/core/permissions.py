# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from libre_claw.config import PermissionsConfig
from libre_claw.core.tools import BaseTool, ToolCall


PermissionDecision = Literal["allow", "ask", "deny"]
PermissionResolution = Literal["allow_once", "deny", "always_allow_tool", "always_allow_call"]


@dataclass
class PermissionManager:
    config: PermissionsConfig
    always_allowed_tools: set[str] = field(default_factory=set)
    always_allowed_calls: set[str] = field(default_factory=set)

    def allow_tools_for_session(self, tool_names: tuple[str, ...]) -> None:
        self.always_allowed_tools.update(name for name in tool_names if name)

    def check(self, call: ToolCall, tool: BaseTool) -> PermissionDecision:
        if tool.permission_level == "deny":
            return "deny"

        if call.name in self.always_allowed_tools or call.fingerprint() in self.always_allowed_calls:
            return "allow"

        if self.config.auto_approve_read and call.name in {
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
            "skills_search",
        }:
            return "allow"

        if self.config.auto_approve_read and call.name == "http_request":
            method = str(call.arguments.get("method", "GET")).upper()
            has_body = bool(call.arguments.get("body") or call.arguments.get("json_body"))
            has_output = bool(call.arguments.get("output_path"))
            if method in {"GET", "HEAD"} and not has_body and not has_output:
                return "allow"

        if self.config.auto_approve_read and call.name == "process":
            action = str(call.arguments.get("action", "")).lower()
            if action in {"poll", "list"}:
                return "allow"

        if tool.permission_level in {"allow", "ask", "deny"}:
            return tool.permission_level

        if self.config.default_level in {"allow", "ask", "deny"}:
            return self.config.default_level  # type: ignore[return-value]

        return "ask"

    def apply_resolution(self, call: ToolCall, resolution: PermissionResolution) -> bool:
        if resolution == "allow_once":
            return True
        if resolution == "always_allow_tool":
            self.always_allowed_tools.add(call.name)
            return True
        if resolution == "always_allow_call":
            self.always_allowed_calls.add(call.fingerprint())
            return True
        return False
