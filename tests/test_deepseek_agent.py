# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from libre_claw.config import PermissionsConfig, load_config
from libre_claw.core.agent import Agent, AgentDone, AgentError, AgentPermissionRequest, AgentTextDelta, AgentToolResult
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session, provider_reasoning_block
from libre_claw.core.tools import ToolContext, ToolRegistry
from libre_claw.providers.deepseek import DeepSeekProvider
from libre_claw.providers.factory import create_provider
from libre_claw.providers.model_catalog import ModelInfo
from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool
from libre_claw.tools_builtin.subagents import SubagentSpawnTool


class ScriptedStream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in self.chunks:
            yield chunk

    async def close(self) -> None:
        self.closed = True


class ScriptedClient:
    def __init__(self, responses: list[list[object]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.streams: list[ScriptedStream] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request: Any) -> ScriptedStream:
        self.requests.append(request)
        stream = ScriptedStream(self.responses.pop(0))
        self.streams.append(stream)
        return stream


def chunk(
    *, text: str | None = None, reasoning: str | None = None,
    calls: list[object] | None = None, finish: str | None = None,
    usage: object | None = None,
) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text, reasoning_content=reasoning, tool_calls=calls),
            finish_reason=finish,
        )],
        usage=usage,
    )


def tool_response(name: str, arguments: str, *, reasoning: str = "Private tool reasoning.") -> list[object]:
    split = max(1, len(arguments) // 2)
    return [
        chunk(reasoning=reasoning),
        chunk(calls=[SimpleNamespace(
            index=0, id="call-1", function=SimpleNamespace(name=name, arguments=arguments[:split]),
        )]),
        chunk(calls=[SimpleNamespace(
            index=0, id=None, function=SimpleNamespace(name=None, arguments=arguments[split:]),
        )], finish="tool_calls"),
    ]


def make_agent(
    client: ScriptedClient, session: Session, registry: ToolRegistry, *, checkpoint=None,
) -> Agent:
    return Agent(
        session=session,
        provider=DeepSeekProvider(api_key="test-key", model="deepseek-future", max_tokens=4096, client=client),
        tool_registry=registry,
        permission_manager=PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True)),
        system_prompt="Test the requested coding task.",
        checkpoint_callback=checkpoint,
    )


