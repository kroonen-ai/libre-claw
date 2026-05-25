# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path

from libre_claw.config import MCPConfig
from libre_claw.core.mcp import create_mcp_tools, mcp_tool_specs
from libre_claw.core.tools import ToolContext


def test_mcp_specs_respect_allowlist(tmp_path: Path) -> None:
    config = MCPConfig(
        enabled=True,
        allowlist=("demo.echo",),
        permission_level="ask",
        tool_timeout=5,
        servers={
            "demo": {
                "command": [sys.executable, "-c", "print('unused')"],
                "tools": ["echo", "secret"],
            }
        },
    )

    specs = mcp_tool_specs(config)

    assert [spec.qualified_name for spec in specs] == ["demo.echo"]
    assert specs[0].exposed_name == "mcp__demo__echo"


async def test_mcp_proxy_tool_calls_stdio_server(tmp_path: Path) -> None:
    server = tmp_path / "mcp_server.py"
    server.write_text(
        r'''
import json
import sys

def read_message():
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if line in {b"\r\n", b"\n", b""}:
            break
        name, value = line.decode("ascii").split(":", 1)
        if name.lower() == "content-length":
            content_length = int(value.strip())
    body = sys.stdin.buffer.read(content_length)
    return json.loads(body.decode("utf-8"))

def write_message(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()

initialize = read_message()
write_message({"jsonrpc": "2.0", "id": initialize["id"], "result": {"capabilities": {}}})
read_message()
call = read_message()
value = call["params"]["arguments"]["value"]
write_message({
    "jsonrpc": "2.0",
    "id": call["id"],
    "result": {"content": [{"type": "text", "text": "echo:" + value}]},
})
''',
        encoding="utf-8",
    )
    config = MCPConfig(
        enabled=True,
        allowlist=("demo.echo",),
        permission_level="allow",
        tool_timeout=5,
        servers={"demo": {"command": [sys.executable, str(server)], "tools": ["echo"]}},
    )
    tools = create_mcp_tools(config, ToolContext(working_directory=tmp_path))

    result = await tools[0].execute(arguments={"value": "ok"})

    assert result.error is None
    assert result.content == "echo:ok"
    assert result.metadata["qualified_name"] == "demo.echo"
