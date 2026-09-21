# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session, session_to_payload, session_from_payload, tool_use_block, tool_result_block
from libre_claw.core.task_control import update_plan
from libre_claw.providers.base import Done, LLMProvider, TextDelta
from libre_claw.telegram.bridge import TelegramBridge
from libre_claw.tui.app import LibreClawApp


class EchoProvider(LLMProvider):
    async def complete(self, *args, **kwargs):
        yield TextDelta("done")
        yield Done()


def test_structured_checkpoint_and_originals_survive_repeated_compaction() -> None:
    session = Session(mode="plan")
    objective = "Keep the public API compatible while fixing pagination."
    session.add_user_message(objective)
    session.update_checkpoint({"decisions": ["Use opaque cursors."], "verification": ["Pagination boundary regression passed."], "outstanding": ["Check the final page."], "last_turn_tree": "abc123"})
    update_plan(session, "set inspect; fix; test")
    for i in range(80):
        session.add_user_message(f"Follow-up {i}")
        session.add_assistant_message("long observation " * 400)
        session.compact(keep_last=4)
    restored = session_from_payload(session_to_payload(session))
    assert restored.checkpoint["objective"] == objective
    assert restored.checkpoint["decisions"] == ["Use opaque cursors."]
    assert "Check the final page." in restored.checkpoint["outstanding"]
    assert restored.checkpoint["last_turn_tree"] == "abc123"
    assert objective in restored.summary
    assert len(restored.summary) <= 12000
    assert restored.archived_messages[0].content[0]["text"] == objective
    assert restored.mode == "plan"
    assert len(restored.plan_steps) == 3


def test_compaction_keeps_tool_exchanges_and_repairs_interrupted_requests() -> None:
    session = Session()
    session.add_user_message("Change the file.")
    session.add_assistant_blocks([tool_use_block("write-1", "file_write", {"path": "x.txt", "content": "hello"})])
    session.add_tool_result_blocks([tool_result_block("write-1", "Written")])
    session.add_assistant_message("Done")
    session.compact(keep_last=2)
    assert session.messages[0].content[0]["type"] == "tool_use"
    session.add_assistant_blocks([tool_use_block("write-2", "file_write", {"path": "x.txt", "content": "second"})])
    restored = session_from_payload(session_to_payload(session))
    restored.recover_interrupted_tools()
    restored.recover_interrupted_tools()
    results = [block for message in restored.messages for block in message.content if block.get("tool_use_id") == "write-2"]
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "inspect current state" in results[0]["content"]


def test_viewing_plan_never_erases_outstanding_work() -> None:
    session = Session()
    session.update_checkpoint({"outstanding": ["Verify migration rollback"]})
    for action in ("show", "on", "off"):
        update_plan(session, action)
        assert session.checkpoint["outstanding"] == ["Verify migration rollback"]
    update_plan(session, "set first; second")
    update_plan(session, "done 1")
    assert session.checkpoint["outstanding"] == ["Verify migration rollback", "second"]
    with pytest.raises(ValueError):
        update_plan(session, "edit 0 nope")


async def test_tui_resume_restores_model_workspace_session_and_plan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.tui.app.create_provider", lambda *args, **kwargs: EchoProvider())
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Fix pagination", kind="chat", provider="openai", model="future-model", working_directory=workspace, state="done")
    session = Session()
    session.add_user_message("Keep compatibility")
    session.add_assistant_message("First step finished")
    update_plan(session, "set finish implementation; verify")
    session.queue_steering("Also check empty input")
    await store.save_session(run.run_id, session)
    app = LibreClawApp(config=load_config())
    app.run_store = store
    async with app.run_test():
        await app._handle_resume_command(run.run_id)
        assert app.config.general.default_provider == "openai"
        assert app.config.general.default_model == "future-model"
        assert app.config.general.working_directory == workspace
        assert app.agent.session is app.session
        assert app.session.messages[0].content[0]["text"] == "Keep compatibility"
        assert app.session.pending_steering == ["Also check empty input"]
        assert len(app.session.plan_steps) == 2
        continued = await app._start_run("chat", "finish it")
        assert continued.run_id == run.run_id
        await app._finish_active_run("done", summary="done")
    restored = await RunStore(tmp_path / "runs").load_session(run.run_id)
    assert restored.checkpoint["objective"] == "Keep compatibility"


async def test_telegram_resume_does_not_change_another_chats_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.telegram.bridge.create_provider", lambda *args, **kwargs: EchoProvider())
    config = load_config()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    config = replace(config, general=replace(config.general, working_directory=first))
    bridge = TelegramBridge(config)
    bridge.run_store = RunStore(tmp_path / "runs")
    run = await bridge.run_store.create_run("Other project", kind="chat", provider="openai", model="future-model", working_directory=second, state="done")
    session = Session()
    session.add_user_message("Second project context")
    await bridge.run_store.save_session(run.run_id, session)
    assert (await bridge.resume_command_text(1, run.run_id)).startswith("Resumed")
    assert bridge.config.general.working_directory == first
    assert bridge._create_agent(bridge.state_for(1)).tool_registry.context.working_directory == second
    assert bridge._create_agent(bridge.state_for(2)).tool_registry.context.working_directory == first
    assert bridge.state_for(1).session.messages[0].content[0]["text"] == "Second project context"