async def test_deepseek_tool_loop_and_durable_followup_preserve_all_reasoning(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Expected repository content.", encoding="utf-8")
    context = ToolContext(working_directory=tmp_path)
    registry = ToolRegistry([ReadFileTool(context)])
    client = ScriptedClient([
        tool_response("read_file", '{"path":"README.md"}'),
        [chunk(reasoning="Private final reasoning.", text="I read the file.", finish="stop")],
    ])
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Read the file", kind="chat", provider="deepseek", model="deepseek-future")

    async def checkpoint(session: Session) -> None:
        await store.save_session(run.run_id, session)

    agent = make_agent(client, Session(), registry, checkpoint=checkpoint)
    events = [event async for event in agent.run("Read README.md.")]

    assert isinstance(events[-1], AgentDone)
    assert not any(isinstance(event, AgentError) for event in events)
    results = [event for event in events if isinstance(event, AgentToolResult)]
    assert len(results) == 1 and "Expected repository content." in results[0].result.content
    assert "".join(event.text for event in events if isinstance(event, AgentTextDelta)) == "I read the file."
    exchange = client.requests[1]["messages"]
    assistant = next(message for message in exchange if message["role"] == "assistant")
    assert assistant["reasoning_content"] == "Private tool reasoning."
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert exchange[-1]["role"] == "tool" and exchange[-1]["tool_call_id"] == "call-1"
    assert "Expected repository content." in exchange[-1]["content"]
    assert all(stream.closed for stream in client.streams)

    restored = await RunStore(tmp_path / "runs").load_session(run.run_id)
    assert restored.messages == agent.session.messages
    assert restored.messages[-1].content[0] == provider_reasoning_block("Private final reasoning.", "deepseek")
    resumed_client = ScriptedClient([[chunk(reasoning="Private follow-up reasoning.", text="Still remembered.", finish="stop")]])
    resumed = make_agent(resumed_client, restored, registry)
    followup_events = [event async for event in resumed.run("What did the file say?")]

    assert isinstance(followup_events[-1], AgentDone)
    assistants = [message for message in resumed_client.requests[0]["messages"] if message["role"] == "assistant"]
    assert [message["reasoning_content"] for message in assistants] == [
        "Private tool reasoning.", "Private final reasoning.",
    ]
    assert resumed_client.requests[0]["tools"][0]["function"]["name"] == "read_file"


async def test_deepseek_interrupted_tool_resumes_with_original_reasoning_and_unknown_result(tmp_path: Path) -> None:
    registry = ToolRegistry([WriteFileTool(ToolContext(working_directory=tmp_path))])
    client = ScriptedClient([tool_response("write_file", '{"path":"change.txt","content":"change"}')])
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Write a file", kind="chat", provider="deepseek", model="deepseek-future")

    async def checkpoint(session: Session) -> None:
        await store.save_session(run.run_id, session)

    agent = make_agent(client, Session(), registry, checkpoint=checkpoint)
    turn = agent.run("Write change.txt.")
    async for event in turn:
        if isinstance(event, AgentPermissionRequest):
            event.future.cancel()
            break
    else:
        raise AssertionError("The write must request permission before execution.")
    await turn.aclose()
    assert not (tmp_path / "change.txt").exists()

    restored = await RunStore(tmp_path / "runs").load_session(run.run_id, recover=True)
    resumed_client = ScriptedClient([[chunk(reasoning="Private recovery reasoning.", text="I will inspect before retrying.", finish="stop")]])
    resumed = make_agent(resumed_client, restored, registry)
    events = [event async for event in resumed.run("Check the interrupted operation.")]

    assert isinstance(events[-1], AgentDone)
    messages = resumed_client.requests[0]["messages"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["reasoning_content"] == "Private tool reasoning."
    results = [message for message in messages if message["role"] == "tool"]
    assert len(results) == 1 and results[0]["tool_call_id"] == "call-1"
    assert "Completion is unknown" in results[0]["content"]
    assert not (tmp_path / "change.txt").exists()


async def test_deepseek_factory_provider_runs_as_scoped_worker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    workspace = tmp_path / "worker"
    workspace.mkdir()
    (workspace / "README.md").write_text("Scoped content.", encoding="utf-8")
    client = ScriptedClient([
        tool_response("read_file", '{"path":"README.md"}'),
        [chunk(reasoning="Private worker conclusion.", text="Scoped file verified.", finish="stop")],
    ])
    monkeypatch.setattr("libre_claw.providers.openai.AsyncOpenAI", lambda **kwargs: client)

    async def metadata(config, provider, model, **kwargs):
        return ModelInfo(provider, model, model)

    monkeypatch.setattr("libre_claw.providers.model_catalog.discover_model", metadata)
    config = load_config()
    created = []

    def worker_provider(provider, model, scope, read_only):
        result = create_provider(config, provider_name=provider, model=model)
        created.append((result, scope, read_only))
        return result

    context = ToolContext(
        working_directory=tmp_path, default_provider="deepseek", default_model="deepseek-future",
        subagent_provider_factory=worker_provider,
    )
    parent = make_agent(ScriptedClient([]), Session(), ToolRegistry([
        ReadFileTool(context), WriteFileTool(context), SubagentSpawnTool(context),
    ]))
    parent.session.add_user_message("Private parent transcript.")
    state = await parent.subagents.spawn(task="Read README.md and verify it.", scope="worker")
    snapshots = await parent.subagents.wait([state["id"]], 2)

    assert snapshots[0]["status"] == "done", snapshots[0]["error"]
    assert snapshots[0]["output"] == "Scoped file verified."
    assert snapshots[0]["provider"] == "deepseek" and snapshots[0]["model"] == "deepseek-future"
    assert isinstance(created[0][0], DeepSeekProvider)
    assert created[0][1] == workspace and created[0][2] is True
    assert "Private parent transcript." not in str(client.requests)
    assert {tool["function"]["name"] for tool in client.requests[0]["tools"]} == {"read_file"}
    assert "Scoped content." in client.requests[1]["messages"][-1]["content"]
    await parent.subagents.close()
