# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from libre_claw.config import PermissionsConfig
from libre_claw.core.agent import (
    Agent,
    AgentDone,
    AgentError,
    AgentFallback,
    AgentPermissionRequest,
    AgentTextDelta,
    AgentToolCall,
    AgentToolResult,
    MemoryProvider,
    SkillProvider,
)
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import (
    ChatMessage,
    Session,
    UserAttachment,
    image_block,
    text_block,
    tool_result_block,
    tool_use_block,
)
from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolRegistry, ToolResult
from libre_claw.providers.base import (
    Done,
    LLMProvider,
    ProviderError,
    StreamEvent,
    TextDelta,
    ToolCallReady,
    ToolSchema,
    Usage,
)


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


class AttachmentTool(BaseTool):
    name = "attachment"
    description = "Return an image attachment."
    parameters = {}
    permission_level = "allow"

    async def execute(self) -> ToolResult:
        attachment = UserAttachment(
            media_type="image/png",
            data="aGVsbG8=",
            filename="preview.png",
            path="/tmp/preview.png",
        )
        return ToolResult(content="attached", attachments=(attachment,))


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


class DelayTool(BaseTool):
    name = "delay"
    description = "Return a value after a delay."
    parameters = {
        "value": {"type": "string"},
        "delay": {"type": "number"},
    }
    required = ("value", "delay")
    permission_level = "allow"

    async def execute(self, *, value: str, delay: float) -> ToolResult:
        await asyncio.sleep(delay)
        return ToolResult(content=value)


class BlockingProvider(LLMProvider):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, system, stream, temperature, max_tokens
        await asyncio.sleep(60)
        yield TextDelta("late")


