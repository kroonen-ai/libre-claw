# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from libre_claw.config import load_config
from libre_claw.core.agent import AgentDone, AgentTextDelta
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.daemon import DaemonServer


class Request:
    def __init__(self, data: dict[str, Any], run_id: str = "") -> None:
        self.data = data
        self.match_info = {"run_id": run_id}

    async def json(self) -> dict[str, Any]:
        return self.data


class ControlledAgent:
    def __init__(self, controller: Controller, session: Session) -> None:
        self.controller = controller
        self.session = session
        self.checkpoint_callback = None
        self.subagents = None

    async def run(self, message: str, *, attachments=()):
        self.controller.calls.append(message)
        self.controller.active += 1
        self.controller.max_active = max(self.controller.max_active, self.controller.active)
        self.session.add_user_message(message)
        await self.controller.started.put(message)
        try:
            await self.controller.gates.setdefault(message, asyncio.Event()).wait()
            self.session.add_assistant_message(f"answer:{message}")
            yield AgentTextDelta(f"answer:{message}")
            yield AgentDone()
        finally:
            self.controller.active -= 1


class Controller:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.gates: dict[str, asyncio.Event] = {}
        self.active = 0
        self.max_active = 0

    async def create_agent(self, config, *, session, **kwargs):
        return ControlledAgent(self, session)

    async def wait_started(self, expected: str) -> None:
        assert await asyncio.wait_for(self.started.get(), 5) == expected

    def release(self, message: str) -> None:
        self.gates.setdefault(message, asyncio.Event()).set()


@pytest.fixture
async def harness(tmp_path: Path, monkeypatch):
    config = load_config(working_directory=tmp_path)
    config = replace(config, general=replace(config.general, default_provider="openai", default_model="test-model"), memory=replace(config.memory, enabled=False), automations=replace(config.automations, enabled=False))
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"), start_telegram_bridge=False)
    controller = Controller()
    monkeypatch.setattr(server, "_create_agent", controller.create_agent)

    async def no_pet(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "_send_petdex_state", no_pet)
    yield server, controller
    await asyncio.wait_for(server._on_cleanup(None), 5)


async def start(server: DaemonServer, message: str) -> str:
    response = await server.start_run(Request({"message": message}))
    assert response.status == 202
    return json.loads(response.text)["run"]["run_id"]


async def queue(server: DaemonServer, run_id: str, message: str) -> None:
    response = await server.control_run(Request({"action": "queue", "text": message}, run_id))
    assert response.status == 200, response.text


async def wait_idle(server: DaemonServer, run_id: str) -> None:
    active = server.active_runs.get(run_id)
    if active is not None:
        await asyncio.wait_for(asyncio.shield(active.task), 5)
    wakeup = server._queue_wakeups.get(run_id)
    if wakeup is not None:
        await asyncio.wait_for(asyncio.shield(wakeup), 5)


async def test_queued_turns_keep_run_running_and_publish_one_terminal_event(harness) -> None:
    server, controller = harness
    run_id = await start(server, "first")
    await controller.wait_started("first")
    await queue(server, run_id, "second")
    controller.release("first")
    await controller.wait_started("second")
    assert (await server.run_store.load_run(run_id)).state == "running"
    events = await server.run_store.load_events(run_id)
    assert [event.type for event in events].count("turn_finished") == 1
    assert "run_finished" not in [event.type for event in events]
    saved = await server.run_store.load_session(run_id)
    assert "answer:first" in str(saved.messages)
    controller.release("second")
    await wait_idle(server, run_id)
    events = await server.run_store.load_events(run_id)
    assert [event.type for event in events].count("turn_finished") == 2
    assert [event.type for event in events].count("run_finished") == 1
    assert controller.max_active == 1
    assert (await server.run_store.load_run(run_id)).state == "done"


async def test_queue_added_after_terminal_claim_during_teardown_is_started(harness, monkeypatch) -> None:
    server, controller = harness
    terminal_written, release_teardown = asyncio.Event(), asyncio.Event()
    original = server.run_store.finish_turn
    held = False

    async def hold_finish(*args, **kwargs):
        nonlocal held
        result = await original(*args, **kwargs)
        if not held and result is None:
            held = True
            terminal_written.set()
            await release_teardown.wait()
        return result

    monkeypatch.setattr(server.run_store, "finish_turn", hold_finish)
    run_id = await start(server, "first")
    await controller.wait_started("first")
    controller.release("first")
    await asyncio.wait_for(terminal_written.wait(), 5)
    await queue(server, run_id, "late")
    release_teardown.set()
    await controller.wait_started("late")
    controller.release("late")
    await wait_idle(server, run_id)
    assert controller.calls == ["first", "late"] and controller.max_active == 1
    assert not await server.run_store.queued_messages(run_id)


