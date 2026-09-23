# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from libre_claw.config import load_config
from libre_claw.core.orchestration_setup import prepare_orchestration
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.daemon import DaemonServer
from libre_claw.providers.base import Done, LLMProvider, TextDelta, ToolCallReady, Usage
from libre_claw.providers.model_catalog import ModelCatalog


PLUGIN = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/examples/orchestration"


class TeamProvider(LLMProvider):
    def __init__(self, model, harness):
        self.model = model
        self.harness = harness
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        self.harness.requests.append((self.model, copy.deepcopy(messages), kwargs))
        if self.model != "lead":
            self.harness.active += 1
            self.harness.maximum = max(self.harness.maximum, self.harness.active)
            if self.harness.active == 2:
                self.harness.overlap.set()
            try:
                await asyncio.wait_for(self.harness.overlap.wait(), 5)
                await asyncio.sleep(0.03)
                if self.harness.write_mode and self.calls == 1:
                    name = "a.txt" if self.model == "worker-a" else "b.txt"
                    yield ToolCallReady(f"edit-{self.model}", "write_file", {"path": name, "content": self.model})
                else:
                    yield TextDelta(f"Evidence from {self.model}")
                yield Done(usage=Usage(input_tokens=10, output_tokens=5, cost=0.001))
            finally:
                self.harness.active -= 1
            return
        names = {tool["name"] for tool in kwargs["tools"]}
        assert "cordis__orchestration__delegate" in names
        assert not any(name.startswith("subagent_") for name in names)
        if self.calls == 1:
            yield ToolCallReady("team-1", "cordis__orchestration__delegate", {"tasks": self.harness.tasks or [
                {"worker": "scout", "task": "Inspect the first component and return evidence.", "scope": "."},
                {"worker": "reviewer", "task": "Independently inspect the second component.", "scope": "."},
            ]})
        else:
            results = [block for message in messages for block in message.content if block.get("type") == "tool_result"]
            states = json.loads(results[-1]["content"]) if results else []
            if isinstance(states, list) and states and all(state.get("status") == "done" for state in states):
                yield TextDelta("Both independent reports are ready.")
            else:
                yield ToolCallReady(f"team-{self.calls}", "cordis__orchestration__wait", {"timeout": 5})
        yield Done(usage=Usage(input_tokens=20, output_tokens=5, cost=0.002))


class Harness:
    def __init__(self):
        self.requests = []
        self.active = self.maximum = 0
        self.overlap = asyncio.Event()
        self.write_mode = False
        self.tasks = None

    def factory(self, config, *args, **kwargs):
        return TeamProvider(config.general.default_model, self)


@pytest.fixture
async def team_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-test-key")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = load_config(working_directory=workspace)
    config = replace(config, general=replace(config.general, default_provider="openai", default_model="lead"),
        automations=replace(config.automations, enabled=False), memory=replace(config.memory, enabled=False),
        petdex=replace(config.petdex, enabled=False))
    harness = Harness()
    monkeypatch.setattr("libre_claw.providers.factory.create_provider", harness.factory)
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"),
                          provider_factory=harness.factory, start_telegram_bridge=False)
    manager = server.cordis_manager
    manager.install(PLUGIN)
    settings = manager.details("orchestration", workspace)["config"]
    settings["orchestrator"].update(provider="openai", model="lead")
    for worker in settings["workers"]:
        if worker["id"] == "scout":
            worker.update(provider="deepseek", model="worker-a")
        elif worker["id"] == "reviewer":
            worker.update(provider="ollama", model="worker-b")
    manager.configure("orchestration", workspace, settings)
    manager.enable("orchestration", workspace, allow_model=True)
    async with TestClient(TestServer(server.app())) as client:
        yield client, server, harness


async def terminal_run(client, run_id, *, approve=False):
    async with asyncio.timeout(12):
        while True:
            value = await (await client.get(f"/runs/{run_id}")).json()
            if approve:
                for call_id in value["pending_permissions"]:
                    response = await client.post(f"/runs/{run_id}/permissions/{call_id}", json={"resolution": "allow_once"})
                    assert response.status == 200
            if value["run"]["state"] in {"done", "failed", "cancelled"}:
                return value
            await asyncio.sleep(0.01)


async def test_real_plugin_dispatches_concurrent_provider_workers_and_persists_profile(team_app):
    client, server, harness = team_app
    profiles = await (await client.get("/orchestration")).json()
    assert profiles["profiles"][0]["ready"] and not harness.requests
    started = await client.post("/runs", json={"message": "PARENT-ONLY detail. Coordinate two independent inspections.",
                                              "orchestration_plugin": "orchestration"})
    assert started.status == 202, await started.text()
    run_id = (await started.json())["run"]["run_id"]
    finished = await terminal_run(client, run_id)
    events = await server.run_store.load_events(run_id)
    assert finished["run"]["state"] == "done", [(event.type, event.data) for event in events]
    assert finished["run"]["orchestration_plugin"] == "orchestration"
    assert harness.maximum == 2
    children = [(model, messages, options) for model, messages, options in harness.requests if model != "lead"]
    assert {model for model, _, _ in children} == {"worker-a", "worker-b"}
    assert all("PARENT-ONLY" not in repr(messages) and "host-only-test-key" not in repr(options)
               for _, messages, options in children)
    assert all(options["max_tokens"] == 4096 for _, _, options in children)
    session = await server.run_store.load_session(run_id)
    assert session.checkpoint["orchestration"]["profile"]["orchestrator"]["model"] == "lead"
    assert {state["worker_id"] for state in session.subagents.values()} == {"scout", "reviewer"}
    assert all(state["status"] == "done" for state in session.subagents.values())
    public = await (await client.get(f"/runs/{run_id}/session?controls=1")).json()
    assert {item["role"] for item in public["subagents"]} == {"scout", "reviewer"}
    assert {item["name"] for item in public["subagents"]} == {"Scout", "Reviewer"}
    assert all("session" not in item and "role_prompt" not in item for item in public["subagents"])
    assert "orchestration" not in session.control_prompt()
    assert any(event.type == "orchestration_selected" for event in events)


