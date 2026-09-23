# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest

from libre_claw.core.orchestration import LimitedProvider, bind_orchestration, resolve_profile, validate_profile
from libre_claw.core.agent import Agent, AgentPermissionRequest
from libre_claw.core.session import Session, session_from_payload, session_to_payload
from libre_claw.core.tools import BaseTool, ToolCall
from libre_claw.providers.base import CacheableSystemPrompt, Done, ProviderError, TextDelta, ToolCallReady, Usage
from libre_claw.providers.model_catalog import ModelInfo
from test_subagents import WorkerProvider, make_parent
from test_subagent_recovery import restored_parent


def profile(**updates):
    return {"orchestrator": {"prompt": "COORDINATOR_ONLY", "context_window_tokens": 8192, "max_output_tokens": 512},
            "workers": [
                {"id": "scout", "name": "Source scout", "role": "scout", "read_only": True,
                 "provider": "provider-a", "model": "future/scout", "prompt": "SCOUT_ONLY",
                 "context_window_tokens": 4096, "max_output_tokens": 256},
                {"id": "writer", "name": "Implementation", "role": "worker", "read_only": False,
                 "provider": "provider-b", "model": "future:writer", "context_window_tokens": 8192, "max_output_tokens": 512},
            ], "default_worker": "scout", "max_concurrent": 3, "max_total_workers": 12, **updates}


