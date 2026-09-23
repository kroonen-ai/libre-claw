# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from libre_claw.config import load_config
from libre_claw.core.agent import AgentDone
from libre_claw.core.cordis_bindings import BoundStore, MEMORY_METHODS, RUN_METHODS
from libre_claw.core.cordis_engine import CordisEngine, CordisEngineError
from libre_claw.core.goal import GoalComplete, GoalRunner
from libre_claw.core.memory import MemoryStore, extract_memories_with_provider
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.daemon import DaemonServer
from libre_claw.providers.base import Done, LLMProvider, TextDelta


class ControlledProvider(LLMProvider):
    def __init__(self, text="", *, block=False):
        self.text = text
        self.block = block
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def complete(self, **kwargs):
        self.started.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            yield TextDelta(self.text)
            yield Done()
        finally:
            self.closed.set()


class GoalAgent:
    async def run(self, prompt):
        yield AgentDone()


async def test_storage_binding_enforces_service_state_and_follows_replacement(tmp_path):
    raw = MemoryStore(tmp_path / "memory.db")
    first = CordisEngine(components={"memory": False})
    current = [first]
    store = BoundStore(raw, lambda: current[0], MEMORY_METHODS)
    try:
        with pytest.raises(CordisEngineError, match="disabled"):
            await store.add_fact("Must not be persisted")
        assert not raw.path.exists()
        second = CordisEngine()
        current[0] = second
        try:
            await store.add_fact("Bound through the replacement")
            assert [item.fact for item in await raw.list_facts()] == ["Bound through the replacement"]
            graph = await second.inspect()
            memory = next(item for item in graph["components"] if item["id"] == "memory")
            assert memory["completed"] == 1
            await second.aclose()
            with pytest.raises(CordisEngineError, match="closed"):
                await store.add_fact("Must not fall back")
            assert len(await raw.list_facts()) == 1
        finally:
            await second.aclose()
    finally:
        await first.aclose()


async def test_store_cancellation_joins_implementation_cleanup_and_releases_operation():
    class Store:
        started = asyncio.Event()
        closed = asyncio.Event()

        async def save_session(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.closed.set()

    raw = Store()
    async with CordisEngine() as engine:
        store = BoundStore(raw, lambda: engine, RUN_METHODS)
        task = asyncio.create_task(store.save_session())
        await asyncio.wait_for(raw.started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert raw.closed.is_set()
        assert (await engine.inspect())["active_operations"] == 0


async def test_goal_lifecycle_and_judge_use_workflow_and_provider_services():
    provider = ControlledProvider('{"done":true,"confidence":1,"reason":"verified"}')
    async with CordisEngine() as engine:
        runner = GoalRunner(GoalAgent(), provider, Session(), "Finish", engine=engine)
        events = [event async for event in runner.run()]
        assert any(isinstance(event, GoalComplete) for event in events)
        assert provider.closed.is_set()
        graph = {item["id"]: item for item in (await engine.inspect())["components"]}
        assert graph["workflows"]["completed"] == 1
        assert graph["providers"]["completed"] == 1


async def test_goal_judge_cannot_bypass_disabled_provider_service():
    provider = ControlledProvider()
    async with CordisEngine(components={
        "providers": False, "memory": False, "agent": False, "workflows": False,
    }) as engine:
        runner = GoalRunner(GoalAgent(), provider, Session(), "Finish", engine=engine)
        with pytest.raises(CordisEngineError, match="disabled"):
            await runner._judge(1)
        assert not provider.started.is_set()


async def test_memory_extraction_is_gated_and_cancellation_closes_provider():
    provider = ControlledProvider(block=True)
    async with CordisEngine(components={"memory": False}) as engine:
        with pytest.raises(CordisEngineError, match="disabled"):
            await extract_memories_with_provider(
                provider, engine=engine, user_message="Remember", assistant_text="A fact",
            )
        assert not provider.started.is_set()
        await engine.configure({"memory": True})
        task = asyncio.create_task(extract_memories_with_provider(
            provider, engine=engine, user_message="Remember", assistant_text="A fact",
        ))
        await asyncio.wait_for(provider.started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.closed.is_set()
        assert (await engine.inspect())["active_operations"] == 0


async def test_failed_engine_blocks_dispatch_but_leaves_recovery_controls_available(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    config = replace(config, automations=replace(config.automations, enabled=False),
                     memory=replace(config.memory, enabled=False), petdex=replace(config.petdex, enabled=False))
    provider = ControlledProvider()
    raw = RunStore(tmp_path / "runs")
    saved = await raw.create_run("recoverable", kind="chat", provider="fake", model="fake")
    server = DaemonServer(config, run_store=raw, provider_factory=lambda _: provider,
                          start_telegram_bridge=False)
    async with TestClient(TestServer(server.app())) as client:
        await server.engine.aclose()
        denied = await client.post("/runs", json={"message": "Must not start"})
        assert denied.status == 503
        assert not provider.started.is_set()
        assert len(await raw.list_runs()) == 1
        assert (await client.get(f"/runs/{saved.run_id}")).status == 200
        assert (await client.get(f"/runs/{saved.run_id}/events")).status == 200
        cancelled = await client.post(f"/runs/{saved.run_id}/cancel", json={})
        assert cancelled.status == 200
        assert (await raw.load_run(saved.run_id)).state == "cancelled"


async def test_engine_loss_closes_active_provider_and_persists_recoverable_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    config = replace(config, automations=replace(config.automations, enabled=False),
                     memory=replace(config.memory, enabled=False), petdex=replace(config.petdex, enabled=False))
    provider = ControlledProvider(block=True)
    raw = RunStore(tmp_path / "runs")
    server = DaemonServer(config, run_store=raw, provider_factory=lambda _: provider,
                          start_telegram_bridge=False)
    async with TestClient(TestServer(server.app())) as client:
        started = await client.post("/runs", json={"message": "Keep the interrupted task"})
        assert started.status == 202, await started.text()
        run_id = (await started.json())["run"]["run_id"]
        await asyncio.wait_for(provider.started.wait(), 5)
        await server.engine.aclose()
        async with asyncio.timeout(5):
            while (await raw.load_run(run_id)).state not in {"failed", "cancelled"}:
                await asyncio.sleep(0.01)
        assert provider.closed.is_set()
        session = await raw.load_session(run_id)
        assert session.messages[0].content[0]["text"] == "Keep the interrupted task"
        assert (await client.get(f"/runs/{run_id}")).status == 200


async def test_automation_finalizer_is_gated_before_inference(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    provider = ControlledProvider("A report")
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"),
                          provider_factory=lambda _: provider, start_telegram_bridge=False)
    server.engine = CordisEngine(components={
        "providers": False, "memory": False, "agent": False, "workflows": False,
    })
    try:
        automation = await server._recovery_automation_store.create(
            name="Report", prompt="Inspect", schedule="every 60 minutes", route="report",
        )
        run = await server._recovery_run_store.create_run(
            "Partial", kind="automation", provider="fake", model="fake", state="failed",
        )
        assert await server._finalize_partial_automation(automation, run, config, "failed") is False
        assert not provider.started.is_set()
        assert any(event.type == "automation_finalizer_error"
                   for event in await server._recovery_run_store.load_events(run.run_id))
    finally:
        await server.engine.aclose()