async def test_two_model_builders_edit_disjoint_owned_paths_through_real_plugin(team_app):
    client, server, harness = team_app
    workspace = server.config.general.working_directory
    manager = server.cordis_manager
    config = manager.details("orchestration", workspace)["config"]
    builder = next(worker for worker in config["workers"] if worker["id"] == "builder")
    builder.update(provider="deepseek", model="worker-a")
    config["workers"].append({**builder, "id": "builder2", "name": "Second builder", "provider": "ollama", "model": "worker-b"})
    await manager.configure_async("orchestration", workspace, config)
    harness.write_mode = True
    harness.tasks = [
        {"worker": "builder", "task": "Write a.txt only.", "scope": ".", "write_paths": ["a.txt"]},
        {"worker": "builder2", "task": "Write b.txt only.", "scope": ".", "write_paths": ["b.txt"]},
    ]
    response = await client.post("/runs", json={"message": "Implement independent files", "orchestration_plugin": "orchestration"})
    assert response.status == 202, await response.text()
    run_id = (await response.json())["run"]["run_id"]
    result = await terminal_run(client, run_id, approve=True)
    assert result["run"]["state"] == "done", await (await client.get(f"/runs/{run_id}/events")).text()
    assert harness.maximum == 2
    assert (workspace / "a.txt").read_text() == "worker-a"
    assert (workspace / "b.txt").read_text() == "worker-b"
    session = await server.run_store.load_session(run_id)
    assert all(worker["status"] == "done" and worker["tool_calls"] == 1 for worker in session.subagents.values())
    events = await server.run_store.load_events(run_id)
    assert len([event for event in events if event.type == "permission_response"]) == 2


@pytest.mark.parametrize("bad", [None, {}, {"plugin_id": "orchestration"}])
async def test_invalid_saved_team_snapshot_cannot_resume_without_policy(team_app, bad):
    client, server, harness = team_app
    record = await server.run_store.create_run("Saved team", kind="chat", provider="openai", model="lead",
        working_directory=server.config.general.working_directory, state="done", orchestration_plugin="orchestration")
    session = Session()
    session.checkpoint["orchestration"] = bad
    await server.run_store.save_session(record.run_id, session)
    response = await client.post(f"/runs/{record.run_id}/messages", json={"message": "Continue"})
    assert response.status == 409
    assert not harness.requests


async def test_profile_override_and_unclaimed_import_fail_before_inference(team_app):
    client, _, harness = team_app
    response = await client.post("/runs", json={"message": "Task", "orchestration_plugin": "orchestration", "model": "outside"})
    assert response.status == 400
    response = await client.post("/runs", json={"message": "Task", "session": {"checkpoint": {"orchestration": None}}})
    assert response.status == 400
    assert not harness.requests


async def test_payload_config_preserves_plugin_and_search_settings(team_app):
    _, server, _ = team_app
    server.config = replace(server.config, cordis=replace(server.config.cordis, tool_timeout=7),
                            web_search=replace(server.config.web_search, enabled=False, base_url="http://127.0.0.1:9"))
    derived = await server._config_for_payload({})
    assert derived.cordis == server.config.cordis
    assert derived.web_search == server.config.web_search


async def test_route_check_never_generates_and_rejects_catalog_auth_errors(team_app, monkeypatch):
    client, _, harness = team_app
    async def catalog(config, provider):
        return ModelCatalog(models=(), source="test", error="401 Unauthorized")
    monkeypatch.setattr("libre_claw.web.orchestration_api.discover_models", catalog)
    response = await client.post("/orchestration/check", json={"plugin_id": "orchestration"})
    value = await response.json()
    assert response.status == 200 and value["ready"] is False
    assert all(route["ready"] is False for route in value["routes"])
    assert value["inference_requests"] == 0 and not harness.requests


async def test_unstarted_team_queue_claim_is_returned_after_profile_revocation(team_app):
    _, server, harness = team_app
    session = Session()
    config = prepare_orchestration(server.config, server.cordis_manager, session, selected="orchestration")
    record = await server.run_store.create_run("Queued team", kind="chat", provider="openai", model="lead",
        working_directory=config.general.working_directory, state="done", orchestration_plugin="orchestration")
    await server.run_store.save_session(record.run_id, session)
    await server.run_store.queue_message(record.run_id, "Keep this unstarted follow-up")
    claim = await server.run_store.take_queued_message(record.run_id)
    server.cordis_manager.disable("orchestration", config.general.working_directory)
    state = await server._run_agent(record, claim["message"], config, session=session,
                                    surface="test", continuation=True, queued_claim=claim)
    assert state == "failed" and not harness.requests
    queued = await server.run_store.queued_messages(record.run_id)
    assert queued == [claim]
    events = await server.run_store.load_events(record.run_id)
    assert not any(event.type == "user_message" for event in events)
