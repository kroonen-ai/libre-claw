# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace

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
    provider_reasoning_block,
    text_block,
    tool_result_block,
    tool_use_block,
)
from libre_claw.core.tools import BaseTool, ToolCall, ToolContext, ToolRegistry, ToolResult
from libre_claw.providers.base import (
    CacheableSystemPrompt,
    Done,
    LLMProvider,
    ProviderError,
    ReasoningDelta,
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
        self.received_systems: list[str | None] = []

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
        self.received_systems.append(system)
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
    read_only = True
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
    read_only = True
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


async def test_agent_retains_provider_reasoning_privately_across_tool_turns() -> None:
    provider = ScriptedProvider(
        [
            [
                ReasoningDelta("inspect privately", provider="moonshot"),
                ToolCallReady("toolu_1", "echo", {"value": "x"}),
                Done(stop_reason="tool_use"),
            ],
            [
                ReasoningDelta("finish privately", provider="moonshot"),
                TextDelta("done"),
                Done(),
            ],
        ]
    )
    registry = ToolRegistry([EchoTool(ToolContext(working_directory=Path.cwd()))])
    agent = make_agent(provider, registry)

    events = await collect_events(agent, "Use a tool")

    assert ReasoningDelta("inspect privately", provider="moonshot") not in events
    assert events[-2:] == [AgentTextDelta("done"), AgentDone(None)]
    assert agent.session.messages[1] == ChatMessage(
        role="assistant",
        content=[
            provider_reasoning_block("inspect privately", "moonshot"),
            tool_use_block("toolu_1", "echo", {"value": "x"}),
        ],
    )
    assert agent.session.messages[-1] == ChatMessage(
        role="assistant",
        content=[
            provider_reasoning_block("finish privately", "moonshot"),
            text_block("done"),
        ],
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


async def test_agent_discards_primary_reasoning_before_fallback() -> None:
    primary = ScriptedProvider(
        [[ReasoningDelta("primary private thought", provider="moonshot"), ProviderError("temporarily unavailable")]]
    )
    fallback = ScriptedProvider([[TextDelta("fallback"), Done()]])
    agent = make_agent(
        primary,
        fallback_providers=(("openrouter:backup", fallback),),
    )

    events = await collect_events(agent, "Hi")

    assert isinstance(events[0], AgentFallback)
    assert events[-2:] == [AgentTextDelta("fallback"), AgentDone(None)]
    assert agent.session.messages[-1] == ChatMessage(
        role="assistant",
        content=[text_block("fallback")],
    )
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


async def test_agent_retains_reasoning_when_partial_text_fails() -> None:
    provider = ScriptedProvider([
        [ReasoningDelta("saved reasoning", provider="deepseek"), TextDelta("partial"), ProviderError("aborted")],
        [TextDelta("resumed"), Done()],
    ])
    agent = make_agent(provider)
    await collect_events(agent, "start")
    await collect_events(agent, "continue")
    assert provider.received_messages[1][1].content == [
        provider_reasoning_block("saved reasoning", "deepseek"), text_block("partial"),
    ]


async def test_agent_retains_explicit_empty_reasoning() -> None:
    provider = ScriptedProvider([[ReasoningDelta("", provider="deepseek"), TextDelta("answer"), Done()]])
    agent = make_agent(provider)
    await collect_events(agent, "start")
    assert agent.session.messages[-1].content == [
        provider_reasoning_block("", "deepseek"), text_block("answer"),
    ]


async def test_agent_retains_partial_reasoning_and_text_at_deadline() -> None:
    class PartialProvider(ScriptedProvider):
        async def complete(self, *args, **kwargs):
            yield ReasoningDelta("private reasoning", provider="deepseek")
            yield TextDelta("partial answer")
            await asyncio.sleep(60)

    agent = make_agent(PartialProvider([]), deadline_monotonic=time.monotonic() + 0.1)
    events = await collect_events(agent, "start")
    assert isinstance(events[-1], AgentError)
    assert "deadline" in events[-1].message
    assert agent.session.messages[-1].content == [
        provider_reasoning_block("private reasoning", "deepseek"), text_block("partial answer"),
    ]


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


async def test_agent_mutations_in_one_batch_run_sequentially(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool
    context = ToolContext(working_directory=tmp_path)
    registry = ToolRegistry([WriteFileTool(context), ReadFileTool(context)])
    provider = ScriptedProvider([
        [ToolCallReady("write", "write_file", {"path": "sample", "content": "fresh"}),
         ToolCallReady("read", "read_file", {"path": "sample"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(provider, registry)
    agent.permission_manager.always_allowed_tools.add("write_file")
    events = await collect_events(agent, "write then read")
    results = [event.result for event in events if isinstance(event, AgentToolResult)]
    assert all(not result.is_error for result in results)
    assert "fresh" in results[1].content


async def test_agent_plan_mode_blocks_preapproved_mutations(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.filesystem import WriteFileTool
    provider = ScriptedProvider([
        [ToolCallReady("write", "write_file", {"path": "sample", "content": "no"}), Done()],
        [TextDelta("plan"), Done()],
    ])
    agent = make_agent(provider, ToolRegistry([WriteFileTool(ToolContext(working_directory=tmp_path))]))
    agent.session.mode = "plan"
    agent.permission_manager.always_allowed_tools.add("write_file")
    events = await collect_events(agent, "plan only")
    assert not (tmp_path / "sample").exists()
    assert any(isinstance(event, AgentToolResult) and "Plan mode" in (event.result.error or "") for event in events)


async def test_agent_loads_nested_instructions_before_editing(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.filesystem import WriteFileTool
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Use a greeting in new files.")
    provider = ScriptedProvider([
        [ToolCallReady("first", "write_file", {"path": "src/new.txt", "content": "bad"}), Done()],
        [ToolCallReady("retry", "write_file", {"path": "src/new.txt", "content": "hello"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(provider, ToolRegistry([WriteFileTool(ToolContext(working_directory=tmp_path))]))
    agent.permission_manager.always_allowed_tools.add("write_file")
    events = await collect_events(agent, "write a file")
    results = [event.result for event in events if isinstance(event, AgentToolResult)]
    assert "project instructions" in (results[0].error or "")
    assert not results[1].is_error
    assert (nested / "new.txt").read_text() == "hello"
    assert "Use a greeting" in (provider.received_system or "")


async def test_agent_applies_steering_after_complete_tool_protocol(tmp_path: Path) -> None:
    class SteeringTool(EchoTool):
        async def execute(self, value: str) -> ToolResult:
            agent.session.queue_steering("Also explain the result")
            return await super().execute(value)
    provider = ScriptedProvider([
        [ToolCallReady("tool", "echo", {"value": "yes"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(provider, ToolRegistry([SteeringTool(ToolContext(working_directory=tmp_path))]))
    await collect_events(agent, "do the work")
    received = provider.received_messages[1]
    assert received[-2].content[0]["type"] == "tool_result"
    assert received[-1].content[0]["text"] == "Also explain the result"
    assert not agent.session.pending_steering


async def test_agent_checkpoint_precedes_side_effects(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.filesystem import WriteFileTool
    provider = ScriptedProvider([[ToolCallReady("write", "write_file", {"path": "sample", "content": "x"}), Done()]])
    agent = make_agent(provider, ToolRegistry([WriteFileTool(ToolContext(working_directory=tmp_path))]))
    agent.permission_manager.always_allowed_tools.add("write_file")
    snapshots = []
    async def checkpoint(session: Session) -> None:
        snapshots.append(list(session.messages))
        if session.messages[-1].content[0]["type"] == "tool_use":
            raise OSError("disk full")
    agent.checkpoint_callback = checkpoint
    import pytest
    with pytest.raises(OSError, match="disk full"):
        await collect_events(agent, "write")
    assert not (tmp_path / "sample").exists()
    assert snapshots


async def test_plan_mode_enforces_native_codex_sandbox_without_mutating_provider(tmp_path: Path) -> None:
    from libre_claw.providers.codex import CodexProvider
    observed = []
    class FakeNativeProvider(CodexProvider):
        async def complete(self, *args, **kwargs):
            observed.append(self.sandbox)
            yield TextDelta("plan")
            yield Done()
    provider = FakeNativeProvider(model="future-model", working_directory=tmp_path)
    agent = make_agent(provider)
    agent.session.mode = "plan"
    await collect_events(agent, "review")
    assert observed == ["read-only"]
    assert provider.sandbox == "workspace-write"


async def test_known_tool_incapable_provider_gets_no_tool_schemas(tmp_path: Path) -> None:
    from types import SimpleNamespace
    provider = ScriptedProvider([[TextDelta("reply"), Done()]])
    provider.model_info = SimpleNamespace(supports_tools=False, context_window_tokens=200000)
    agent = make_agent(provider, ToolRegistry([EchoTool(ToolContext(working_directory=tmp_path))]))
    await collect_events(agent, "talk")
    assert provider.received_tools == [[]]
    assert "No tools are enabled" in provider.received_system


async def test_agent_repairs_tool_protocol_on_next_turn_after_interrupt(tmp_path: Path) -> None:
    session = Session()
    session.add_user_message("do the work")
    session.add_assistant_blocks([tool_use_block("interrupted", "write_file", {"path": "file", "content": "x"})])
    provider = ScriptedProvider([[TextDelta("checked current state"), Done()]])
    agent = make_agent(provider)
    agent.session = session
    await collect_events(agent, "continue")
    messages = provider.received_messages[0]
    assert messages[-2].content[0]["tool_use_id"] == "interrupted"
    assert messages[-2].content[0]["is_error"]
    assert messages[-1].content[0]["text"] == "continue"


async def test_agent_steering_prevents_obsolete_pending_write(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.filesystem import WriteFileTool
    class SteeredProvider(ScriptedProvider):
        async def complete(self, *args, **kwargs):
            if not self.received_messages:
                agent.session.queue_steering("Do not write the file; explain the plan.")
            async for event in super().complete(*args, **kwargs):
                yield event
    provider = SteeredProvider([
        [ToolCallReady("write", "write_file", {"path": "sample", "content": "obsolete"}), Done()],
        [TextDelta("plan"), Done()],
    ])
    agent = make_agent(provider, ToolRegistry([WriteFileTool(ToolContext(working_directory=tmp_path))]))
    agent.permission_manager.always_allowed_tools.add("write_file")
    await collect_events(agent, "write")
    assert not (tmp_path / "sample").exists()
    assert provider.received_messages[1][-2].content[0]["is_error"]
    assert provider.received_messages[1][-1].content[0]["text"] == "Do not write the file; explain the plan."


async def test_agent_discovers_selected_model_once_before_requests(tmp_path: Path) -> None:
    from types import SimpleNamespace
    provider = ScriptedProvider([
        [ToolCallReady("echo", "echo", {"value": "ok"}), Done()],
        [TextDelta("done"), Done()],
    ])
    calls = []
    async def discover():
        calls.append(True)
        provider.model_info = SimpleNamespace(supports_tools=True, context_window_tokens=200000)
        return provider.model_info
    provider.ensure_model_info = discover
    agent = make_agent(provider, ToolRegistry([EchoTool(ToolContext(working_directory=tmp_path))]))
    await collect_events(agent, "work")
    assert calls == [True]
    assert len(provider.received_messages) == 2


async def test_agent_can_run_when_model_discovery_is_unavailable() -> None:
    provider = ScriptedProvider([[TextDelta("done"), Done()]])
    async def discover():
        raise RuntimeError("catalog offline")
    provider.ensure_model_info = discover
    agent = make_agent(provider)
    events = await collect_events(agent, "work")
    assert events[-1] == AgentDone(None)


async def test_tool_rounds_keep_cached_prefix_when_deadline_time_advances(tmp_path: Path, monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("libre_claw.core.agent.time", SimpleNamespace(monotonic=lambda: now[0]))

    class AdvancingTool(EchoTool):
        async def execute(self, value: str) -> ToolResult:
            now[0] += 15
            return await super().execute(value)

    provider = ScriptedProvider([
        [ToolCallReady("echo", "echo", {"value": "ok"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(
        provider,
        ToolRegistry([AdvancingTool(ToolContext(working_directory=tmp_path))]),
        deadline_monotonic=160.0,
    )

    await collect_events(agent, "work")

    assert provider.received_systems[0] == provider.received_systems[1]
    assert "starting time budget was about 60 seconds" in provider.received_systems[0]
    assert provider.received_messages[1][:len(provider.received_messages[0])] == provider.received_messages[0]
    assert agent._remaining_seconds() == 45.0
    now[0] = 161.0
    assert agent._deadline_expired()


async def test_checkpoint_changes_preserve_durable_prompt_prefix(tmp_path: Path) -> None:
    class CheckpointTool(EchoTool):
        async def execute(self, value: str) -> ToolResult:
            agent.session.update_checkpoint({"verification": ["Tests passed."]})
            agent.session.plan_steps = [{"status": "complete", "text": "Run tests"}]
            return await super().execute(value)

    (tmp_path / "AGENTS.md").write_text("Keep the documented API compatible.")
    provider = ScriptedProvider([
        [ToolCallReady("echo", "echo", {"value": "ok"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(
        provider,
        ToolRegistry([CheckpointTool(ToolContext(working_directory=tmp_path))]),
        skill_provider=lambda _: ["Verify the public API."],
        soul_provider=lambda: ["Be precise."],
        memory_provider=lambda _: ["Use concise responses."],
    )
    agent.session.plan_steps = [{"status": "in_progress", "text": "Run tests"}]

    await collect_events(agent, "work")

    before, after = provider.received_systems
    assert isinstance(before, CacheableSystemPrompt)
    assert isinstance(after, CacheableSystemPrompt)
    assert before.cache_prefix == after.cache_prefix
    assert "Keep the documented API compatible." in before.cache_prefix
    assert "Be precise." in before.cache_prefix
    assert "Verify the public API." in before.cache_prefix
    assert "Relevant persistent memory:" not in before.cache_prefix
    assert "Task plan:" not in before.cache_prefix
    assert "Task checkpoint:" not in before.cache_prefix
    assert before.split("Task plan:\n")[0] == after.split("Task plan:\n")[0]
    assert "Keep the documented API compatible." in before.split("Task plan:\n")[0]
    assert "Relevant persistent memory:" in before.split("Task plan:\n")[0]
    assert "Tests passed." not in before
    assert "Tests passed." in after
    assert "[in_progress] Run tests" in before
    assert "[complete] Run tests" in after


async def test_memory_refresh_preserves_tool_and_skill_prefix_across_turns() -> None:
    provider = ScriptedProvider([[TextDelta("first"), Done()], [TextDelta("second"), Done()]])
    agent = make_agent(
        provider,
        skill_provider=lambda _: ["Use the relevant tests."],
        memory_provider=lambda message: [f"Preference for {message}"],
    )

    await collect_events(agent, "task one")
    await collect_events(agent, "task two")

    before, after = provider.received_systems
    assert isinstance(before, CacheableSystemPrompt)
    assert isinstance(after, CacheableSystemPrompt)
    assert before.cache_prefix == after.cache_prefix
    assert before.cache_scope == after.cache_scope
    assert before.split("Relevant persistent memory:\n")[0] == after.split("Relevant persistent memory:\n")[0]
    assert "Preference for task one" in before
    assert "Preference for task two" in after
    assert "Preference for task one" not in after
    assert "Relevant Libre Claw skills" in before.split("Relevant persistent memory:\n")[0]


async def test_cache_stability_does_not_hide_changed_project_instructions(tmp_path: Path) -> None:
    instruction_path = tmp_path / "AGENTS.md"
    instruction_path.write_text("First instruction.")

    class ChangingInstructionsTool(EchoTool):
        async def execute(self, value: str) -> ToolResult:
            instruction_path.write_text("Updated instruction.")
            return await super().execute(value)

    provider = ScriptedProvider([
        [ToolCallReady("echo", "echo", {"value": "ok"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(provider, ToolRegistry([ChangingInstructionsTool(ToolContext(working_directory=tmp_path))]))

    await collect_events(agent, "work")

    before, after = provider.received_systems
    assert "First instruction." in before
    assert "Updated instruction." in after
    assert "First instruction." not in after


async def test_tool_schema_order_is_canonical_in_requests_and_estimates(tmp_path: Path) -> None:
    class ReorderedRegistry(ToolRegistry):
        calls = 0

        def schemas(self):
            self.calls += 1
            schemas = super().schemas()
            if self.calls % 2:
                return [dict(reversed(list(schema.items()))) for schema in reversed(schemas)]
            return schemas

    context = ToolContext(working_directory=tmp_path)
    registry = ReorderedRegistry([EchoTool(context), AskTool(context)])
    provider = ScriptedProvider([
        [ToolCallReady("echo", "echo", {"value": "ok"}), Done()],
        [TextDelta("done"), Done()],
    ])
    agent = make_agent(provider, registry)

    await collect_events(agent, "work")

    before, after = [json.dumps(tools, separators=(",", ":")) for tools in provider.received_tools]
    assert before == after == agent._serialized_tool_schemas
    assert [schema["name"] for schema in provider.received_tools[0]] == ["ask_echo", "echo"]


async def test_discovered_tool_capability_updates_prompt_estimate(tmp_path: Path) -> None:
    class VerboseTool(EchoTool):
        description = "Detailed tool guidance. " * 1000

    provider = ScriptedProvider([[TextDelta("reply"), Done()]])
    agent = make_agent(
        provider,
        ToolRegistry([VerboseTool(ToolContext(working_directory=tmp_path))]),
        context_window_tokens=1000,
        compact_keep_last=2,
    )
    for _ in range(5):
        agent.session.add_user_message("Earlier request.")
        agent.session.add_assistant_message("Earlier response.")

    async def discover():
        provider.model_info = SimpleNamespace(supports_tools=False)

    provider.ensure_model_info = discover
    assert "echo" in agent._serialized_tool_schemas

    await collect_events(agent, "talk")

    assert provider.received_tools == [[]]
    assert agent._serialized_tool_schemas == "[]"
    assert "No tools are enabled" in provider.received_system
    assert agent.session.summary is None
    assert len(provider.received_messages[0]) == 11


def test_prompt_cache_scope_survives_compaction_and_agent_recreation() -> None:
    agent = make_agent(ScriptedProvider([]))
    empty = agent.resolved_system_prompt()
    assert isinstance(empty, CacheableSystemPrompt)
    assert empty.cache_scope is None
    agent.session.add_user_message("Original task")
    for _ in range(5):
        agent.session.add_assistant_message("Earlier response")
        agent.session.add_user_message("Follow-up")
    before = agent.resolved_system_prompt()

    agent.session.compact(keep_last=2)
    after = agent.resolved_system_prompt()
    restored = make_agent(ScriptedProvider([]))
    restored.session = agent.session
    recreated = restored.resolved_system_prompt()

    assert agent.session.archived_messages
    assert before != after
    assert before.cache_prefix == after.cache_prefix == recreated.cache_prefix
    assert before.cache_scope == after.cache_scope == recreated.cache_scope
    assert len(before.cache_scope) == 64
    assert "Original task" not in before.cache_scope

    agent.session.clear()
    assert agent.resolved_system_prompt().cache_scope is None
    agent.session.add_user_message("A different task")
    assert agent.resolved_system_prompt().cache_scope != before.cache_scope


def test_prompt_cache_boundary_preserves_complete_prompt_text() -> None:
    agent = make_agent(ScriptedProvider([]))
    agent._active_memory = ["Use concise responses."]
    agent.session.summary = "Earlier context."
    agent.session.add_user_message("Original task")

    prompt = agent.resolved_system_prompt()

    assert isinstance(prompt, CacheableSystemPrompt)
    assert prompt.cache_prefix == "test system\n\nNo tools are enabled for this run."
    assert str(prompt) == "\n\n".join([
        prompt.cache_prefix,
        "Relevant persistent memory:\n- Use concise responses.",
        "Compacted prior conversation summary:\nEarlier context.",
        agent.session.control_prompt(),
    ])
    # Native/plain-string providers and trajectory JSON still get all context.
    assert json.loads(json.dumps({"system": prompt}))["system"] == str(prompt)