class RecordedWorker(WorkerProvider):
    def __init__(self, model, *, gate=None, responses=None):
        super().__init__(responses=responses)
        self.model = model
        self.gate = gate
        self.started = asyncio.Event()
        self.calls = []
        self.closed = False
        self.model_info = ModelInfo("fixture", model, model, context_window_tokens=1_000_000,
                                    max_completion_tokens=100_000, supports_tools=True)

    async def ensure_model_info(self):
        return self.model_info

    async def complete(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            for event in self.responses.pop(0):
                yield event
        finally:
            self.closed = True


def team(tmp_path, *, settings=None, gate=None, responses=None):
    created = []
    options = []
    def factory(provider, model, scope, read_only, limits):
        worker = RecordedWorker(model, gate=gate, responses=copy.deepcopy(responses))
        created.append(worker)
        options.append((provider, model, scope, read_only, limits))
        return worker
    parent = make_parent(tmp_path, WorkerProvider(), orchestration_provider_factory=factory)
    allowed = [True]
    controller = bind_orchestration(parent, settings or profile(), lambda: allowed[0])
    return parent, controller, created, options, allowed


def task(worker="scout", **updates):
    return {"worker": worker, "task": "Inspect source\nReport observed evidence.", "scope": ".", **updates}


def test_routes_are_dynamic_and_inherited_once_without_mutating_settings():
    raw = {"workers": [{"id": "inherit", "role": "worker"}]}
    resolved = resolve_profile(raw, default_provider="future-provider", default_model="arbitrary/model:tag")
    assert resolved["orchestrator"]["provider"] == "future-provider"
    assert resolved["workers"][0]["model"] == "arbitrary/model:tag"
    assert raw == {"workers": [{"id": "inherit", "role": "worker"}]}
    with pytest.raises(ValueError, match="worker model"):
        resolve_profile({"workers": [{"id": "other", "provider": "different"}]},
                        default_provider="first", default_model="first-model")


def test_exposing_profile_settings_cannot_mutate_an_active_route(tmp_path):
    parent, controller, _, _, _ = team(tmp_path)
    snapshot = controller.profile
    snapshot["workers"][0]["provider"] = "outside"
    assert controller.resolve_spawn({"task": "inspect", "scope": "."})["provider"] == "provider-a"
    assert "cordis__orchestration__delegate" in parent.system_prompt_extra


@pytest.mark.parametrize("change", [
    {"unknown": True}, {"max_concurrent": True}, {"max_concurrent": 9}, {"max_total_workers": 33},
    {"workers": []}, {"workers": [{"id": "bad", "role": "scout", "read_only": False}]},
    {"workers": [{"id": "duplicate"}, {"id": "duplicate"}]}, {"default_worker": "absent"},
    {"orchestrator": {"context_window_tokens": 4096, "max_output_tokens": 4096}},
])
def test_profile_validation_rejects_ambiguous_or_unbounded_settings(change):
    with pytest.raises(ValueError):
        validate_profile(profile(**change))


async def test_multiple_provider_workers_overlap_and_keep_contexts_and_prompts_separate(tmp_path):
    gate = asyncio.Event()
    parent, controller, workers, settings, _ = team(tmp_path, gate=gate)
    parent.session.add_user_message("PRIVATE_PARENT_TRANSCRIPT")
    try:
        spawned = await controller.dispatch([task(), task("writer", write_paths=["implementation.py"])])
        await asyncio.wait_for(asyncio.gather(*(worker.started.wait() for worker in workers)), 1)
        assert len(workers) == 2 and controller.status()["active"] == 2
        assert [value[:2] for value in settings] == [("provider-a", "future/scout"), ("provider-b", "future:writer")]
        assert {state["worker_id"] for state in spawned} == {"scout", "writer"}
        assert parent.subagents.ownership_error(ToolCall("parent", "write_file", {"path": "implementation.py"}))
        assert parent.subagents.ownership_error(ToolCall("parent", "write_file", {"path": "separate.py"})) is None
        gate.set()
        await asyncio.gather(*(state.task_handle for state in parent.subagents.states.values()))
        assert all(state["status"] == "done" for state in await controller.wait())
        for worker in workers:
            assert "PRIVATE_PARENT_TRANSCRIPT" not in repr(worker.calls)
            assert "COORDINATOR_ONLY" not in worker.calls[0][1]["system"]
            assert len(worker.calls[0][0]) == 1
        assert "SCOUT_ONLY" in workers[0].calls[0][1]["system"]
        assert [worker.calls[0][1]["max_tokens"] for worker in workers] == [256, 512]
        assert parent.context_window_tokens == 8192 and parent.provider.output_limit == 512
    finally:
        await parent.subagents.close()


async def test_native_spawn_cannot_escape_active_routes_or_raise_worker_permissions(tmp_path):
    parent, controller, workers, _, _ = team(tmp_path)
    for overrides in ({"worker": "absent"}, {"worker": False}, {"worker": ""},
                      {"provider": "outside"}, {"model": "outside"}, {"read_only": False}):
        with pytest.raises(ValueError):
            await parent.subagents.spawn(task="inspect", scope=".", **overrides)
    assert workers == []
    schema = parent.tool_registry.get("subagent_spawn").schema()["input_schema"]["properties"]
    assert schema["worker"]["enum"] == ["scout", "writer"]
    assert "provider" not in schema and "model" not in schema
    assert controller.is_read_only([task()]) is True
    assert controller.is_read_only([task("writer", write_paths=["one"])]) is False


@pytest.mark.parametrize("case", ["capacity", "overlap", "unknown", "extra-route", "plan", "deadline"])
async def test_batch_preflight_rejects_every_invalid_assignment_before_starting_any_worker(tmp_path, case):
    settings = profile()
    if case == "capacity":
        settings["max_concurrent"] = 1
    if case == "overlap":
        settings["workers"][1]["max_concurrent"] = 2
    parent, controller, workers, _, _ = team(tmp_path, settings=settings)
    batch = [task(), task("writer", write_paths=["one"])]
    if case == "overlap":
        batch = [task("writer", write_paths=["one"]), task("writer", write_paths=["one/nested"])]
    elif case == "unknown":
        batch[1]["worker"] = "unknown"
    elif case == "extra-route":
        batch[1]["provider"] = "outside"
    elif case == "plan":
        parent.session.mode = "plan"
    elif case == "deadline":
        parent.deadline_monotonic = time.monotonic() - 1
    with pytest.raises(ValueError):
        await controller.dispatch(batch)
    assert workers == [] and parent.subagents.states == {}


async def test_global_and_per_profile_limits_and_turn_dispatch_caps_are_enforced(tmp_path):
    gate = asyncio.Event()
    parent, controller, workers, _, _ = team(tmp_path, gate=gate, settings=profile(max_total_workers=2))
    try:
        first = (await controller.dispatch([task()]))[0]
        with pytest.raises(ValueError, match="concurrency"):
            await controller.dispatch([task()])
        await controller.cancel([first["id"]])
        second = (await controller.dispatch([task()]))[0]
        await controller.cancel([second["id"]])
        with pytest.raises(ValueError, match="dispatch limit"):
            await parent.subagents.spawn(task="another", scope=".")
        assert len(workers) == 2
        parent.subagents.begin_turn()
        gate.set()
        assert len(await controller.dispatch([task()])) == 1
    finally:
        await parent.subagents.close()


async def test_explicit_profile_can_use_more_than_legacy_default_three_slots(tmp_path):
    raw = profile(max_concurrent=4)
    raw["workers"][0]["max_concurrent"] = 4
    gate = asyncio.Event()
    parent, controller, _, _, _ = team(tmp_path, settings=raw, gate=gate)
    try:
        assert len(await controller.dispatch([task() for _ in range(4)])) == 4
    finally:
        await parent.subagents.close()


async def test_builder_and_checkpoint_failures_roll_back_the_entire_batch(tmp_path):
    parent, controller, workers, _, _ = team(tmp_path)
    original = parent.subagents._build_child
    async def failed_builder(state):
        if state.worker_id == "writer":
            raise RuntimeError("second factory failed")
        return await original(state)
    parent.subagents._build_child = failed_builder
    with pytest.raises(RuntimeError, match="factory"):
        await controller.dispatch([task(), task("writer", write_paths=["one"])])
    assert all(not worker.calls for worker in workers) and not parent.subagents.states
    parent.subagents._build_child = original
    async def failed_checkpoint(session):
        raise OSError("checkpoint unavailable")
    parent.checkpoint_callback = failed_checkpoint
    with pytest.raises(OSError, match="checkpoint"):
        await controller.dispatch([task(), task("writer", write_paths=["one"])])
    assert not parent.subagents.states and parent.subagents._spawn_count == 0
    assert parent.session.subagents == {}
    assert all(not worker.calls for worker in workers)


async def test_partially_scheduled_batch_never_reaches_a_provider(tmp_path, monkeypatch):
    parent, controller, workers, _, _ = team(tmp_path)
    original = asyncio.create_task
    scheduled = []
    def create(coroutine, **kwargs):
        if kwargs.get("name", "").startswith("subagent-"):
            if scheduled:
                raise RuntimeError("scheduler failure")
            result = original(coroutine, **kwargs)
            scheduled.append(result)
            return result
        return original(coroutine, **kwargs)
    monkeypatch.setattr(asyncio, "create_task", create)
    with pytest.raises(RuntimeError, match="scheduler"):
        await controller.dispatch([task(), task("writer", write_paths=["one"])])
    assert scheduled[0].done()
    assert not parent.subagents.states and parent.subagents._spawn_count == 0
    assert all(not worker.calls for worker in workers)


async def test_profile_workers_cannot_access_parent_history_network_git_or_other_plugins(tmp_path):
    parent, controller, workers, _, _ = team(tmp_path)
    class PrivateTool(BaseTool):
        name = "memory_search"
        description = "Read unrelated memory"
        parameters = {}
        read_only = True
        async def execute(self):
            raise AssertionError("Private tool must never reach a worker")
    parent.tool_registry.register(PrivateTool(parent.tool_registry.context))
    started = (await controller.dispatch([task()]))[0]
    await parent.subagents.states[started["id"]].task_handle
    names = {tool["name"] for tool in workers[0].calls[0][1]["tools"]}
    assert names == {"read_file", "task_checkpoint"}


async def test_revocation_after_write_approval_request_prevents_the_write(tmp_path):
    parent, controller, workers, _, allowed = team(tmp_path, responses=[
        [ToolCallReady("write", "write_file", {"path": "one", "content": "forbidden"}), Done()],
        [TextDelta("done"), Done()],
    ])
    started = (await controller.dispatch([task("writer", write_paths=["one"])]))[0]
    while True:
        event = await asyncio.wait_for(parent.control_events.get(), 1)
        if isinstance(event, AgentPermissionRequest):
            allowed[0] = False
            event.future.set_result("allow_once")
            break
    await asyncio.wait_for(parent.subagents.states[started["id"]].task_handle, 1)
    assert not (tmp_path / "one").exists()
    assert parent.subagents.ownership_error(ToolCall("parent", "write_file", {"path": "other"}))
    assert len(workers[0].calls) == 1


async def test_worker_snapshot_survives_resume_and_refuses_changed_routes(tmp_path):
    gate = asyncio.Event()
    parent, controller, _, _, _ = team(tmp_path, gate=gate)
    started = (await controller.dispatch([task()]))[0]
    await parent.subagents.close(interrupted=True)
    payload = session_to_payload(parent.session)
    restored = restored_parent(tmp_path, payload, RecordedWorker("future/scout"))
    with pytest.raises(ValueError, match="original orchestration"):
        await restored.subagents.resume(started["id"])
    active = bind_orchestration(restored, profile(), lambda: True)
    await restored.subagents.resume(started["id"])
    await restored.subagents.states[started["id"]].task_handle
    result = (await active.wait())[0]
    assert result["worker_id"] == "scout" and result["role"] == "scout" and result["name"] == "Source scout"
    altered = restored_parent(tmp_path, payload, RecordedWorker("future/scout"))
    changed = profile()
    changed["workers"][0]["model"] = "unapproved-new-model"
    bind_orchestration(altered, changed, lambda: True)
    with pytest.raises(ValueError, match="original orchestration"):
        await altered.subagents.resume(started["id"])


async def test_normalized_model_alias_keeps_selected_route_for_wait_and_resume(tmp_path):
    selected_routes = []
    workers = []
    blocked = asyncio.Event()

    def factory(provider, model, scope, read_only, limits):
        selected_routes.append((provider, model))
        worker = RecordedWorker(
            "future/scout-canonical", gate=blocked if len(workers) == 1 else None,
        )
        workers.append(worker)
        return worker

    parent = make_parent(
        tmp_path, WorkerProvider(), orchestration_provider_factory=factory,
    )
    controller = bind_orchestration(parent, profile(), lambda: True)
    completed = (await controller.dispatch([task()]))[0]
    result = (await controller.wait([completed["id"]], timeout=1))[0]
    assert result["status"] == "done"
    assert result["model"] == "future/scout"
    assert workers[0].model == "future/scout-canonical"

    interrupted = (await controller.dispatch([task()]))[0]
    await workers[1].started.wait()
    assert (await controller.wait([interrupted["id"]]))[0]["model"] == "future/scout"
    await parent.subagents.close(interrupted=True)
    payload = session_to_payload(parent.session)
    assert payload["subagents"][interrupted["id"]]["model"] == "future/scout"

    template = make_parent(
        tmp_path, WorkerProvider(), orchestration_provider_factory=factory,
    )
    restored = Agent(
        session=session_from_payload(payload), provider=WorkerProvider(),
        tool_registry=template.tool_registry,
        permission_manager=template.permission_manager, system_prompt="test system",
    )
    active = bind_orchestration(restored, profile(), lambda: True)
    await restored.subagents.resume(interrupted["id"])
    result = (await active.wait([interrupted["id"]], timeout=1))[0]
    assert result["status"] == "done"
    assert result["model"] == "future/scout"
    assert selected_routes == [("provider-a", "future/scout")] * 3


async def test_provider_limits_preserve_cache_metadata_usage_and_metadata_refresh():
    raw = RecordedWorker("model", responses=[[TextDelta("ok"), Done(Usage(100, 5, cached_tokens=80))]])
    limited = LimitedProvider(raw, context_window_tokens=4096, max_output_tokens=256)
    assert (await limited.ensure_model_info()).context_window_tokens == 4096
    assert limited.model_info.max_completion_tokens == 256
    prompt = CacheableSystemPrompt("stable", "current", cache_scope="scope")
    result = [event async for event in limited.complete([], system=prompt, max_tokens=10000)]
    assert raw.calls[0][1]["system"] is prompt and raw.calls[0][1]["max_tokens"] == 256
    assert result[-1].usage == Usage(100, 5, cached_tokens=80)
    assert raw.model_info.context_window_tokens == 1_000_000


async def test_provider_metadata_can_shrink_but_never_expand_profile_limits():
    raw = RecordedWorker("small")
    raw.auto_context_window = False
    raw.model_info = ModelInfo("fixture", "small", "small", context_window_tokens=2048, max_completion_tokens=128)
    limited = LimitedProvider(raw, context_window_tokens=8192, max_output_tokens=4096)
    assert limited.auto_context_window is True
    assert (await limited.ensure_model_info()).context_window_tokens == 2048
    assert limited.model_info.max_completion_tokens == 128
    _ = [event async for event in limited.complete([], max_tokens=2000)]
    assert raw.calls[0][1]["max_tokens"] == 128
    raw.model_info = ModelInfo("fixture", "small", "small", context_window_tokens=1024)
    assert limited.model_info.max_completion_tokens == 1024


async def test_worker_factory_receives_reasoning_and_profile_request_limits(tmp_path):
    settings = profile()
    settings["workers"][0]["reasoning_effort"] = "high"
    parent, controller, _, options, _ = team(tmp_path, settings=settings)
    started = (await controller.dispatch([task()]))[0]
    await parent.subagents.states[started["id"]].task_handle
    assert options[0][4] == {"reasoning_effort": "high", "context_window_tokens": 4096, "max_output_tokens": 256}


async def test_profile_provider_failure_never_falls_back_or_retries(tmp_path):
    parent, controller, workers, _, _ = team(tmp_path, responses=[[ProviderError("429 quota exceeded")]])
    started = (await controller.dispatch([task()]))[0]
    await parent.subagents.states[started["id"]].task_handle
    assert len(workers[0].calls) == 1
    assert parent.subagents.states[started["id"]].status == "failed"
    assert parent.fallback_providers == () and parent.provider_retry_attempts == 0


def test_controller_cannot_be_reused_with_a_different_parent_session(tmp_path):
    parent, controller, _, _, _ = team(tmp_path)
    parent.session = Session()
    with pytest.raises(PermissionError):
        controller.status()


def test_large_worker_reports_remain_bounded_and_signal_partial_detail(tmp_path):
    _, controller, _, _, _ = team(tmp_path)
    workers = [{"id": str(index), "worker_id": "scout", "role": "scout", "name": "Scout",
                "task": "界" * 16000, "output": "界" * 16000,
                "write_paths": ["some/path"], "scope": str(tmp_path), "provider": "a", "model": "b"}
               for index in range(32)]
    reports = controller._reports(workers)
    assert len(json.dumps(reports, ensure_ascii=True).encode()) < 180 * 1024
    assert all(report["details_truncated"] for report in reports)
    assert controller._reports([workers[0]])[0]["output"] == workers[0]["output"]
