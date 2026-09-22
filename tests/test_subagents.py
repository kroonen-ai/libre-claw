# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from libre_claw.config import PermissionsConfig
from libre_claw.core.agent import Agent, AgentPermissionRequest, AgentSubagentUpdate
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolCall, ToolContext, ToolRegistry
from libre_claw.providers.base import Done, LLMProvider, TextDelta, ToolCallReady
from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool
from libre_claw.tools_builtin.subagents import SubagentSpawnTool, SubagentListTool, SubagentWaitTool, SubagentCancelTool, TaskCheckpointTool


class WorkerProvider(LLMProvider):
    def __init__(self, responses=None, delay=0):
        self.responses = responses or [[TextDelta("worker result"), Done()]]
        self.delay = delay
        self.messages = []

    async def complete(self, messages, **kwargs):
        self.messages.append(list(messages))
        await asyncio.sleep(self.delay)
        for event in self.responses.pop(0):
            yield event


def make_parent(tmp_path: Path, worker: LLMProvider, provider=None, **context_kwargs) -> Agent:
    context = ToolContext(
        working_directory=tmp_path, default_provider="example", default_model="arbitrary-model-id",
        subagent_provider_factory=lambda provider, model, scope, read_only: worker,
        **context_kwargs,
    )
    tools = [kind(context) for kind in (
        ReadFileTool, WriteFileTool, SubagentSpawnTool, SubagentListTool,
        SubagentWaitTool, SubagentCancelTool, TaskCheckpointTool,
    )]
    return Agent(
        session=Session(), provider=provider or WorkerProvider(), tool_registry=ToolRegistry(tools),
        permission_manager=PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True)), system_prompt="test system",
    )


async def test_subagents_separate_context_and_accept_arbitrary_model(tmp_path: Path) -> None:
    worker = WorkerProvider()
    parent = make_parent(tmp_path, worker)
    parent.session.add_user_message("private parent transcript")
    state = await parent.subagents.spawn(task="inspect the project", scope=".", model="vendor/future-model")
    snapshots = await parent.subagents.wait([state["id"]], 1)
    assert snapshots[0]["status"] == "done"
    assert snapshots[0]["model"] == "vendor/future-model"
    assert snapshots[0]["output"] == "worker result"
    assert len(worker.messages[0]) == 1
    assert worker.messages[0][0].content[0]["text"] == "inspect the project"
    assert len(parent.session.messages) == 1


async def test_read_only_worker_cannot_write_even_with_parent_approval(tmp_path: Path) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "forbidden.txt", "content": "no"}), Done()],
        [TextDelta("cannot write"), Done()],
    ])
    parent = make_parent(tmp_path, worker)
    parent.permission_manager.always_allowed_tools.add("write_file")
    state = await parent.subagents.spawn(task="inspect", scope=".")
    await parent.subagents.wait([state["id"]], 1)
    assert not (tmp_path / "forbidden.txt").exists()
    assert "Unknown tool" in worker.messages[1][-1].content[0]["content"]


async def test_write_worker_requires_per_call_permission(tmp_path: Path) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "allowed.txt", "content": "yes"}), Done()],
        [TextDelta("finished"), Done()],
    ])
    parent_provider = WorkerProvider([
        [ToolCallReady("spawn", "subagent_spawn", {"task": "write allowed.txt", "scope": ".", "read_only": False, "write_paths": ["allowed.txt"]}), Done()],
        [ToolCallReady("wait", "subagent_wait", {"timeout": 1}), Done()],
        [TextDelta("parent finished"), Done()],
    ])
    parent = make_parent(tmp_path, worker, parent_provider)
    events = []
    async for event in parent.run("delegate the file"):
        events.append(event)
        if isinstance(event, AgentPermissionRequest):
            assert event.call.name == "write_file"
            assert ":write" in event.call.id
            assert not (tmp_path / "allowed.txt").exists()
            event.future.set_result("allow_once")
    assert (tmp_path / "allowed.txt").read_text() == "yes"
    assert any(isinstance(event, AgentSubagentUpdate) and event.snapshot["status"] == "done" for event in events)


async def test_denied_child_permission_does_not_write(tmp_path: Path) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "allowed.txt", "content": "no"}), Done()],
        [TextDelta("denied"), Done()],
    ])
    parent = make_parent(tmp_path, worker)
    state = await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["allowed.txt"])
    while True:
        event = await asyncio.wait_for(parent.subagents.events.get(), 1)
        if isinstance(event, AgentPermissionRequest):
            event.future.set_result("deny")
            break
    await parent.subagents.wait([state["id"]], 1)
    assert not (tmp_path / "allowed.txt").exists()


