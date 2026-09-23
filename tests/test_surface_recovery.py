# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from libre_claw.config import load_config
from libre_claw.core import AgentDone, Session
from libre_claw.core.cordis_engine import CordisEngine
import libre_claw.core.runs as runs_module
from libre_claw.telegram.bridge import TelegramBridge
from libre_claw.telegram.handlers import TelegramHandlers
from libre_claw.tui.app import LibreClawApp


class EmptyAgent:
    subagents = None

    async def run(self, text, attachments=()):
        yield AgentDone()


@pytest.fixture
def local_config(tmp_path: Path):
    original = tmp_path / "original"
    original.mkdir()
    config = load_config(working_directory=original)
    return replace(
        config,
        general=replace(config.general, default_provider="ollama", default_model="original-model"),
        memory=replace(config.memory, enabled=False, archive_sessions=False),
        petdex=replace(config.petdex, enabled=False, notify_tui=False),
    )


async def resumed_bridge(config, tmp_path: Path):
    workspace = tmp_path / "resumed"
    workspace.mkdir()
    bridge = TelegramBridge(config)
    bridge.run_store = runs_module.RunStore(tmp_path / "runs")
    run = await bridge.run_store.create_run(
        "saved task", kind="chat", provider="ollama", model="saved-model", working_directory=workspace, state="done",
    )
    assert (await bridge.resume_command_text(1, run.run_id)).startswith("Resumed")
    return bridge, run, workspace


async def test_telegram_resumed_model_selection_is_persisted(local_config, tmp_path: Path, monkeypatch) -> None:
    bridge, run, workspace = await resumed_bridge(local_config, tmp_path)
    state = bridge.state_for(1)
    state.runtime_config = replace(state.runtime_config, general=replace(state.runtime_config.general, default_model="selected-model"))
    monkeypatch.setattr(bridge, "_create_agent", lambda _state: EmptyAgent())
    _ = [event async for event in bridge.stream_message(1, "continue")]
    saved = await bridge.run_store.load_run(run.run_id)
    assert saved.model == "selected-model" and saved.provider == "ollama"
    assert Path(saved.working_directory) == workspace
    state.runtime_config = None
    await bridge.resume_command_text(1, run.run_id)
    assert bridge.runtime_config_for(1).general.default_model == "selected-model"
    assert "`selected-model`" in bridge.status_text(1)
    assert "`original-model`" not in bridge.status_text(1)
    assert bridge.runtime_config_for(2).general.default_model == "original-model"


async def test_telegram_resumed_memory_uses_task_workspace_and_provider(local_config, tmp_path: Path, monkeypatch) -> None:
    config = replace(local_config, memory=replace(local_config.memory, enabled=True, auto_summarize=True, auto_extract=True))
    bridge, _, workspace = await resumed_bridge(config, tmp_path)
    writes, searches, providers = [], [], []

    class Memory:
        async def add_memory_item(self, **kwargs):
            writes.append(kwargs)

        async def search_memory_items(self, query, **kwargs):
            searches.append(kwargs)
            return []

    async def extract(*args, **kwargs):
        return [SimpleNamespace(kind="fact", scope="project", text="Keep the saved requirement.")]

    def provider(config):
        providers.append(config)
        return object()

    bridge.memory_store = Memory()
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", provider)
    monkeypatch.setattr("libre_claw.telegram.bridge.extract_memories_with_provider", extract)
    await bridge._extract_turn_memory(1, "task", "result")
    assert all(item["project_root"] == workspace for item in writes)
    assert searches[0]["project_root"] == workspace
    assert providers[0].general.default_model == "saved-model"
    assert providers[0].general.working_directory == workspace


async def test_telegram_document_uploads_use_resumed_workspace(local_config, tmp_path: Path, monkeypatch) -> None:
    bridge, _, workspace = await resumed_bridge(local_config, tmp_path)
    allowed = workspace / "result.txt"
    allowed.write_text("saved task result")
    unrelated = local_config.general.working_directory / "unrelated.txt"
    unrelated.write_text("other task data")
    handlers = TelegramHandlers(bridge, auth=None)
    sent = []

    async def send(_message, paths):
        sent.extend(paths)
        return len(paths), []

    monkeypatch.setattr("libre_claw.telegram.handlers._reply_document_paths", send)
    await handlers._send_documents_from_text(object(), 1, f"Result: {allowed}\nUnrelated: {unrelated}")
    assert sent == [allowed.resolve()]
    handlers._recent_document_paths[1] = [allowed, unrelated]
    sent.clear()
    assert await handlers._send_remembered_documents(object(), 1)
    assert sent == [allowed]