async def test_queue_added_to_idle_task_starts_without_another_user_message(harness) -> None:
    server, controller = harness
    run_id = await start(server, "first")
    await controller.wait_started("first")
    controller.release("first")
    await wait_idle(server, run_id)
    await queue(server, run_id, "idle-followup")
    await controller.wait_started("idle-followup")
    controller.release("idle-followup")
    await wait_idle(server, run_id)
    assert controller.calls == ["first", "idle-followup"]


async def test_two_simultaneous_continuations_cannot_start_two_agents(harness, monkeypatch) -> None:
    server, controller = harness
    run = await server.run_store.create_run("Existing", kind="chat", provider="openai", model="test-model", working_directory=server.config.general.working_directory, state="done")
    await server.run_store.save_session(run.run_id, Session())
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = server._config_for_payload

    async def block_config(payload):
        entered.set()
        await proceed.wait()
        return await original(payload)

    monkeypatch.setattr(server, "_config_for_payload", block_config)
    first = asyncio.create_task(server.continue_run(Request({"message": "first"}, run.run_id)))
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(server.continue_run(Request({"message": "second"}, run.run_id)))
    proceed.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert sorted(response.status for response in responses) == [202, 409]
    await controller.wait_started("first")
    assert controller.calls == ["first"]
    controller.release("first")
    await wait_idle(server, run.run_id)


async def test_old_done_callback_cannot_remove_a_replacement_active_run(harness) -> None:
    server, _ = harness
    old_gate, new_gate = asyncio.Event(), asyncio.Event()
    old = asyncio.create_task(old_gate.wait())
    new = asyncio.create_task(new_gate.wait())
    server._register_active("test", old, "old")
    replacement = server._register_active("test", new, "new")
    old_gate.set()
    await old
    assert server.active_runs["test"] is replacement
    new_gate.set()
    await new


