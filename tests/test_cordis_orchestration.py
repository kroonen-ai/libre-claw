# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from libre_claw.core.cordis import CordisError, CordisManager
from libre_claw.core.cordis_host import CordisHost
from libre_claw.core.cordis_packages import CordisPackagePreviews, catalog
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.cordis_worker import CordisWorker
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolContext


SOURCE = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/examples/orchestration"


class Controller:
    plugin_id = "orchestration"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.calls: list[tuple[str, object]] = []
        self.allowed = True

    def authorize(self) -> bool:
        if not self.allowed:
            raise PermissionError("Task access revoked")
        return True

    def tasks_read_only(self, tasks) -> bool:
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("Missing assignments")
        return all(task["worker"] != "builder" or task.get("read_only") is True for task in tasks)

    async def dispatch(self, tasks):
        self.calls.append(("dispatch", tasks))
        return [{"id": "worker-1", "state": "running"}]

    async def wait(self, ids=None, timeout=0):
        self.calls.append(("wait", {"ids": ids, "timeout": timeout}))
        return [{"id": "worker-1", "state": "done", "summary": "bounded result"}]

    async def cancel(self, ids=None):
        self.calls.append(("cancel", ids))
        return [{"id": "worker-1", "state": "cancelled"}]

    def status(self):
        self.calls.append(("status", None))
        return {"active": 0, "total_dispatched": 1}


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "workspace"
    directory.mkdir()
    return directory


@pytest.fixture
def manager(tmp_path):
    return CordisManager(root=tmp_path / "registry")


def install_profile(manager, workspace, *, allow_model=True):
    installed = manager.install(SOURCE)
    manager.enable("orchestration", workspace, allow_model=allow_model)
    return installed


def task_context(workspace, controller=None):
    session = controller.session if controller else Session()
    state = {"agent_session": session}
    if controller is not None:
        state["orchestration_controller"] = controller
    return ToolContext(workspace, shared_state=state)


async def test_catalog_and_preview_include_declarative_profile_without_starting_models(manager, workspace):
    entry = next(item for item in catalog() if item["id"] == "orchestration")
    assert entry["source"] == "builtin:orchestration"
    assert entry["requires_model_access"] is True
    assert entry["offline"] is True
    preview = manager.preview(SOURCE)
    assert preview["tools"] == ["delegate", "wait", "status", "cancel"]
    assert preview["requires_model_access"] is True
    previews = CordisPackagePreviews(manager)
    try:
        checked = await previews.preview("builtin:orchestration", workspace)
        assert checked["id"] == "orchestration"
        assert checked["requires_model_access"] is True
        assert manager.list_plugins(workspace) == []
    finally:
        previews.close()


def test_profile_loading_requires_bundled_source_enable_and_model_access(manager, workspace):
    manager.install(SOURCE)
    with pytest.raises(CordisError, match="model access"):
        manager.orchestration_profile(workspace)
    manager.enable("orchestration", workspace)
    with pytest.raises(CordisError, match="model access"):
        manager.orchestration_profile(workspace)
    manager.enable("orchestration", workspace, allow_model=True)
    profile = manager.orchestration_profile(workspace)
    assert profile["plugin_id"] == "orchestration"
    assert len(profile["digest"]) == 64
    assert profile["authorize"]() is True
    assert [worker["id"] for worker in profile["config"]["workers"]] == ["scout", "builder", "reviewer"]
    assert all(worker["provider"] == worker["model"] == "" for worker in profile["config"]["workers"])
    assert profile["config"]["orchestrator"]["provider"] == profile["config"]["orchestrator"]["model"] == ""
    assert manager._workers == {}
    assert manager.details("orchestration", workspace)["requires_model_access"] is True


