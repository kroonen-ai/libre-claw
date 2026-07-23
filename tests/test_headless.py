# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest

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


class BlockingProvider(LLMProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()

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
        self.started.set()
        await asyncio.Event().wait()
        if False:
            yield Done()


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


async def test_headless_run_writes_atif_trajectory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    provider = ScriptedProvider(
        [[TextDelta("done"), Done(Usage(input_tokens=9, output_tokens=2))]]
    )
    trajectory_path = tmp_path / "logs" / "agent" / "trajectory.json"

    result = await run_headless(
        load_config(working_directory=tmp_path),
        "Complete this",
        provider=provider,
        tool_registry=ToolRegistry(),
        memory_store=MemoryStore(tmp_path / "memory.db"),
        trajectory_path=trajectory_path,
        trajectory_agent_version="commit-sha",
        trajectory_reasoning_effort="auto",
    )

    payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert result.text == "done"
    assert payload["agent"]["version"] == "commit-sha"
    assert payload["steps"][-1]["message"] == "done"
    assert payload["steps"][-1]["reasoning_effort"] == "auto"
    assert payload["final_metrics"]["total_prompt_tokens"] == 9


async def test_headless_cancel_preserves_latest_atif_checkpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    provider = BlockingProvider()
    trajectory_path = tmp_path / "logs" / "agent" / "trajectory.json"
    task = asyncio.create_task(
        run_headless(
            load_config(working_directory=tmp_path),
            "Keep this instruction",
            provider=provider,
            tool_registry=ToolRegistry(),
            memory_store=MemoryStore(tmp_path / "memory.db"),
            trajectory_path=trajectory_path,
        )
    )

    await asyncio.wait_for(provider.started.wait(), timeout=1)
    checkpoint = json.loads(trajectory_path.read_text(encoding="utf-8"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    cancelled = json.loads(trajectory_path.read_text(encoding="utf-8"))

    assert checkpoint["trajectory_id"] == cancelled["trajectory_id"]
    assert cancelled["extra"]["completed"] is False
    assert "cancelled" in cancelled["extra"]["error"].lower()
    assert any(step.get("message") == "Keep this instruction" for step in cancelled["steps"])


async def test_headless_deadline_finishes_atif_before_outer_cancellation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    trajectory_path = tmp_path / "logs" / "agent" / "trajectory.json"

    result = await asyncio.wait_for(
        run_headless(
            load_config(working_directory=tmp_path),
            "Keep this instruction",
            provider=BlockingProvider(),
            tool_registry=ToolRegistry(),
            memory_store=MemoryStore(tmp_path / "memory.db"),
            trajectory_path=trajectory_path,
            deadline_seconds=0.1,
            deadline_reserve_seconds=0.02,
        ),
        timeout=0.5,
    )

    payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert result.error == "Run deadline reached before a final response was produced."
    assert payload["extra"]["completed"] is False
    assert payload["extra"]["error"] == result.error
    assert any(step.get("message") == "Keep this instruction" for step in payload["steps"])