async def test_cancel_during_checkpoint_finalizes_run_and_clears_active_session(harness, monkeypatch) -> None:
    server, controller = harness
    entered = asyncio.Event()

    async def blocked_checkpoint(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("libre_claw.core.git_review.create_checkpoint", blocked_checkpoint)
    run_id = await start(server, "never-started")
    await asyncio.wait_for(entered.wait(), 5)
    active = server.active_runs[run_id]
    active.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active.task
    assert (await server.run_store.load_run(run_id)).state == "cancelled"
    assert run_id not in server._active_sessions
    assert controller.calls == []


async def test_cancel_keeps_unstarted_followups_queued_and_stops_wakeup(harness) -> None:
    server, controller = harness
    run_id = await start(server, "first")
    await controller.wait_started("first")
    await queue(server, run_id, "do-not-start")
    active = server.active_runs[run_id]
    response = await server.cancel_run(Request({}, run_id))
    assert response.status == 200
    with pytest.raises(asyncio.CancelledError):
        await active.task
    assert (await server.run_store.load_run(run_id)).state == "cancelled"
    assert [item["message"] for item in await server.run_store.queued_messages(run_id)] == ["do-not-start"]
    assert controller.calls == ["first"]


async def test_cancel_during_memory_teardown_still_persists_cancelled_state(harness, monkeypatch) -> None:
    server, controller = harness
    entered = asyncio.Event()

    async def blocked_memory(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_extract_run_memory", blocked_memory)
    run_id = await start(server, "first")
    await controller.wait_started("first")
    controller.release("first")
    await asyncio.wait_for(entered.wait(), 5)
    active = server.active_runs[run_id]
    active.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active.task, 5)
    assert (await server.run_store.load_run(run_id)).state == "cancelled"
    assert "answer:first" in str((await server.run_store.load_session(run_id)).messages)


async def test_queue_claims_are_unique_across_multiple_store_instances(tmp_path: Path) -> None:
    stores = [RunStore(tmp_path / "runs") for _ in range(4)]
    run = await stores[0].create_run("Task", kind="chat", provider="openai", model="test", state="running")
    await asyncio.gather(*(stores[index % 4].queue_message(run.run_id, f"message-{index}") for index in range(20)))
    items = await stores[0].queued_messages(run.run_id)
    assert len(items) == 20
    claimed = await asyncio.gather(*(stores[index % 4].take_queued_message(run.run_id) for index in range(20)))
    assert len({item["id"] for item in claimed}) == 20
    assert not await stores[0].queued_messages(run.run_id)


async def test_crash_after_claim_journal_does_not_replay_unknown_work(tmp_path: Path, monkeypatch) -> None:
    from libre_claw.core import runs as module
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Task", kind="chat", provider="openai", model="test", state="running")
    item = await store.queue_message(run.run_id, "side effect")
    original = module._write_json

    def fail_queue_removal(path, payload):
        if path.name == "queue.json" and payload == {"messages": []}:
            raise OSError("simulated process interruption")
        original(path, payload)

    monkeypatch.setattr(module, "_write_json", fail_queue_removal)
    with pytest.raises(OSError, match="interruption"):
        await store.take_queued_message(run.run_id)
    recovered = RunStore(store.root)
    assert not await recovered.queued_messages(run.run_id)
    events = await recovered.load_events(run.run_id)
    assert any(event.type == "queued_message_started" and event.data["id"] == item["id"] for event in events)


async def managed_checkout(server: DaemonServer, tmp_path: Path):
    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)

    git("init", "-b", "main")
    git("config", "user.name", "Worktree Launch Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "example.txt").write_text("original\n")
    git("add", ".")
    git("commit", "-m", "Create launch fixture")
    owner = await server.run_store.create_run("Worktree placeholder", kind="chat", provider="openai", model="test-model", working_directory=repo, state="done")
    return await server.workflows.worktrees.create(repo, owner.run_id)


async def test_simultaneous_worktree_launches_claim_checkout_only_once(harness, tmp_path: Path, monkeypatch) -> None:
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    first_creating, release_creation, second_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_create = server.run_store.create_run
    original_lock = server._run_start_lock

    class ObservedLock:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.attempts = 0

        async def __aenter__(self):
            self.attempts += 1
            if self.attempts == 2:
                second_waiting.set()
            await self.lock.acquire()

        async def __aexit__(self, *args):
            self.lock.release()

    launch_lock = ObservedLock()

    async def hold_first_creation(title, **kwargs):
        if title == "first":
            first_creating.set()
            await release_creation.wait()
        return await original_create(title, **kwargs)

    monkeypatch.setattr(server.run_store, "create_run", hold_first_creation)
    monkeypatch.setattr(server, "_run_start_lock", lambda key: launch_lock if key == f"worktree:{worktree.worktree_id}" else original_lock(key))
    first = asyncio.create_task(server.start_run(Request({"message": "first", "worktree_id": worktree.worktree_id})))
    await asyncio.wait_for(first_creating.wait(), 5)
    second = asyncio.create_task(server.start_run(Request({"message": "second", "worktree_id": worktree.worktree_id})))
    await asyncio.wait_for(second_waiting.wait(), 5)
    release_creation.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert [response.status for response in responses] == [202, 409]
    await controller.wait_started("first")
    assert controller.calls == ["first"]
    accepted_run_id = json.loads(responses[0].text)["run"]["run_id"]
    associated = await server.workflows.worktrees.get(worktree.worktree_id)
    assert associated.run_id == accepted_run_id
    assert len(await server.run_store.list_runs()) == 2
    controller.release("first")
    await wait_idle(server, accepted_run_id)


@pytest.mark.parametrize("state", ["queued", "running", "blocked"])
async def test_worktree_launch_respects_durable_local_task_in_checkout_subdirectory(harness, tmp_path: Path, state: str) -> None:
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    nested = Path(worktree.path) / "nested"
    nested.mkdir()
    local = await server.run_store.create_run("Local TUI task", kind="chat", provider="openai", model="test-model", working_directory=nested, state=state)
    assert local.run_id not in server.active_runs
    response = await server.start_run(Request({"message": "conflicting work", "worktree_id": worktree.worktree_id}))
    assert response.status == 409
    assert "still using this workspace" in json.loads(response.text)["error"]
    assert controller.calls == []
    assert (await server.workflows.worktrees.get(worktree.worktree_id)).run_id == worktree.run_id
    assert len(await server.run_store.list_runs()) == 2


async def test_done_worktree_placeholder_does_not_block_launch(harness, tmp_path: Path) -> None:
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    response = await server.start_run(Request({"message": "isolated", "worktree_id": worktree.worktree_id}))
    assert response.status == 202
    run_id = json.loads(response.text)["run"]["run_id"]
    await controller.wait_started("isolated")
    assert (await server.run_store.load_run(run_id)).working_directory == worktree.path
    controller.release("isolated")
    await wait_idle(server, run_id)


async def saved_worktree_task(server: DaemonServer, worktree, *, nested: bool = False):
    workspace = Path(worktree.path)
    if nested:
        workspace = workspace / "component"
        workspace.mkdir(exist_ok=True)
    run = await server.run_store.create_run(
        "Older task in this checkout", kind="chat", provider="openai", model="test-model",
        working_directory=workspace, state="done",
    )
    await server.run_store.save_session(run.run_id, Session())
    return run


async def test_older_task_continuation_rejects_checkout_owned_by_new_task(harness, tmp_path: Path) -> None:
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree, nested=True)
    response = await server.start_run(Request({"message": "new owner", "worktree_id": worktree.worktree_id}))
    assert response.status == 202
    owner_id = json.loads(response.text)["run"]["run_id"]
    await controller.wait_started("new owner")
    response = await server.continue_run(Request({"message": "old continuation"}, old.run_id))
    assert response.status == 409
    assert owner_id in json.loads(response.text)["error"]
    assert controller.calls == ["new owner"]
    assert (await server.run_store.load_run(old.run_id)).state == "done"
    controller.release("new owner")
    await wait_idle(server, owner_id)


