# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from libre_claw.core.agent import Agent, AgentPermissionRequest
from libre_claw.core.session import Session, session_from_payload, session_to_payload
from libre_claw.core.subagents import SubagentState
from libre_claw.providers.base import Done, TextDelta, ToolCallReady, Usage
from libre_claw.tools_builtin.subagents import SubagentResumeTool
from test_subagents import WorkerProvider, make_parent


def restored_parent(tmp_path: Path, payload: dict, worker: WorkerProvider) -> Agent:
    template = make_parent(tmp_path, worker)
    template.tool_registry.register(SubagentResumeTool(template.tool_registry.context))
    return Agent(
        session=session_from_payload(payload), provider=WorkerProvider(),
        tool_registry=template.tool_registry, permission_manager=template.permission_manager,
        system_prompt="test system",
    )


def interrupted_payload(tmp_path: Path, *, read_only=True, **kwargs) -> tuple[dict, str]:
    state = SubagentState(
        id="recoverable", task="finish assigned work", scope=tmp_path,
        read_only=read_only, write_paths=() if read_only else (tmp_path / "owned.txt",),
        provider="example", model="arbitrary-model-id", max_tool_calls=5, max_seconds=30,
        status="running", **kwargs,
    )
    state.session.add_user_message("finish assigned work")
    session = Session()
    session.subagents[state.id] = state.durable_snapshot()
    return json.loads(json.dumps(session_to_payload(session))), state.id


async def test_worker_checkpoint_survives_restart_without_replaying_unknown_write(tmp_path: Path) -> None:
    saved: list[dict] = []
    parent = make_parent(tmp_path, WorkerProvider([
        [ToolCallReady("pending-write", "write_file", {"path": "owned.txt", "content": "1"}), Done(Usage(3, 2))],
    ]))

    async def checkpoint(session: Session) -> None:
        saved.append(json.loads(json.dumps(session_to_payload(session))))

    parent.checkpoint_callback = checkpoint
    spawned = await parent.subagents.spawn(task="write one", scope=".", read_only=False, write_paths=["owned.txt"])
    while True:
        event = await asyncio.wait_for(parent.subagents.events.get(), 1)
        if isinstance(event, AgentPermissionRequest):
            break
    durable = copy.deepcopy(saved[-1])
    await parent.subagents.cancel(spawned["id"])
    # A crashed process may have executed a write whose result was never saved.
    (tmp_path / "owned.txt").write_text("1")
    worker = WorkerProvider([
        [ToolCallReady("inspect", "read_file", {"path": "owned.txt"}), Done(Usage(4, 1))],
        [TextDelta("The requested write already completed; preserved it."), Done(Usage(5, 2))],
    ])
    restored = restored_parent(tmp_path, durable, worker)
    state = restored.subagents.states[spawned["id"]]
    assert state.status == "interrupted"
    assert state.task_handle is None
    assert worker.messages == []
    assert state.tool_calls == 1
    assert state.usage == Usage(3, 2)
    assert state.session.messages[-1].content[0]["tool_use_id"] == "pending-write"
    assert "Completion is unknown" in state.session.messages[-1].content[0]["content"]
    await restored.subagents.resume(state.id, "Do not overwrite work that already completed.")
    result = (await restored.subagents.wait([state.id], 1))[0]
    assert result["status"] == "done"
    assert result["tool_calls"] == 2
    assert result["usage"]["input_tokens"] == 12
    assert restored.subagents.total_usage().input_tokens == 9
    assert "Do not overwrite" in worker.messages[0][-1].content[0]["text"]
    assert (tmp_path / "owned.txt").read_text() == "1"
    final = json.loads(json.dumps(session_to_payload(restored.session)))
    after_completion = restored_parent(tmp_path, final, WorkerProvider())
    assert after_completion.subagents.states[state.id].status == "done"
    with pytest.raises(ValueError, match="Only interrupted"):
        await after_completion.subagents.resume(state.id)


