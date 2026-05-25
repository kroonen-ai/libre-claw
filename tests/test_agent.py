# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from libre_claw.config import PermissionsConfig
from libre_claw.core.agent import (
    Agent,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    SkillProvider,
)
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import ChatMessage, Session, text_block, tool_result_block, tool_use_block
from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolRegistry, ToolResult
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolCallReady, ToolSchema, Usage


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.received_messages: list[list[ChatMessage]] = []
        self.received_tools: list[list[ToolSchema]] = []
        self.received_system: str | None = None

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del stream, temperature, max_tokens
        self.received_messages.append(list(messages))
        self.received_tools.append(list(tools or []))
        self.received_system = system
        for event in self.responses.pop(0):
            yield event


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a value."
    parameters = {"value": {"type": "string"}}
    required = ("value",)
    permission_level = "allow"

    async def execute(self, value: str) -> ToolResult:
        return ToolResult(content=f"echo:{value}")


class AskTool(EchoTool):
    name = "ask_echo"
    permission_level = "ask"


class BarrierTool(BaseTool):
    name = "barrier"
    description = "Track concurrent execution."
    parameters = {"value": {"type": "string"}}
    required = ("value",)
    permission_level = "allow"
    running = 0
    max_running = 0

    async def execute(self, value: str) -> ToolResult:
        type(self).running += 1
        type(self).max_running = max(type(self).max_running, type(self).running)
        await asyncio.sleep(0.01)
        type(self).running -= 1
        return ToolResult(content=value)


def make_agent(
    provider: LLMProvider,
    registry: ToolRegistry | None = None,
    max_tool_calls_per_turn: int = 50,
    system_prompt_extra: str = "",
    skill_provider: SkillProvider | None = None,
    soul_provider=None,
) -> Agent:
    permissions = PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True))
    return Agent(
        session=Session(),
        provider=provider,
        tool_registry=registry or ToolRegistry(),
        permission_manager=permissions,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        system_prompt="test system",
        system_prompt_extra=system_prompt_extra,
        skill_provider=skill_provider,
        soul_provider=soul_provider,
    )


async def collect_events(agent: Agent, message: str) -> list[object]:
    events: list[object] = []
    async for event in agent.run(message):
        if isinstance(event, AgentPermissionRequest):
            event.future.set_result("deny")
        events.append(event)
    return events


async def test_agent_streams_text_only_response_and_saves_history() -> None:
    provider = ScriptedProvider([[TextDelta("Hel"), TextDelta("lo"), Done(Usage(input_tokens=3, output_tokens=2))]])
    agent = make_agent(provider)

    events = await collect_events(agent, "Hi")

    assert events == [
        AgentTextDelta("Hel"),
        AgentTextDelta("lo"),
        AgentDone(Usage(input_tokens=3, output_tokens=2)),
    ]
    assert provider.received_messages[0] == [ChatMessage(role="user", content=[text_block("Hi")])]
    assert provider.received_system is not None
    assert provider.received_system.startswith("test system")
    assert agent.session.messages == [
        ChatMessage(role="user", content=[text_block("Hi")]),
        ChatMessage(role="assistant", content=[text_block("Hello")]),
    ]


async def test_agent_appends_configured_system_prompt_extra() -> None:
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(provider, system_prompt_extra="extra instructions")

    await collect_events(agent, "Hi")

    assert provider.received_system is not None
    assert provider.received_system.startswith("test system\n\nextra instructions")


async def test_agent_injects_soul_persona_into_system_prompt() -> None:
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(provider, soul_provider=lambda: ["Be electric but precise."])

    await collect_events(agent, "Hi")

    assert provider.received_system is not None
    assert "Libre Claw soul/persona customization" in provider.received_system
    assert "Be electric but precise." in provider.received_system
    assert "never override safety rules" in provider.received_system


async def test_agent_loads_relevant_skills_into_system_prompt() -> None:
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(
        provider,
        skill_provider=lambda prompt: [
            "Skill: Pytest Debug\n\nRun focused pytest cases."
        ] if "pytest" in prompt else [],
    )

    await collect_events(agent, "debug pytest failure")

    assert provider.received_system is not None
    assert "Relevant Libre Claw skills" in provider.received_system
    assert "Skill: Pytest Debug" in provider.received_system
    assert "/skills add <name>" in provider.received_system


async def test_agent_executes_tool_then_continues_to_final_answer() -> None:
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "echo", {"value": "x"}), Done(stop_reason="tool_use")],
            [TextDelta("done"), Done()],
        ]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    events = await collect_events(agent, "Use a tool")

    assert events == [
        AgentToolCall(ToolCall(id="toolu_1", name="echo", arguments={"value": "x"})),
        AgentToolResult(
            ToolCall(id="toolu_1", name="echo", arguments={"value": "x"}),
            ToolResult(content="echo:x"),
        ),
        AgentTextDelta("done"),
        AgentDone(None),
    ]
    assert agent.session.messages[1] == ChatMessage(
        role="assistant",
        content=[tool_use_block("toolu_1", "echo", {"value": "x"})],
    )
    assert agent.session.messages[2] == ChatMessage(
        role="user",
        content=[tool_result_block("toolu_1", "echo:x")],
    )


async def test_agent_accumulates_usage_across_tool_loop() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallReady("toolu_1", "echo", {"value": "x"}),
                Done(Usage(input_tokens=1, output_tokens=2, cost=0.125), stop_reason="tool_use"),
            ],
            [
                TextDelta("done"),
                Done(Usage(input_tokens=3, output_tokens=4, cached_tokens=1, reasoning_tokens=2, cost=0.25)),
            ],
        ]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    events = await collect_events(agent, "Use a tool")

    assert events[-1] == AgentDone(
        Usage(
            input_tokens=4,
            output_tokens=6,
            cached_tokens=1,
            reasoning_tokens=2,
            cost=0.375,
        )
    )


async def test_agent_executes_parallel_tool_calls_concurrently() -> None:
    BarrierTool.running = 0
    BarrierTool.max_running = 0
    provider = ScriptedProvider(
        [
            [
                ToolCallReady("toolu_1", "barrier", {"value": "a"}),
                ToolCallReady("toolu_2", "barrier", {"value": "b"}),
                Done(stop_reason="tool_use"),
            ],
            [TextDelta("done"), Done()],
        ]
    )
    registry = ToolRegistry([BarrierTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    await collect_events(agent, "Use two tools")

    assert BarrierTool.max_running == 2


async def test_agent_sends_denied_tool_result_back_to_model() -> None:
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "ask_echo", {"value": "x"}), Done(stop_reason="tool_use")],
            [TextDelta("done"), Done()],
        ]
    )
    registry = ToolRegistry([AskTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    events = await collect_events(agent, "Ask")

    assert any(isinstance(event, AgentPermissionRequest) for event in events)
    assert provider.received_messages[1][-1] == ChatMessage(
        role="user",
        content=[tool_result_block("toolu_1", "User denied this action", is_error=True)],
    )


async def test_agent_stops_when_tool_call_ceiling_is_exceeded() -> None:
    provider = ScriptedProvider(
        [[ToolCallReady("toolu_1", "echo", {"value": "x"}), ToolCallReady("toolu_2", "echo", {"value": "y"}), Done()]]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry, max_tool_calls_per_turn=1)

    events = await collect_events(agent, "Too many")

    assert isinstance(events[-1], AgentError)
