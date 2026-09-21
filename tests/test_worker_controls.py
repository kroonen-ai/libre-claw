# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session, session_from_payload, session_to_payload
from libre_claw.core.task_control import request_subagent_resume, saved_subagent_snapshots
from libre_claw.daemon import DaemonServer
from libre_claw.telegram.bridge import TelegramBridge
from test_subagent_recovery import interrupted_payload


class Request:
    def __init__(self, run_id: str, data: dict | None = None, *, compact: bool = False):
        self.match_info = {"run_id": run_id}
        self.query = {"controls": "1"} if compact else {}
        self.data = data or {}

    async def json(self):
        return self.data


def test_worker_resume_requests_survive_roundtrip_and_reject_plan_writes(tmp_path: Path) -> None:
    payload, worker_id = interrupted_payload(tmp_path, read_only=False)
    session = session_from_payload(payload)
    session.mode = "plan"
    with pytest.raises(ValueError, match="Plan mode"):
        request_subagent_resume(session, worker_id)
    session.mode = "default"
    request_subagent_resume(session, f"{worker_id} inspect before editing")
    request_subagent_resume(session, f"{worker_id} preserve completed work")
    restored = session_from_payload(session_to_payload(session))
    assert restored.pending_subagent_resumes == [{"id": worker_id, "guidance": "preserve completed work"}]
    assert restored.subagents[worker_id]["session"]["messages"]
    public = saved_subagent_snapshots(restored)[0]
    assert "session" not in public
    assert public["status"] == "interrupted"
    assert public["resume_pending"] is True
    restored.subagents[worker_id]["status"] = "done"
    with pytest.raises(ValueError, match="already completed"):
        request_subagent_resume(restored, worker_id)


async def test_compact_controls_exclude_private_history_and_resume_is_durable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"))
    run = await server.run_store.create_run("Saved parent", kind="chat", provider="openai", model="custom-model", working_directory=tmp_path, state="done")
    payload, worker_id = interrupted_payload(tmp_path)
    session = session_from_payload(payload)
    session.add_user_message("private-parent-history " * 2000)
    session.subagents[worker_id]["session"]["messages"].append({"role": "assistant", "content": [{"type": "provider_reasoning", "text": "private-child-reasoning"}]})
    await server.run_store.save_session(run.run_id, session)
    await server.run_store.queue_message(run.run_id, "long follow-up " * 1000)
    compact = await server.get_session(Request(run.run_id, compact=True))
    public = json.loads(compact.text)
    assert "private-parent-history" not in compact.text
    assert "private-child-reasoning" not in compact.text
    assert public["subagents"][0]["status"] == "interrupted"
    assert len(public["queued"][0]["message"]) == 1000
    assert public["queued"][0]["truncated"] is True
    full = json.loads((await server.get_session(Request(run.run_id))).text)
    assert "private-parent-history" in full["session"]["messages"][0]["content"][0]["text"]
    scheduled = []
    monkeypatch.setattr(server, "_schedule_queue_wakeup", scheduled.append)
    response = await server.control_run(Request(run.run_id, {"action": "agent_resume", "text": f"{worker_id} inspect saved state"}))
    assert response.status == 200
    saved = await server.run_store.load_session(run.run_id)
    assert saved.pending_subagent_resumes == [{"id": worker_id, "guidance": "inspect saved state"}]
    assert scheduled == [run.run_id]
    queued = await server.run_store.queued_messages(run.run_id)
    assert any(worker_id in item["message"] for item in queued)


async def test_local_telegram_worker_resume_queues_on_saved_parent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, memory=replace(config.memory, enabled=False))
    bridge = TelegramBridge(config)
    bridge.run_store = RunStore(tmp_path / "runs")
    run = await bridge.run_store.create_run("Saved parent", kind="chat", provider="openai", model="custom-model", working_directory=tmp_path, state="done")
    payload, worker_id = interrupted_payload(tmp_path)
    state = bridge.state_for(123)
    state.run_id = run.run_id
    state.session = session_from_payload(payload)
    response = await bridge.task_control_text(123, "agent_resume", f"{worker_id} verify first")
    assert "resume queued" in response
    saved = await bridge.run_store.load_session(run.run_id)
    assert saved.pending_subagent_resumes[0]["guidance"] == "verify first"
    assert worker_id in (await bridge.run_store.queued_messages(run.run_id))[0]["message"]


async def test_finalizing_parent_worker_resume_creates_followup(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from libre_claw.daemon import ActiveRun

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(load_config(), run_store=RunStore(tmp_path / "runs"))
    run = await server.run_store.create_run("Finishing parent", kind="chat", provider="openai", model="custom-model", working_directory=tmp_path, state="running")
    payload, worker_id = interrupted_payload(tmp_path)
    server._active_sessions[run.run_id] = session_from_payload(payload)
    server._active_agents[run.run_id] = SimpleNamespace(subagents=None, accepting_control=False)
    hold = asyncio.Event()
    task = asyncio.create_task(hold.wait())
    server.active_runs[run.run_id] = ActiveRun(run.run_id, task)
    scheduled = []
    monkeypatch.setattr(server, "_schedule_queue_wakeup", scheduled.append)
    try:
        response = await server.control_run(Request(run.run_id, {"action": "agent_resume", "text": worker_id}))
        assert response.status == 200
        assert scheduled == [run.run_id]
        assert worker_id in (await server.run_store.queued_messages(run.run_id))[0]["message"]
    finally:
        hold.set()
        await task