async def test_old_task_queue_waits_without_claim_until_checkout_owner_finishes(harness, tmp_path: Path, monkeypatch) -> None:
    from libre_claw.web.workflow_api import WorkspaceBusyError
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree, nested=True)
    response = await server.start_run(Request({"message": "new owner", "worktree_id": worktree.worktree_id}))
    owner_id = json.loads(response.text)["run"]["run_id"]
    await controller.wait_started("new owner")
    busy = asyncio.Event()
    original = server.workflows.assert_idle
    async def observe_busy(*paths, **kwargs):
        try:
            await original(*paths, **kwargs)
        except WorkspaceBusyError:
            busy.set()
            raise
    monkeypatch.setattr(server.workflows, "assert_idle", observe_busy)
    await queue(server, old.run_id, "queued old task")
    await asyncio.wait_for(busy.wait(), 5)
    assert [item["message"] for item in await server.run_store.queued_messages(old.run_id)] == ["queued old task"]
    assert not any(event.type == "queued_message_started" for event in await server.run_store.load_events(old.run_id))
    assert (await server.run_store.load_run(old.run_id)).state == "done"
    assert controller.calls == ["new owner"]
    controller.release("new owner")
    await controller.wait_started("queued old task")
    assert not await server.run_store.queued_messages(old.run_id)
    assert controller.max_active == 1
    controller.release("queued old task")
    await wait_idle(server, old.run_id)
    await wait_idle(server, owner_id)


@pytest.mark.parametrize("winner", ["continue", "new"])
async def test_continuation_and_new_worktree_launch_share_one_checkout_claim(harness, tmp_path: Path, monkeypatch, winner: str) -> None:
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree)
    entered, proceed, competitor_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_lock = server._run_start_lock
    class ObservedLock:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.attempts = 0
        async def __aenter__(self):
            self.attempts += 1
            if self.attempts == 2:
                competitor_waiting.set()
            await self.lock.acquire()
        async def __aexit__(self, *args):
            self.lock.release()
    checkout_lock = ObservedLock()
    monkeypatch.setattr(server, "_run_start_lock", lambda key: checkout_lock if key == f"worktree:{worktree.worktree_id}" else original_lock(key))
    if winner == "continue":
        original = server.run_store.set_runtime
        async def hold_runtime(*args, **kwargs):
            entered.set()
            await proceed.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(server.run_store, "set_runtime", hold_runtime)
    else:
        original = server.run_store.create_run
        async def hold_creation(*args, **kwargs):
            entered.set()
            await proceed.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(server.run_store, "create_run", hold_creation)
    def launch(kind: str):
        if kind == "continue":
            return server.continue_run(Request({"message": "continued work"}, old.run_id))
        return server.start_run(Request({"message": "new work", "worktree_id": worktree.worktree_id}))
    first = asyncio.create_task(launch(winner))
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(launch("new" if winner == "continue" else "continue"))
    await asyncio.wait_for(competitor_waiting.wait(), 5)
    proceed.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert [response.status for response in responses] == [202, 409]
    message = "continued work" if winner == "continue" else "new work"
    await controller.wait_started(message)
    assert controller.calls == [message]
    controller.release(message)
    await wait_idle(server, json.loads(responses[0].text)["run"]["run_id"])