async def test_child_checkpoints_cover_tool_request_and_result_before_completion(tmp_path: Path) -> None:
    saved: list[dict] = []
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "owned.txt", "content": "saved"}), Done(Usage(7, 2))],
        [TextDelta("finished"), Done(Usage(3, 1))],
    ])
    parent = make_parent(tmp_path, worker)
    parent.permission_manager.always_allowed_tools.add("write_file")

    async def checkpoint(session: Session) -> None:
        saved.append(json.loads(json.dumps(session_to_payload(session))))
        for value in session.subagents.values():
            blocks = [block for message in value["session"]["messages"] for block in message["content"]]
            requests = [block for block in blocks if block.get("type") == "tool_use"]
            results = [block for block in blocks if block.get("type") == "tool_result"]
            if requests and not results:
                assert not (tmp_path / "owned.txt").exists()

    parent.checkpoint_callback = checkpoint
    spawned = await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["owned.txt"])
    await parent.subagents.wait([spawned["id"]], 1)
    assert (tmp_path / "owned.txt").read_text() == "saved"
    intermediate = [entry["subagents"][spawned["id"]] for entry in saved]
    assert any(
        item["status"] == "running" and any(block.get("type") == "tool_result" for message in item["session"]["messages"] for block in message["content"])
        for item in intermediate
    )
    assert intermediate[-1]["status"] == "done"
    assert intermediate[-1]["usage"]["input_tokens"] == 10


async def test_resume_uses_remaining_tool_and_time_budgets(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path, tool_calls=4, elapsed_seconds=29.95)
    worker = WorkerProvider(delay=0.2)
    parent = restored_parent(tmp_path, payload, worker)
    await parent.subagents.resume(worker_id)
    snapshot = (await parent.subagents.wait([worker_id], 1))[0]
    assert snapshot["status"] == "failed"
    assert "deadline" in snapshot["error"].lower()
    assert snapshot["elapsed_seconds"] >= 29.95
    exhausted, worker_id = interrupted_payload(tmp_path, tool_calls=5)
    parent = restored_parent(tmp_path, exhausted, WorkerProvider())
    with pytest.raises(ValueError, match="tool-call budget"):
        await parent.subagents.resume(worker_id)


async def test_resumed_worker_requires_new_permission_and_revalidates_ownership(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path, read_only=False)
    worker = WorkerProvider([
        [ToolCallReady("new-write", "write_file", {"path": "owned.txt", "content": "yes"}), Done()],
        [TextDelta("denied"), Done()],
    ])
    parent = restored_parent(tmp_path, payload, worker)
    parent.session.mode = "plan"
    with pytest.raises(ValueError, match="Plan mode"):
        await parent.subagents.resume(worker_id)
    parent.session.mode = "default"
    await parent.subagents.resume(worker_id)
    with pytest.raises(ValueError, match="overlaps"):
        await parent.subagents.spawn(task="conflict", scope=".", read_only=False, write_paths=["owned.txt"])
    while True:
        event = await asyncio.wait_for(parent.subagents.events.get(), 1)
        if isinstance(event, AgentPermissionRequest):
            assert not (tmp_path / "owned.txt").exists()
            event.future.set_result("deny")
            break
    await parent.subagents.wait([worker_id], 1)
    assert not (tmp_path / "owned.txt").exists()


async def test_resume_rejects_scope_changed_to_symlink_outside_workspace(tmp_path: Path) -> None:
    directory = tmp_path / "scope"
    directory.mkdir()
    payload, worker_id = interrupted_payload(directory)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    directory.rmdir()
    directory.symlink_to(outside)
    parent = restored_parent(tmp_path, payload, WorkerProvider())
    with pytest.raises(ValueError):
        await parent.subagents.resume(worker_id)