async def test_worker_write_paths_and_parent_ownership(tmp_path: Path) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "outside.txt", "content": "no"}), Done()],
        [TextDelta("done"), Done()],
    ], delay=0.01)
    parent = make_parent(tmp_path, worker)
    parent.permission_manager.always_allowed_tools.add("write_file")
    state = await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["owned.txt"])
    with pytest.raises(ValueError, match="overlaps"):
        await parent.subagents.spawn(task="conflict", scope=".", read_only=False, write_paths=["owned.txt"])
    error = parent.subagents.ownership_error(ToolCall("parent", "write_file", {"path": "owned.txt", "content": "x"}))
    assert state["id"] in error
    assert parent.subagents.ownership_error(ToolCall("parent", "write_file", {"path": "separate.txt", "content": "x"})) is None
    await parent.subagents.wait([state["id"]], 1)
    assert not (tmp_path / "outside.txt").exists()
    assert "outside" in worker.messages[1][-1].content[0]["content"]


async def test_worker_rejects_symlink_escape(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "link").symlink_to(tmp_path)
    parent = make_parent(tmp_path, WorkerProvider())
    with pytest.raises(ValueError, match="inside the assigned scope"):
        await parent.subagents.spawn(task="write", scope="scope", read_only=False, write_paths=["link/file"])


async def test_subagent_budgets_cancellation_and_plan_mode(tmp_path: Path) -> None:
    parent = make_parent(tmp_path, WorkerProvider(delay=10), subagent_max_concurrent=1)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await parent.subagents.spawn(task="read", scope=".", max_tool_calls=101)
    parent.session.mode = "plan"
    with pytest.raises(ValueError, match="Plan mode"):
        await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["one"])
    state = await parent.subagents.spawn(task="read", scope=".")
    with pytest.raises(ValueError, match="slots are busy"):
        await parent.subagents.spawn(task="another", scope=".")
    cancelled = await parent.subagents.cancel(state["id"])
    assert cancelled["status"] == "cancelled"
    assert parent.subagents.states[state["id"]].task_handle.done()


async def test_parent_end_cancels_unfinished_workers(tmp_path: Path) -> None:
    provider = WorkerProvider([
        [ToolCallReady("spawn", "subagent_spawn", {"task": "inspect", "scope": "."}), Done()],
        [TextDelta("done"), Done()],
    ])
    parent = make_parent(tmp_path, WorkerProvider(delay=10), provider)
    async for _ in parent.run("work"):
        pass
    assert parent.subagents.snapshots()[0]["status"] == "cancelled"


async def test_task_checkpoint_updates_structured_session(tmp_path: Path) -> None:
    parent = make_parent(tmp_path, WorkerProvider())
    result = await parent.tool_registry.execute(ToolCall("checkpoint", "task_checkpoint", {
        "requirements": ["preserve API"], "decisions": ["use existing service"], "outstanding": ["run integration checks"],
    }))
    assert not result.is_error
    assert parent.session.checkpoint["outstanding"] == ["run integration checks"]
    assert "use existing service" in parent.session.control_prompt()


async def test_task_history_recovers_exact_long_constraint_after_compaction(tmp_path: Path) -> None:
    from libre_claw.tools_builtin.subagents import TaskHistoryTool
    from libre_claw.core.session import UserAttachment
    parent = make_parent(tmp_path, WorkerProvider())
    parent.tool_registry.register(TaskHistoryTool(parent.tool_registry.context))
    exact_constraint = "PRESERVE the regional tax rounding mode exactly: half-even with precision 7."
    original = "Introduction. " * 700 + exact_constraint + " Additional context." * 300
    parent.session.add_user_message(original, attachments=[UserAttachment(media_type="image/png", data="PRIVATE_BASE64_PAYLOAD", filename="reference.png")])
    for _ in range(8):
        parent.session.add_assistant_message("progress")
        parent.session.add_user_message("continue")
    parent.session.compact(keep_last=2, max_summary_chars=500)
    assert exact_constraint not in (parent.session.summary or "")
    result = await parent.tool_registry.execute(ToolCall("history", "task_history", {"query": "regional tax rounding", "max_chars": 800}))
    assert not result.is_error
    history = result.metadata["history"]
    assert history["total_matches"] == 1
    assert history["messages"][0]["archived"]
    assert exact_constraint in history["messages"][0]["text"]
    assert "PRIVATE_BASE64_PAYLOAD" not in result.content
    continuation = await parent.tool_registry.execute(ToolCall("more", "task_history", {"message_index": 0, "offset": len(original), "max_chars": 300}))
    assert "reference.png" in continuation.content
    assert "PRIVATE_BASE64_PAYLOAD" not in continuation.content
    page = await parent.tool_registry.execute(ToolCall("page", "task_history", {"page": 1, "page_size": 2}))
    assert len(page.metadata["history"]["messages"]) == 2
    assert page.metadata["history"]["next_page"] == 2