def test_profile_authorization_revokes_on_config_change_and_disable(manager, workspace):
    install_profile(manager, workspace)
    profile = manager.orchestration_profile(workspace)
    manager.configure("orchestration", workspace, {"max_total_workers": 8})
    with pytest.raises(CordisError, match="changed"):
        profile["authorize"]()
    latest = manager.orchestration_profile(workspace)
    manager.disable("orchestration", workspace)
    with pytest.raises(CordisError, match="model access"):
        latest["authorize"]()


def test_copied_orchestration_id_cannot_claim_trusted_behavior(manager, workspace, tmp_path):
    fork = tmp_path / "fork"
    shutil.copytree(SOURCE, fork)
    with (fork / "plugin.mjs").open("a") as file:
        file.write("\n// Modified plugin source.\n")
    manager.install(fork)
    manager.enable("orchestration", workspace, allow_model=True)
    with pytest.raises(CordisError, match="unchanged included"):
        manager.orchestration_profile(workspace)
    controller = Controller(Session())
    for tool in manager.create_tools(task_context(workspace, controller)):
        assert tool.permission_level == "ask"
        assert tool.is_read_only({"tasks": [{"worker": "scout", "task": "Inspect", "scope": "."}]}) is False


def test_plan_mode_and_permission_shortcut_require_an_active_matching_controller(manager, workspace):
    install_profile(manager, workspace)
    context = task_context(workspace)
    tools = {tool.tool_name: tool for tool in manager.create_tools(context)}
    assert all(tool.permission_level == "ask" for tool in tools.values())
    assert all(tool.is_read_only({}) is False for tool in tools.values())
    controller = Controller(context.shared_state["agent_session"])
    context.shared_state["orchestration_controller"] = controller
    assert all(tool.permission_level == "allow" for tool in tools.values())
    assert all(tools[name].is_read_only({}) for name in ("status", "wait", "cancel"))
    assert tools["delegate"].is_read_only({"tasks": [{"worker": "scout", "task": "Inspect", "scope": "."}]})
    assert not tools["delegate"].is_read_only({"tasks": [{"worker": "builder", "task": "Change", "scope": "."}]})
    controller.session = Session()
    assert all(tool.permission_level == "ask" for tool in tools.values())
    assert tools["status"].is_read_only({}) is False


@pytest.mark.parametrize("case", ["no-model", "untrusted-source", "background", "wrong-plugin", "different-session", "revoked"])
async def test_host_broker_rejects_unselected_or_untrusted_delegation(workspace, case):
    controller = Controller(Session())
    context = task_context(workspace, controller)
    if case == "different-session":
        context.shared_state["agent_session"] = Session()
    if case == "revoked":
        controller.allowed = False
    host = CordisHost("other" if case == "wrong-plugin" else "orchestration",
                      lambda: {"allow_model": case != "no-model"},
                      context=None if case == "background" else context,
                      orchestration_authorize=None if case == "untrusted-source" else lambda: True)
    with pytest.raises(PermissionError):
        await host.dispatch("orchestration.dispatch", {"tasks": [{"worker": "scout", "task": "Inspect", "scope": "."}]})
    assert controller.calls == []


async def test_host_broker_forwards_only_scoped_operations_and_bounds_wait(workspace):
    controller = Controller(Session())
    context = task_context(workspace, controller)
    host = CordisHost("orchestration", lambda: {"allow_model": True}, context=context,
                      orchestration_authorize=lambda: True)
    tasks = [{"worker": "scout", "task": "Inspect one file", "scope": "."}]
    assert (await host.dispatch("orchestration.dispatch", {"tasks": tasks}))[0]["id"] == "worker-1"
    assert (await host.dispatch("orchestration.wait", {"ids": ["worker-1"], "timeout": 1}))[0]["state"] == "done"
    assert (await host.dispatch("orchestration.status", {}))["active"] == 0
    assert (await host.dispatch("orchestration.cancel", {"ids": ["worker-1"]}))[0]["state"] == "cancelled"
    for timeout in (-1, 61, float("nan"), True, "1"):
        with pytest.raises(ValueError):
            await host.dispatch("orchestration.wait", {"timeout": timeout})
    with pytest.raises(PermissionError):
        await host.dispatch("orchestration.status", {"session_id": "other"})
    with pytest.raises(PermissionError):
        await host.dispatch("orchestration.execute", {})
    assert [name for name, _ in controller.calls] == ["dispatch", "wait", "status", "cancel"]