async def test_failed_cancelled_and_finished_workers_do_not_restart(tmp_path: Path) -> None:
    for status in ("failed", "cancelled", "done"):
        payload, worker_id = interrupted_payload(tmp_path)
        payload["subagents"][worker_id]["status"] = status
        provider = WorkerProvider()
        parent = restored_parent(tmp_path, payload, provider)
        assert parent.subagents.states[worker_id].status == status
        with pytest.raises(ValueError, match="Only interrupted"):
            await parent.subagents.resume(worker_id)
        assert provider.messages == []
    payload, worker_id = interrupted_payload(tmp_path)
    parent = restored_parent(tmp_path, payload, WorkerProvider())
    assert (await parent.subagents.cancel(worker_id))["status"] == "cancelled"


async def test_parent_and_parallel_child_saves_are_serialized(tmp_path: Path) -> None:
    parent = make_parent(tmp_path, WorkerProvider([
        [TextDelta("worker one"), Done()], [TextDelta("worker two"), Done()],
    ], delay=0.03))
    active_saves = 0
    maximum_saves = 0
    async def checkpoint(session: Session) -> None:
        nonlocal active_saves, maximum_saves
        active_saves += 1
        maximum_saves = max(maximum_saves, active_saves)
        await asyncio.sleep(0.002)
        json.dumps(session_to_payload(session))
        active_saves -= 1
    parent.checkpoint_callback = checkpoint
    await asyncio.gather(
        parent.subagents.spawn(task="one", scope="."), parent.subagents.spawn(task="two", scope="."),
        parent._checkpoint(), parent._checkpoint(),
    )
    await parent.subagents.wait(timeout=1)
    await parent.subagents.wait(timeout=1)
    assert maximum_saves == 1
    assert len(parent.session.subagents) == 2
    assert all(value["status"] == "done" for value in parent.session.subagents.values())


async def test_pending_resume_runs_deterministically_with_permission_events(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path, read_only=False)
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "owned.txt", "content": "resumed"}), Done()],
        [TextDelta("worker finished"), Done()],
    ])
    parent = restored_parent(tmp_path, payload, worker)
    parent.session.pending_subagent_resumes.append({"id": worker_id, "guidance": "Finish the requested file."})
    approvals = []
    async for event in parent.run("Resume the worker and report its result."):
        if isinstance(event, AgentPermissionRequest):
            approvals.append(event.call)
            event.future.set_result("allow_once")
    assert len(approvals) == 1
    assert (tmp_path / "owned.txt").read_text() == "resumed"
    assert parent.subagents.states[worker_id].status == "done"
    assert parent.session.pending_subagent_resumes == []
    assert any("worker finished" in block.get("text", "") for message in parent.session.messages for block in message.content)


@pytest.mark.parametrize("control", ["plan", "steer"])
async def test_parent_control_blocks_already_approved_child_write(tmp_path: Path, control: str) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "owned.txt", "content": "forbidden"}), Done()],
        [TextDelta("Respected the new guidance."), Done()],
    ])
    parent = make_parent(tmp_path, worker)
    spawned = await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["owned.txt"])
    while True:
        event = await asyncio.wait_for(parent.subagents.events.get(), 1)
        if isinstance(event, AgentPermissionRequest):
            if control == "plan":
                parent.session.mode = "plan"
            else:
                parent.session.queue_steering("Do not write that file.")
                parent.subagents.steer("Do not write that file.")
            event.future.set_result("allow_once")
            break
    await parent.subagents.wait([spawned["id"]], 1)
    assert not (tmp_path / "owned.txt").exists()
    if control == "steer":
        assert any("Do not write" in block.get("text", "") for message in worker.messages[-1] for block in message.content)


async def test_shutdown_preserves_interrupted_workers_for_explicit_resume(tmp_path: Path) -> None:
    parent = make_parent(tmp_path, WorkerProvider(delay=10))
    spawned = await parent.subagents.spawn(task="inspect", scope=".")
    await parent.subagents.close(interrupted=True)
    assert parent.subagents.states[spawned["id"]].status == "interrupted"
    assert parent.session.subagents[spawned["id"]]["status"] == "interrupted"
    await parent.subagents.close()
    assert parent.subagents.states[spawned["id"]].status == "interrupted"


