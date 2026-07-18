# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from libre_claw.config import load_config
from libre_claw.core.memory import MemoryStore
from libre_claw.core.tools import BaseTool, ToolContext, ToolRegistry, ToolResult
from libre_claw.headless import run_headless
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    StreamEvent,
    TextDelta,
    ToolCallReady,
    ToolSchema,
    Usage,
)


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses

    async def complete(
        self,
        messages,
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, system, stream, temperature, max_tokens
        for event in self.responses.pop(0):
            yield event


class AskEchoTool(BaseTool):
    name = "ask_echo"
    description = "Echo text after approval."
    parameters = {"value": {"type": "string"}}
    required = ("value",)
    permission_level = "ask"

    async def execute(self, value: str) -> ToolResult:
        return ToolResult(content=f"echo:{value}")


async def test_headless_run_streams_and_accumulates_text(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    provider = ScriptedProvider(
        [[TextDelta("Hel"), TextDelta("lo"), Done(Usage(input_tokens=4, output_tokens=2))]]
    )
    streamed: list[str] = []
    config = load_config(working_directory=tmp_path)

    result = await run_headless(
        config,
        "Say hello",
        provider=provider,
        tool_registry=ToolRegistry(),
        memory_store=MemoryStore(tmp_path / "memory.db"),
        on_text=streamed.append,
    )

    assert result.text == "Hello"
    assert result.usage == Usage(input_tokens=4, output_tokens=2)
    assert result.error is None
    assert streamed == ["Hel", "lo"]


async def test_headless_auto_approves_ask_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    provider = ScriptedProvider(
        [
            [ToolCallReady("call-1", "ask_echo", {"value": "approved"}), Done()],
            [TextDelta("finished"), Done()],
        ]
    )
    context = ToolContext(working_directory=tmp_path)
    registry = ToolRegistry([AskEchoTool(context)])

    result = await run_headless(
        load_config(working_directory=tmp_path),
        "Use the tool",
        auto_approve=True,
        provider=provider,
        tool_registry=registry,
        memory_store=MemoryStore(tmp_path / "memory.db"),
    )

    assert result.text == "finished"
    assert result.error is None
