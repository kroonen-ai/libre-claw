# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from libre_claw import __version__
from libre_claw.config import MCPConfig
from libre_claw.core.tools import BaseTool, PermissionLevel, ToolContext, ToolResult


class MCPError(RuntimeError):
    """Raised when an MCP server cannot be called safely."""


@dataclass(frozen=True)
class MCPToolSpec:
    server: str
    tool: str
    command: tuple[str, ...]
    env: dict[str, str]
    permission_level: PermissionLevel
    timeout: int

    @property
    def exposed_name(self) -> str:
        return f"mcp__{_slug(self.server)}__{_slug(self.tool)}"

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.tool}"


class MCPProxyTool(BaseTool):
    """Proxy a single allowlisted stdio MCP tool through Libre Claw permissions."""

    name: ClassVar[str] = "mcp"
    description: ClassVar[str] = "Call an allowlisted MCP tool."
    parameters: ClassVar[dict[str, Any]] = {
        "arguments": {
            "type": "object",
            "description": "Arguments to pass to the MCP tool.",
            "additionalProperties": True,
        }
    }
    required: ClassVar[tuple[str, ...]] = ("arguments",)
    permission_level: PermissionLevel = "ask"

    def __init__(self, context: ToolContext, spec: MCPToolSpec) -> None:
        super().__init__(context)
        self.spec = spec
        self.name = spec.exposed_name
        self.description = f"Call MCP tool {spec.qualified_name}."
        self.permission_level = spec.permission_level

    async def execute(self, arguments: dict[str, Any] | None = None) -> ToolResult:
        try:
            result = await call_stdio_mcp_tool(self.spec, arguments or {})
        except Exception as exc:
            return ToolResult(error=str(exc), metadata={"server": self.spec.server, "tool": self.spec.tool})
        return result


def create_mcp_tools(config: MCPConfig, context: ToolContext) -> list[MCPProxyTool]:
    if not config.enabled:
        return []
    specs = mcp_tool_specs(config)
    return [MCPProxyTool(context, spec) for spec in specs]


def mcp_tool_specs(config: MCPConfig) -> list[MCPToolSpec]:
    permission = _permission_level(config.permission_level)
    global_allowlist = {_normalize_tool_ref(item) for item in config.allowlist}
    specs: list[MCPToolSpec] = []
    for server_name, server_config in sorted(config.servers.items()):
        command = _command(server_name, server_config)
        tools = _tools(server_name, server_config)
        env = _env(server_config)
        for tool_name in tools:
            qualified = _normalize_tool_ref(f"{server_name}.{tool_name}")
            if global_allowlist and qualified not in global_allowlist:
                continue
            specs.append(
                MCPToolSpec(
                    server=server_name,
                    tool=tool_name,
                    command=command,
                    env=env,
                    permission_level=permission,
                    timeout=config.tool_timeout,
                )
            )
    return specs


async def call_stdio_mcp_tool(spec: MCPToolSpec, arguments: dict[str, Any]) -> ToolResult:
    env = os.environ.copy()
    env.update(spec.env)
    process = await asyncio.create_subprocess_exec(
        *spec.command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        async with asyncio.timeout(max(1, spec.timeout)):
            await _send_message(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "Libre Claw", "version": __version__},
                    },
                },
            )
            initialize = await _read_response(process, 1)
            if "error" in initialize:
                raise MCPError(_rpc_error_text(initialize["error"]))
            await _send_message(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            await _send_message(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": spec.tool, "arguments": arguments},
                },
            )
            response = await _read_response(process, 2)
            if "error" in response:
                raise MCPError(_rpc_error_text(response["error"]))
            return _tool_result_from_mcp(spec, _object(response.get("result")))
    except TimeoutError as exc:
        raise MCPError(f"MCP tool {spec.qualified_name} timed out after {spec.timeout}s") from exc
    finally:
        await _terminate_process(process)


async def _send_message(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise MCPError("MCP server stdin is unavailable.")
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    process.stdin.write(header + body)
    await process.stdin.drain()


async def _read_response(process: asyncio.subprocess.Process, response_id: int) -> dict[str, Any]:
    while True:
        payload = await _read_message(process)
        if payload.get("id") == response_id:
            return payload


async def _read_message(process: asyncio.subprocess.Process) -> dict[str, Any]:
    if process.stdout is None:
        raise MCPError("MCP server stdout is unavailable.")
    content_length: int | None = None
    while True:
        line = await process.stdout.readline()
        if line == b"":
            stderr = await _stderr_text(process)
            raise MCPError(f"MCP server closed stdout before responding. {stderr}".strip())
        stripped = line.strip()
        if not stripped:
            break
        name, _, value = stripped.decode("ascii", errors="replace").partition(":")
        if name.lower() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        raise MCPError("MCP server response did not include Content-Length.")
    body = await process.stdout.readexactly(content_length)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise MCPError("MCP server returned a non-object JSON-RPC message.")
    return payload


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _stderr_text(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(4096), timeout=0.2)
    except asyncio.TimeoutError:
        return ""
    text = data.decode("utf-8", errors="replace").strip()
    return f"stderr: {text}" if text else ""


def _tool_result_from_mcp(spec: MCPToolSpec, payload: dict[str, Any]) -> ToolResult:
    content = _mcp_content_text(payload.get("content"))
    metadata = {"server": spec.server, "tool": spec.tool, "qualified_name": spec.qualified_name}
    if payload.get("isError"):
        return ToolResult(error=content or f"MCP tool {spec.qualified_name} returned an error.", metadata=metadata)
    return ToolResult(content=content, metadata=metadata)


def _mcp_content_text(value: object) -> str:
    if not isinstance(value, list):
        return json.dumps(value, sort_keys=True, default=str) if value is not None else ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(json.dumps(item, sort_keys=True, default=str))
    return "\n".join(part for part in parts if part)


def _command(server_name: str, server_config: dict[str, Any]) -> tuple[str, ...]:
    value = server_config.get("command")
    if isinstance(value, list) and all(isinstance(part, str) and part for part in value):
        return tuple(value)
    if isinstance(value, str) and value.strip():
        return tuple(shlex.split(value))
    raise MCPError(f"MCP server {server_name} must configure a non-empty command.")


def _tools(server_name: str, server_config: dict[str, Any]) -> tuple[str, ...]:
    value = server_config.get("tools", ())
    if not isinstance(value, list) or not value or not all(isinstance(tool, str) and tool.strip() for tool in value):
        raise MCPError(f"MCP server {server_name} must configure a non-empty tools list.")
    return tuple(tool.strip() for tool in value)


def _env(server_config: dict[str, Any]) -> dict[str, str]:
    value = server_config.get("env", {})
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _permission_level(value: str) -> PermissionLevel:
    if value not in {"allow", "ask", "deny"}:
        raise MCPError("[mcp].permission_level must be allow, ask, or deny.")
    return cast(PermissionLevel, value)


def _normalize_tool_ref(value: str) -> str:
    return value.replace("/", ".").strip().lower()


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")


def _object(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rpc_error_text(value: object) -> str:
    payload = _object(value)
    message = payload.get("message")
    return str(message or value)