async def test_resume_requested_during_parent_final_stream_is_handled_before_done(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path)
    parent = restored_parent(tmp_path, payload, WorkerProvider([[TextDelta("recovered result"), Done()]]))
    class QueueWhileStreaming(WorkerProvider):
        async def complete(self, messages, **kwargs):
            async for event in super().complete(messages, **kwargs):
                if len(self.messages) == 1 and isinstance(event, TextDelta):
                    parent.session.pending_subagent_resumes.append({"id": worker_id})
                yield event
    parent.provider = QueueWhileStreaming([
        [TextDelta("initial response"), Done()],
        [TextDelta("summarized worker result"), Done()],
    ])
    async for _ in parent.run("Continue"):
        pass
    assert len(parent.provider.messages) == 2
    assert parent.subagents.states[worker_id].status == "done"
    assert parent.session.pending_subagent_resumes == []
    assert "recovered result" in json.dumps([message.as_provider_dict() for message in parent.provider.messages[-1]])


async def test_parent_stream_close_closes_provider_and_keeps_worker_recoverable(tmp_path: Path) -> None:
    from libre_claw.core.agent import AgentTextDelta
    closed = False
    class StreamingProvider(WorkerProvider):
        async def complete(self, messages, **kwargs):
            nonlocal closed
            try:
                yield TextDelta("partial")
                await asyncio.sleep(30)
            finally:
                closed = True
    parent = make_parent(tmp_path, WorkerProvider(delay=30), StreamingProvider())
    spawned = await parent.subagents.spawn(task="independent inspection", scope=".")
    stream = parent.run("continue")
    assert isinstance(await anext(stream), AgentTextDelta)
    await stream.aclose()
    assert closed
    assert parent.subagents.states[spawned["id"]].status == "interrupted"
    assert parent.subagents.states[spawned["id"]].task_handle.done()


async def test_resumed_worker_preserves_guidance_order_and_resolved_model(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path)
    payload["subagents"][worker_id]["session"]["pending_steering"] = ["Older guidance"]
    worker = WorkerProvider()
    worker.model = "resolved-provider-model"
    parent = restored_parent(tmp_path, payload, worker)
    await parent.subagents.resume(worker_id, "Latest guidance")
    await parent.subagents.wait([worker_id], 1)
    content = [block.get("text", "") for message in worker.messages[0] for block in message.content]
    assert content.index("Older guidance") < next(index for index, text in enumerate(content) if "Latest guidance" in text)
    assert parent.session.subagents[worker_id]["model"] == "resolved-provider-model"


async def test_native_tool_provider_cannot_bypass_scoped_worker_guarantees(tmp_path: Path) -> None:
    from libre_claw.providers.codex import CodexProvider
    parent = make_parent(tmp_path, CodexProvider(model="arbitrary-model", working_directory=tmp_path))
    with pytest.raises(ValueError, match="runs its own tools"):
        await parent.subagents.spawn(task="inspect", scope=".")
    assert parent.subagents.states == {}


async def test_checkpoint_failure_prevents_worker_side_effect_and_reports_failure(tmp_path: Path) -> None:
    worker = WorkerProvider([
        [ToolCallReady("write", "write_file", {"path": "owned.txt", "content": "unsafe"}), Done()],
    ])
    parent = make_parent(tmp_path, worker)
    parent.permission_manager.always_allowed_tools.add("write_file")
    async def failing_checkpoint(session: Session) -> None:
        if any(
            block.get("type") == "tool_use"
            for value in session.subagents.values()
            for message in value["session"]["messages"] for block in message["content"]
        ):
            raise OSError("No space for durable checkpoint")
    parent.checkpoint_callback = failing_checkpoint
    spawned = await parent.subagents.spawn(task="write", scope=".", read_only=False, write_paths=["owned.txt"])
    snapshot = (await parent.subagents.wait([spawned["id"]], 1))[0]
    assert snapshot["status"] == "failed"
    assert "checkpoint" in snapshot["error"].lower()
    assert not (tmp_path / "owned.txt").exists()
    assert parent.subagents.states[spawned["id"]].task_handle.exception() is None