def make_agent(
    provider: LLMProvider,
    registry: ToolRegistry | None = None,
    max_tool_calls_per_turn: int = 50,
    system_prompt_extra: str = "",
    skill_provider: SkillProvider | None = None,
    soul_provider=None,
    memory_provider: MemoryProvider | None = None,
    fallback_providers=None,
    fallback_recheck_after_attempts: int = 3,
    provider_retry_attempts: int = 0,
    provider_retry_initial_delay: float = 0.0,
    auto_compact_threshold: float = 0.8,
    context_window_tokens: int = 200000,
    compact_keep_last: int = 8,
    deadline_monotonic: float | None = None,
    deadline_reserve_seconds: float = 0.0,
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
        memory_provider=memory_provider,
        fallback_providers=fallback_providers,
        fallback_recheck_after_attempts=fallback_recheck_after_attempts,
        provider_retry_attempts=provider_retry_attempts,
        provider_retry_initial_delay=provider_retry_initial_delay,
        auto_compact_threshold=auto_compact_threshold,
        context_window_tokens=context_window_tokens,
        compact_keep_last=compact_keep_last,
        deadline_monotonic=deadline_monotonic,
        deadline_reserve_seconds=deadline_reserve_seconds,
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


async def test_agent_injects_relevant_persistent_memory() -> None:
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(provider, memory_provider=lambda message: [f"remembered for {message}"])

    await collect_events(agent, "timezone")

    assert provider.received_system is not None
    assert "Relevant persistent memory:" in provider.received_system
    assert "remembered for timezone" in provider.received_system


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
    assert "AgentSkills-compatible SKILL.md" in provider.received_system
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


async def test_agent_sends_tool_attachments_back_to_provider() -> None:
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "attachment", {}), Done(stop_reason="tool_use")],
            [TextDelta("seen"), Done()],
        ]
    )
    registry = ToolRegistry([AttachmentTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    await collect_events(agent, "Inspect")

    attachment = UserAttachment(
        media_type="image/png",
        data="aGVsbG8=",
        filename="preview.png",
        path="/tmp/preview.png",
    )
    assert agent.session.messages[2] == ChatMessage(
        role="user",
        content=[
            tool_result_block("toolu_1", "attached"),
            image_block(attachment),
        ],
    )
    assert provider.received_messages[1][2] == agent.session.messages[2]


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


async def test_agent_preserves_completed_parallel_tools_at_deadline() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallReady("toolu_fast", "delay", {"value": "fast", "delay": 0.01}),
                ToolCallReady("toolu_slow", "delay", {"value": "slow", "delay": 1.0}),
                Done(stop_reason="tool_use"),
            ],
            [TextDelta("done"), Done()],
        ]
    )
    registry = ToolRegistry([DelayTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(
        provider,
        registry,
        deadline_monotonic=time.monotonic() + 0.2,
        deadline_reserve_seconds=0.08,
    )

    events = await collect_events(agent, "Use two tools")

    results = [event for event in events if isinstance(event, AgentToolResult)]
    assert results[0].result == ToolResult(content="fast")
    assert "stopped to preserve time" in (results[1].result.error or "")


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


async def test_agent_falls_back_when_primary_provider_fails_before_output() -> None:
    primary = ScriptedProvider([[ProviderError("rate limited")]])
    fallback = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(primary, fallback_providers=(("openrouter:backup", fallback),))

    events = await collect_events(agent, "Hi")

    assert events == [
        AgentFallback("openrouter:backup", "rate limited"),
        AgentTextDelta("ok"),
        AgentDone(None),
    ]
    assert len(primary.received_messages) == 1
    assert len(fallback.received_messages) == 1


async def test_agent_tries_multiple_fallbacks_in_order() -> None:
    primary = ScriptedProvider([[ProviderError("primary down")]])
    fallback_1 = ScriptedProvider([[ProviderError("backup 1 down")]])
    fallback_2 = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(
        primary,
        fallback_providers=(("openrouter:backup-1", fallback_1), ("ollama:backup-2", fallback_2)),
    )

    events = await collect_events(agent, "Hi")

    assert events == [
        AgentFallback("openrouter:backup-1", "primary down"),
        AgentFallback(
            "ollama:backup-2",
            "backup 1 down",
            failed_provider_label="openrouter:backup-1",
        ),
        AgentTextDelta("ok"),
        AgentDone(None),
    ]
    assert len(primary.received_messages) == 1
    assert len(fallback_1.received_messages) == 1
    assert len(fallback_2.received_messages) == 1


async def test_agent_rechecks_primary_after_fallback_provider_calls() -> None:
    primary = ScriptedProvider(
        [
            [ProviderError("rate limited")],
            [TextDelta("primary back"), Done()],
        ]
    )
    fallback = ScriptedProvider([[ToolCallReady("toolu_1", "echo", {"value": "ids"}), Done(stop_reason="tool_use")]])
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(
        primary,
        registry,
        fallback_providers=(("openrouter:backup", fallback),),
        fallback_recheck_after_attempts=1,
    )

    events = await collect_events(agent, "Fetch")

    assert events == [
        AgentFallback("openrouter:backup", "rate limited"),
        AgentToolCall(ToolCall(id="toolu_1", name="echo", arguments={"value": "ids"})),
        AgentToolResult(
            ToolCall(id="toolu_1", name="echo", arguments={"value": "ids"}),
            ToolResult(content="echo:ids"),
        ),
        AgentTextDelta("primary back"),
        AgentDone(None),
    ]
    assert len(primary.received_messages) == 2
    assert len(fallback.received_messages) == 1


async def test_agent_falls_back_when_primary_provider_returns_empty_output() -> None:
    primary = ScriptedProvider([[Done(Usage(input_tokens=5, output_tokens=10))]])
    fallback = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(primary, fallback_providers=(("openrouter:backup", fallback),))

    events = await collect_events(agent, "Hi")

    assert events == [
        AgentFallback("openrouter:backup", "Provider returned no assistant text or tool calls."),
        AgentTextDelta("ok"),
        AgentDone(Usage(input_tokens=5, output_tokens=10)),
    ]
    assert len(primary.received_messages) == 1
    assert len(fallback.received_messages) == 1


async def test_agent_retries_empty_provider_output_before_using_fallback() -> None:
    primary = ScriptedProvider(
        [
            [Done(Usage(input_tokens=5, output_tokens=0))],
            [TextDelta("recovered"), Done()],
        ]
    )
    fallback = ScriptedProvider([[TextDelta("fallback"), Done()]])
    agent = make_agent(
        primary,
        fallback_providers=(("openrouter:backup", fallback),),
        provider_retry_attempts=1,
    )

    events = await collect_events(agent, "Hi")

    assert events == [AgentTextDelta("recovered"), AgentDone(Usage(input_tokens=5))]
    assert len(primary.received_messages) == 2
    assert fallback.received_messages == []


async def test_agent_does_not_fallback_after_partial_output() -> None:
    primary = ScriptedProvider([[TextDelta("partial"), ProviderError("down")]])
    fallback = ScriptedProvider([[TextDelta("ok"), Done()]])
    agent = make_agent(primary, fallback_providers=(("openrouter:backup", fallback),))

    events = await collect_events(agent, "Hi")

    assert events == [
        AgentTextDelta("partial"),
        AgentError("down", provider_label="primary"),
    ]
    assert fallback.received_messages == []


async def test_agent_retries_empty_transient_provider_failure_after_tool_result() -> None:
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "echo", {"value": "ids"}), Done(stop_reason="tool_use")],
            [ProviderError("OpenRouter request failed: ReadError('')")],
            [TextDelta("final brief"), Done()],
        ]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(
        provider,
        registry,
        provider_retry_attempts=2,
        provider_retry_initial_delay=0.0,
    )

    events = await collect_events(agent, "Fetch HN")

    assert events == [
        AgentToolCall(ToolCall(id="toolu_1", name="echo", arguments={"value": "ids"})),
        AgentToolResult(
            ToolCall(id="toolu_1", name="echo", arguments={"value": "ids"}),
            ToolResult(content="echo:ids"),
        ),
        AgentTextDelta("final brief"),
        AgentDone(None),
    ]
    assert len(provider.received_messages) == 3
    assert provider.responses == []