@pytest.mark.parametrize("state", ["queued", "running", "blocked"])
async def test_managed_resume_and_queue_respect_durable_local_tui_owner(harness, tmp_path: Path, monkeypatch, state: str) -> None:
    from libre_claw.web.workflow_api import WorkspaceBusyError
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree)
    nested = Path(worktree.path) / "local-component"
    nested.mkdir()
    local = await server.run_store.create_run(
        "Local TUI owner", kind="chat", provider="openai", model="test-model", working_directory=nested, state=state,
    )
    response = await server.continue_run(Request({"message": "conflicting continuation"}, old.run_id))
    assert response.status == 409
    assert local.run_id in json.loads(response.text)["error"]
    busy = asyncio.Event()
    original = server.workflows.assert_idle
    async def observe_busy(*paths, **kwargs):
        try:
            await original(*paths, **kwargs)
        except WorkspaceBusyError:
            busy.set()
            raise
    monkeypatch.setattr(server.workflows, "assert_idle", observe_busy)
    await queue(server, old.run_id, "wait for local owner")
    await asyncio.wait_for(busy.wait(), 5)
    assert controller.calls == []
    assert len(await server.run_store.queued_messages(old.run_id)) == 1
    await server.run_store.update_state(local.run_id, "done")
    await controller.wait_started("wait for local owner")
    assert controller.max_active == 1
    controller.release("wait for local owner")
    await wait_idle(server, old.run_id)


async def test_cancel_waiting_checkout_queue_is_responsive_and_preserves_message(harness, tmp_path: Path, monkeypatch) -> None:
    from libre_claw.web.workflow_api import WorkspaceBusyError
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree)
    local = await server.run_store.create_run("Local owner", kind="chat", provider="openai", model="test-model", working_directory=worktree.path, state="running")
    busy = asyncio.Event()
    original = server.workflows.assert_idle
    async def observe_busy(*paths, **kwargs):
        try:
            await original(*paths, **kwargs)
        except WorkspaceBusyError:
            busy.set()
            raise
    monkeypatch.setattr(server.workflows, "assert_idle", observe_busy)
    await queue(server, old.run_id, "keep queued")
    await asyncio.wait_for(busy.wait(), 5)
    wakeup = server._queue_wakeups[old.run_id]
    response = await asyncio.wait_for(server.cancel_run(Request({}, old.run_id)), 1)
    assert response.status == 200
    await asyncio.gather(wakeup, return_exceptions=True)
    await server.run_store.update_state(local.run_id, "done")
    assert [item["message"] for item in await server.run_store.queued_messages(old.run_id)] == ["keep queued"]
    assert controller.calls == []
    assert (await server.run_store.load_run(old.run_id)).state == "cancelled"


async def test_unmanaged_workspace_still_allows_independent_concurrent_tasks(harness) -> None:
    server, controller = harness
    first = await start(server, "first independent task")
    second = await start(server, "second independent task")
    await controller.wait_started("first independent task")
    await controller.wait_started("second independent task")
    assert controller.max_active == 2
    controller.release("first independent task")
    controller.release("second independent task")
    await wait_idle(server, first)
    await wait_idle(server, second)


async def test_shutdown_does_not_wait_for_external_owner_or_claim_waiting_queue(harness, tmp_path: Path, monkeypatch) -> None:
    from libre_claw.web.workflow_api import WorkspaceBusyError
    server, controller = harness
    worktree = await managed_checkout(server, tmp_path)
    old = await saved_worktree_task(server, worktree)
    await server.run_store.create_run("Local owner", kind="chat", provider="openai", model="test-model", working_directory=worktree.path, state="running")
    busy = asyncio.Event()
    original = server.workflows.assert_idle
    async def observe_busy(*paths, **kwargs):
        try:
            await original(*paths, **kwargs)
        except WorkspaceBusyError:
            busy.set()
            raise
    monkeypatch.setattr(server.workflows, "assert_idle", observe_busy)
    await queue(server, old.run_id, "still queued after shutdown")
    await asyncio.wait_for(busy.wait(), 5)
    await asyncio.wait_for(server._on_cleanup(None), 1)
    # Re-open the persisted queue independently after its application engine
    # has stopped; the disposed service must not dispatch another operation.
    recovered = RunStore(server.run_store.root)
    assert not server.engine.running
    assert [item["message"] for item in await recovered.queued_messages(old.run_id)] == ["still queued after shutdown"]
    assert controller.calls == []
    assert server._queue_wakeups == {}