@pytest.mark.parametrize("kind", ["chat", "goal"])
async def test_tui_remains_busy_until_session_and_run_finalization_finish(local_config, tmp_path: Path, monkeypatch, kind: str) -> None:
    store = runs_module.RunStore(tmp_path / "runs")
    run = await store.create_run("task", kind=kind, provider="ollama", model="model", working_directory=tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def collect(*args):
        entered.set()
        await release.wait()
        return "", "", ""

    class EmptyGoal:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            yield AgentDone()

    monkeypatch.setattr("libre_claw.tui.app._collect_run_artifacts", collect)
    monkeypatch.setattr("libre_claw.tui.app.GoalRunner", EmptyGoal)
    notices = []
    app = SimpleNamespace(
        config=local_config, session=Session(), agent=EmptyAgent(), run_store=store, engine=CordisEngine(),
        _active_run_id=run.run_id, _resumed_run_id=run.run_id, _active_run_summary="",
        _run_background_tasks=set(), _active_task=None, _pending_permission=None, _user_questions={},
        _pending_key_setup=None, palette_open=False, _goal_max_turns=1,
        transcript=[SimpleNamespace(content="finished response")],
    )
    app._flush_stream_buffer = lambda *args: None
    app._hide_permission_prompt = lambda: None
    app._handle_agent_stream_event = lambda *args, **kwargs: (False, False)
    app._archive_session_event_later = lambda *args: None
    app._schedule_memory_extraction = lambda *args: None
    app._update_shell_chrome = app._update_status = lambda: None
    app._append_system = notices.append
    app.query_one = lambda *args: SimpleNamespace(focus=lambda: None)
    app._finish_active_run = MethodType(LibreClawApp._finish_active_run, app)
    for method in ("_finish_active_run_with_engine", "_finish_local_turn", "_dispatch_queued_followup", "_start_local_queue", "_wake_local_queue", "_drain_local_queue"):
        setattr(app, method, MethodType(getattr(LibreClawApp, method), app))
    stream = LibreClawApp._stream_agent_response(app, "task", 0) if kind == "chat" else LibreClawApp._stream_goal_response(app, "task", 0, object())
    task = asyncio.create_task(stream)
    app._active_task = task
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert app._active_task is task and not task.done()
        await LibreClawApp.handle_user_input(app, "another turn")
        assert notices[-1].startswith("A response is already streaming")
        assert app._active_run_id == run.run_id
        assert (await store.load_run(run.run_id)).state == "running"
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert app._active_task is None and app._active_run_id is None
    assert (await store.load_run(run.run_id)).state == "done"


@pytest.mark.parametrize("kind", ["chat", "goal"])
async def test_tui_claimed_followup_keeps_ownership_until_next_task_is_registered(local_config, tmp_path: Path, monkeypatch, kind: str) -> None:
    store = runs_module.RunStore(tmp_path / "runs")
    run = await store.create_run("task", kind=kind, provider="ollama", model="model", working_directory=tmp_path)
    await store.queue_message(run.run_id, "queued follow-up")
    starting, release_start, streaming, release_stream = (asyncio.Event() for _ in range(4))
    notices, submitted = [], []

    async def collect(*args):
        return "", "", ""

    class EmptyGoal:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            yield AgentDone()

    monkeypatch.setattr("libre_claw.tui.app._collect_run_artifacts", collect)
    monkeypatch.setattr("libre_claw.tui.app.GoalRunner", EmptyGoal)
    app = SimpleNamespace(
        config=local_config, session=Session(), agent=EmptyAgent(), run_store=store, engine=CordisEngine(),
        _active_run_id=run.run_id, _resumed_run_id=run.run_id, _active_run_summary="",
        _run_background_tasks=set(), _active_task=None, _pending_permission=None, _user_questions={},
        _pending_key_setup=None, _pending_attachments=[], palette_open=False,
        _goal_max_turns=1, daemon_client=None,
        transcript=[SimpleNamespace(content="finished response")],
    )
    app._flush_stream_buffer = lambda *args: None
    app._hide_permission_prompt = lambda: None
    app._handle_agent_stream_event = lambda *args, **kwargs: (False, False)
    app._archive_session_event_later = lambda *args: None
    app._schedule_memory_extraction = lambda *args: None
    app._update_shell_chrome = app._update_status = lambda: None
    app._append_system = notices.append
    app._append_user = submitted.append
    app._append_assistant = lambda _: 0
    app.query_one = lambda *args: SimpleNamespace(focus=lambda: None)
    app._finish_active_run = MethodType(LibreClawApp._finish_active_run, app)
    for method in ("_finish_active_run_with_engine", "_finish_local_turn", "_dispatch_queued_followup", "_start_local_queue", "_wake_local_queue", "_drain_local_queue"):
        setattr(app, method, MethodType(getattr(LibreClawApp, method), app))
    app.handle_user_input = MethodType(LibreClawApp.handle_user_input, app)

    async def start_run(*args):
        starting.set()
        await release_start.wait()
        app._active_run_id = run.run_id
        return run

    async def no_event(*args):
        pass

    async def stream_next(*args, **kwargs):
        streaming.set()
        await release_stream.wait()

    app._start_run = start_run
    app._record_run_event = no_event
    app._stream_agent_response = stream_next
    initial = LibreClawApp._stream_agent_response(app, "first", 0) if kind == "chat" else LibreClawApp._stream_goal_response(app, "first", 0, object())
    owner = asyncio.create_task(initial)
    app._active_task = owner
    try:
        await asyncio.wait_for(starting.wait(), 5)
        assert app._active_task is owner and not owner.done()
        await app.handle_user_input("racing fresh message")
        assert notices[-1].startswith("A response is already streaming")
        assert submitted == ["queued follow-up"]
        assert not await store.queued_messages(run.run_id)
        release_start.set()
        await asyncio.wait_for(owner, 5)
        await asyncio.wait_for(streaming.wait(), 5)
        assert app._active_task is not owner and not app._active_task.done()
        assert app._active_run_id == run.run_id
    finally:
        release_start.set()
        release_stream.set()
        await owner
        if app._active_task is not None:
            await app._active_task


async def test_returned_queue_claim_is_idempotent_fifo_and_recovers_journal_gap(tmp_path: Path, monkeypatch) -> None:
    store = runs_module.RunStore(tmp_path / "runs")
    run = await store.create_run("task", kind="chat", provider="ollama", model="model")
    first = await store.queue_message(run.run_id, "first")
    second = await store.queue_message(run.run_id, "second")
    assert await store.take_queued_message(run.run_id) == first
    await store.release_queued_message(run.run_id, first)
    await store.release_queued_message(run.run_id, first)
    assert await store.queued_messages(run.run_id) == [first, second]
    assert await store.take_queued_message(run.run_id) == first
    write = runs_module._write_json

    def crash_before_queue_snapshot(path, payload):
        if path.name == "queue.json":
            raise OSError("simulated exit after durable return journal")
        return write(path, payload)

    monkeypatch.setattr(runs_module, "_write_json", crash_before_queue_snapshot)
    with pytest.raises(OSError):
        await store.release_queued_message(run.run_id, first)
    restarted = runs_module.RunStore(store.root)
    assert await restarted.queued_messages(run.run_id) == [first, second]
    monkeypatch.setattr(runs_module, "_write_json", write)
    assert await restarted.take_queued_message(run.run_id) == first
    assert await restarted.queued_messages(run.run_id) == [second]
    with pytest.raises(ValueError, match="recorded"):
        await restarted.release_queued_message(run.run_id, {**first, "message": "unreviewed replacement"})


@pytest.mark.parametrize("surface", ["tui", "telegram"])
async def test_stop_during_finalization_restores_unstarted_followup(local_config, tmp_path: Path, monkeypatch, surface: str) -> None:
    bridge, run, _ = await resumed_bridge(local_config, tmp_path)
    store = bridge.run_store
    first = await store.queue_message(run.run_id, "first pending")
    second = await store.queue_message(run.run_id, "second pending")
    entered, release = asyncio.Event(), asyncio.Event()
    original_finish = store.finish_turn

    async def held_finish(*args, **kwargs):
        result = await original_finish(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(store, "finish_turn", held_finish)
    state = bridge.state_for(1)
    state.session.add_user_message("preserve conversation")
    if surface == "telegram":
        monkeypatch.setattr(bridge, "_create_agent", lambda _state: EmptyAgent())

        async def consume():
            return [event async for event in bridge.stream_message(1, "finish")]

        task = asyncio.create_task(consume())
    else:
        async def finish(*args, **kwargs):
            await store.save_session(run.run_id, state.session)
            return await store.finish_turn(run.run_id, "done", summary="Completed first turn", diff="keep existing diff")

        app = SimpleNamespace(_active_run_id=run.run_id, run_store=store, _finish_active_run=finish, _user_questions={})
        task = asyncio.create_task(LibreClawApp._finish_local_turn(app, "done"))
    await asyncio.wait_for(entered.wait(), 5)
    assert await store.queued_messages(run.run_id) == [second]
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # A second stop must not cancel the durable cleanup itself.
    release.set()
    if surface == "telegram":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        assert await asyncio.wait_for(task, 5) is None
        assert (run.path / "diff.patch").read_text() == "keep existing diff"
    assert (await store.load_run(run.run_id)).state == "cancelled"
    assert await store.queued_messages(run.run_id) == [first, second]
    assert (await store.load_session(run.run_id)).messages[0].content[0]["text"] == "preserve conversation"
    events = await store.load_events(run.run_id)
    assert sum(event.type == "queued_message_returned" for event in events) == 1


async def test_tui_idle_queue_reserves_execution_and_uses_existing_run(local_config, tmp_path: Path) -> None:
    store = runs_module.RunStore(tmp_path / "runs")
    run = await store.create_run("task", kind="chat", provider="ollama", model="model", state="done")
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    app = SimpleNamespace(
        run_store=store, _active_run_id=None, _resumed_run_id=run.run_id,
        _active_task=None, daemon_client=None, _append_system=lambda _: None,
    )
    for method in ("_start_local_queue", "_wake_local_queue", "_drain_local_queue", "_dispatch_queued_followup"):
        setattr(app, method, MethodType(getattr(LibreClawApp, method), app))

    async def input(message, *, _owned_task=None):
        assert app._active_task is _owned_task is asyncio.current_task()
        observed.append((message, app._resumed_run_id))
        entered.set()
        await release.wait()

    app.handle_user_input = input
    await LibreClawApp._handle_task_control(app, "queue", "do next")
    owner = app._active_task
    assert owner is not None and not owner.done()
    await asyncio.wait_for(entered.wait(), 5)
    app._start_local_queue(run.run_id)
    assert app._active_task is owner
    release.set()
    await asyncio.wait_for(owner, 5)
    assert observed == [("do next", run.run_id)]
    assert not await store.queued_messages(run.run_id)


@pytest.mark.parametrize("late_arrival", [False, True])
async def test_telegram_queue_uses_normal_renderer_without_sending_command(local_config, tmp_path: Path, monkeypatch, late_arrival: bool) -> None:
    from libre_claw.core import AgentTextDelta
    bridge, run, _ = await resumed_bridge(local_config, tmp_path)
    prompts = []

    class Agent(EmptyAgent):
        async def run(self, text, attachments=()):
            prompts.append(text)
            yield AgentTextDelta("queued result")
            yield AgentDone()

    monkeypatch.setattr(bridge, "_create_agent", lambda _state: Agent())
    handlers = TelegramHandlers(bridge, auth=None)

    async def authorized(_update):
        return True

    monkeypatch.setattr(handlers, "_authorized", authorized)
    replies, edits = [], []

    class Message:
        text = "/queue do next"
        caption = ""

        async def reply_text(self, text, **kwargs):
            replies.append(text)
            return self

        async def edit_text(self, text, **kwargs):
            edits.append(text)

    class Bot:
        async def send_chat_action(self, **kwargs):
            pass

    update = SimpleNamespace(effective_chat=SimpleNamespace(id=1), effective_message=Message())
    context = SimpleNamespace(args=["do", "next"], bot=Bot())
    if late_arrival:
        prior_release = asyncio.Event()
        prior = asyncio.create_task(prior_release.wait())
        bridge.state_for(1).task = prior
    await handlers.queue(update, context)
    if late_arrival:
        assert bridge.state_for(1).task is prior
        prior_release.set()
        await asyncio.wait_for(handlers._queue_wakeups[1], 5)
    task = bridge.state_for(1).task
    assert task is not None
    await asyncio.wait_for(task, 5)
    assert prompts == ["do next"]
    assert any("queued result" in text for text in [*replies, *edits])
    assert bridge.state_for(1).run_id == run.run_id
    assert not await bridge.run_store.queued_messages(run.run_id)


async def test_tui_late_queue_after_final_claim_wakes_when_cleanup_ends(local_config, tmp_path: Path) -> None:
    store = runs_module.RunStore(tmp_path / "runs")
    run = await store.create_run("task", kind="chat", provider="ollama", model="model", state="done")
    release = asyncio.Event()
    prior = asyncio.create_task(release.wait())
    observed = []
    app = SimpleNamespace(
        run_store=store, _active_run_id=run.run_id, _resumed_run_id=run.run_id,
        _active_task=prior, daemon_client=None, _append_system=lambda _: None,
    )
    for method in ("_start_local_queue", "_wake_local_queue", "_drain_local_queue", "_dispatch_queued_followup"):
        setattr(app, method, MethodType(getattr(LibreClawApp, method), app))

    async def input(message, *, _owned_task=None):
        assert app._active_task is _owned_task is asyncio.current_task()
        observed.append(message)

    app.handle_user_input = input
    await LibreClawApp._handle_task_control(app, "queue", "arrived during cleanup")
    assert app._active_task is prior and not observed
    release.set()
    await asyncio.wait_for(app._local_queue_wakeups[run.run_id], 5)
    next_task = app._active_task
    if next_task is not None:
        await asyncio.wait_for(next_task, 5)
    assert observed == ["arrived during cleanup"]
    assert not await store.queued_messages(run.run_id)