async def test_agent_prompt_describes_only_registered_tools() -> None:
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    await collect_events(agent, "Hi")

    assert provider.received_system is not None
    assert "Available tools for this run: echo." in provider.received_system
    assert "skills add" not in provider.received_system.lower()
    assert [schema["name"] for schema in provider.received_tools[0]] == ["echo"]


async def test_agent_compacts_after_provider_reports_large_context_usage() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallReady("toolu_1", "echo", {"value": "ids"}),
                Done(Usage(input_tokens=90, output_tokens=2)),
            ],
            [TextDelta("final"), Done()],
        ]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(
        provider,
        registry,
        auto_compact_threshold=0.8,
        context_window_tokens=100,
        compact_keep_last=2,
    )

    events = await collect_events(agent, "Fetch")

    assert isinstance(events[-1], AgentDone)
    assert agent.session.summary is not None
    assert "user: Fetch" in agent.session.summary
    assert len(provider.received_messages[1]) == 2


async def test_agent_stops_before_provider_call_when_deadline_expired() -> None:
    provider = ScriptedProvider([[TextDelta("too late"), Done()]])
    agent = make_agent(provider, deadline_monotonic=time.monotonic() - 1)

    events = await collect_events(agent, "Hi")

    assert events == [AgentError("Run deadline reached before a final response was produced.")]
    assert provider.received_messages == []


async def test_agent_stops_blocked_provider_stream_before_outer_deadline() -> None:
    agent = make_agent(
        BlockingProvider(),
        deadline_monotonic=time.monotonic() + 0.1,
        deadline_reserve_seconds=0.02,
    )

    events = await asyncio.wait_for(collect_events(agent, "Hi"), timeout=0.5)

    assert events == [AgentError("Run deadline reached before a final response was produced.")]


async def test_agent_retry_backoff_does_not_outlive_deadline() -> None:
    provider = ScriptedProvider(
        [
            [ProviderError("temporary network failure")],
            [TextDelta("too late"), Done()],
        ]
    )
    agent = make_agent(
        provider,
        provider_retry_attempts=1,
        provider_retry_initial_delay=60,
        deadline_monotonic=time.monotonic() + 0.1,
    )

    events = await asyncio.wait_for(collect_events(agent, "Hi"), timeout=0.5)

    assert events == [AgentError("Run deadline reached before a final response was produced.")]
    assert len(provider.received_messages) == 1