async def test_cancel_removes_explicit_resume_request_before_parent_starts(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path)
    worker = WorkerProvider()
    parent = restored_parent(tmp_path, payload, worker)
    parent.session.pending_subagent_resumes.append({"id": worker_id, "guidance": "resume"})
    await parent.subagents.cancel(worker_id)
    assert parent.session.pending_subagent_resumes == []
    async for _ in parent.run("Report status"):
        pass
    assert worker.messages == []
    assert parent.subagents.states[worker_id].status == "cancelled"


async def test_cancel_second_worker_during_batched_resume_never_starts_it(tmp_path: Path) -> None:
    payload, first_id = interrupted_payload(tmp_path)
    second_id = "second-worker"
    payload["subagents"][second_id] = {**copy.deepcopy(payload["subagents"][first_id]), "id": second_id}
    started, release = asyncio.Event(), asyncio.Event()
    class HeldWorker(WorkerProvider):
        async def complete(self, messages, **kwargs):
            started.set()
            await release.wait()
            async for event in super().complete(messages, **kwargs):
                yield event
    worker = HeldWorker([[TextDelta("first worker done"), Done()]])
    parent = restored_parent(tmp_path, payload, worker)
    parent.session.pending_subagent_resumes = [{"id": first_id}, {"id": second_id}]
    async def consume():
        async for _ in parent.run("Resume these workers"):
            pass
    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    await parent.subagents.cancel(second_id)
    release.set()
    await asyncio.wait_for(task, 2)
    assert len(worker.messages) == 1
    assert parent.subagents.states[first_id].status == "done"
    assert parent.subagents.states[second_id].status == "cancelled"
    assert parent.subagents.states[second_id].task_handle is None


async def test_cancel_during_resume_provider_setup_cannot_revive_worker(tmp_path: Path) -> None:
    from dataclasses import replace
    import threading
    payload, worker_id = interrupted_payload(tmp_path)
    worker = WorkerProvider()
    parent = restored_parent(tmp_path, payload, worker)
    entered, release = threading.Event(), threading.Event()
    def factory(*args):
        entered.set()
        release.wait(2)
        return worker
    context = replace(parent.tool_registry.context, subagent_provider_factory=factory)
    for tool in parent.tool_registry.tools():
        tool.context = context
    task = asyncio.create_task(parent.subagents.resume(worker_id))
    assert await asyncio.to_thread(entered.wait, 2)
    await parent.subagents.cancel(worker_id)
    release.set()
    with pytest.raises(ValueError, match="cancelled"):
        await asyncio.wait_for(task, 2)
    assert worker.messages == []
    assert parent.subagents.states[worker_id].status == "cancelled"
    assert parent.subagents.states[worker_id].task_handle is None


async def test_cancel_while_resume_checkpoint_is_saving_prevents_worker_launch(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path)
    worker = WorkerProvider()
    parent = restored_parent(tmp_path, payload, worker)
    entered, release = asyncio.Event(), asyncio.Event()
    async def checkpoint(session: Session) -> None:
        if session.subagents[worker_id]["status"] == "running":
            entered.set()
            await release.wait()
    parent.checkpoint_callback = checkpoint
    task = asyncio.create_task(parent.subagents.resume(worker_id))
    await asyncio.wait_for(entered.wait(), 2)
    cancelling = asyncio.create_task(parent.subagents.cancel(worker_id))
    # The cancellation updates state immediately, before waiting on the shared checkpoint lock.
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(cancelling, 2)
    with pytest.raises(ValueError, match="cancelled"):
        await asyncio.wait_for(task, 2)
    assert worker.messages == []
    assert parent.subagents.states[worker_id].status == "cancelled"
    assert parent.subagents.states[worker_id].task_handle is None