async def test_host_plan_mode_cannot_bypass_dispatch_read_only_policy(workspace):
    session = Session(mode="plan")
    controller = Controller(session)
    host = CordisHost("orchestration", lambda: {"allow_model": True}, context=task_context(workspace, controller),
                      orchestration_authorize=lambda: True)
    with pytest.raises(PermissionError, match="read-only"):
        await host.dispatch("orchestration.dispatch", {"tasks": [{"worker": "builder", "task": "Modify", "scope": "."}]})
    assert controller.calls == []


async def test_host_cannot_follow_a_rebound_session_into_another_task(workspace):
    first = Controller(Session())
    context = task_context(workspace, first)
    host = CordisHost("orchestration", lambda: {"allow_model": True}, context=context,
                      orchestration_authorize=lambda: True)
    replacement = Controller(Session())
    context.shared_state.update(agent_session=replacement.session, orchestration_controller=replacement)
    with pytest.raises(PermissionError, match="task changed"):
        await host.dispatch("orchestration.status", {})
    assert first.calls == replacement.calls == []


async def test_included_plugin_executes_real_offline_runtime_and_host_broker(manager, workspace, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    try:
        prepare_cordis_process(node, manager.runtime_path, SOURCE, tmp_path / "probe")
    except CordisSecurityError as error:
        pytest.skip(str(error))
    install_profile(manager, workspace)
    controller = Controller(Session())
    context = task_context(workspace, controller)
    tools = {tool.tool_name: tool for tool in manager.create_tools(context)}
    delegated = await tools["delegate"].execute(tasks=[{"worker": "scout", "task": "Inspect only this task", "scope": "."}])
    assert delegated.error is None
    assert json.loads(delegated.content)[0]["id"] == "worker-1"
    assert (await tools["wait"].execute(ids=["worker-1"], timeout=0)).error is None
    assert (await tools["status"].execute()).error is None
    assert (await tools["cancel"].execute(ids=["worker-1"])).error is None
    assert [method for method, _ in controller.calls] == ["dispatch", "wait", "status", "cancel"]


async def test_bounded_worker_wait_preserves_the_remaining_plugin_deadline(manager, workspace, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    state = tmp_path / "wait-state"
    try:
        prepared = prepare_cordis_process(node, manager.runtime_path, SOURCE, state)
    except CordisSecurityError as error:
        pytest.skip(str(error))

    class WaitingController(Controller):
        async def wait(self, ids=None, timeout=0):
            await asyncio.sleep(0.1)
            return await super().wait(ids, timeout)

    controller = WaitingController(Session())
    host = CordisHost("orchestration", lambda: {"allow_model": True},
                      context=task_context(workspace, controller), orchestration_authorize=lambda: True)

    async def handler(method, params, *, stream, timeout):
        assert not stream
        assert timeout is not None
        host.timeout = timeout
        # Start the small remaining budget at the actual host boundary rather
        # than making process scheduling on slower CI machines part of the test.
        timeout.reschedule(asyncio.get_running_loop().time() + 0.03)
        return await host.dispatch(method, params)

    async with CordisWorker(prepared, {"entry": str(SOURCE / "plugin.mjs"),
                                      "plugin_id": "orchestration", "state_dir": str(state)}) as worker:
        result = await worker.request("tools/call", {"name": "wait", "arguments": {"timeout": 1},
                                                     "context": host.execution}, handler)
        assert result["error"] is False
        assert json.loads(result["content"])[0]["state"] == "done"
        assert worker.running
        assert host.timeout.when() is not None
        assert not host.timeout.expired()
