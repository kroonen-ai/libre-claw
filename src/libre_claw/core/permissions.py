# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
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

    def check(self, call: ToolCall, tool: BaseTool) -> PermissionDecision:
        if tool.permission_level == "deny":
            return "deny"

        if call.name in self.always_allowed_tools or call.fingerprint() in self.always_allowed_calls:
            return "allow"

        if self.config.auto_approve_read and call.name in {"read_file", "list_directory"}:
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
