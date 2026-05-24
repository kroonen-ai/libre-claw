# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from libre_claw.core.memory import MemoryStore

from libre_claw.core.sandbox import SandboxPolicy


PermissionLevel = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class ToolContext:
    working_directory: Path
    restrict_to_working_dir: bool = True
    command_timeout: int = 120
    allow_sudo: bool = False
    blocked_patterns: tuple[str, ...] = ()
    memory_store: "MemoryStore | None" = None

    def sandbox_policy(self) -> SandboxPolicy:
        return SandboxPolicy(
            working_directory=self.working_directory,
            restrict_to_working_dir=self.restrict_to_working_dir,
            command_timeout=self.command_timeout,
            allow_sudo=self.allow_sudo,
            blocked_patterns=self.blocked_patterns,
        )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "arguments": dict(self.arguments),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ToolResult:
    content: str = ""
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def as_text(self) -> str:
        if self.error is not None:
            return self.error
        return self.content


class ToolRegistryError(RuntimeError):
    """Raised when tool registration or lookup fails."""


class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[Mapping[str, Any]]
    required: ClassVar[tuple[str, ...]] = ()
    permission_level: ClassVar[PermissionLevel] = "ask"

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": dict(self.parameters),
                "required": list(self.required),
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool and return a normalized result."""

    def resolve_path(self, path: str) -> Path:
        return self.context.sandbox_policy().resolve_path(path)


_REGISTERED_TOOL_TYPES: dict[str, type[BaseTool]] = {}


def register_tool(tool_type: type[BaseTool]) -> type[BaseTool]:
    if tool_type.name in _REGISTERED_TOOL_TYPES:
        msg = f"Tool already registered: {tool_type.name}"
        raise ToolRegistryError(msg)
    _REGISTERED_TOOL_TYPES[tool_type.name] = tool_type
    return tool_type


def registered_tool_types() -> tuple[type[BaseTool], ...]:
    return tuple(_REGISTERED_TOOL_TYPES.values())


class ToolRegistry:
    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            msg = f"Tool already registered: {tool.name}"
            raise ToolRegistryError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            msg = f"Unknown tool: {name}"
            raise ToolRegistryError(msg) from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            tool = self.get(call.name)
            return await tool.execute(**dict(call.arguments))
        except Exception as exc:
            return ToolResult(error=str(exc))

    def __contains__(self, name: str) -> bool:
        return name in self._tools
