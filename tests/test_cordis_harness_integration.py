# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from libre_claw.core.cordis import CordisError, CordisManager, MANIFEST_NAME
from libre_claw.core.cordis import _workspace
from libre_claw.core.cordis_harness import adapt_harness_package
from libre_claw.core.cordis_host import CordisHost, tool_attachments
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.cordis_packages import CordisPackagePreviews, _package_files, catalog
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolCall, ToolContext, ToolRegistry
from libre_claw.config import load_config


RUNTIME = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime"


@pytest.fixture
def runtime_supported(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    probe = tmp_path / "probe"
    probe.mkdir()
    try:
        prepare_cordis_process(node, RUNTIME / "runtime.mjs", probe, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(f"Enforced offline Node permissions unavailable: {exc}")


@pytest.fixture
def workspace(tmp_path):
    result = tmp_path / "workspace"
    result.mkdir()
    return result


def install(manager, directory, modules, rows, *, package_name="@fixture/host-tools"):
    package = {"name": package_name, "version": "1.0.0", "type": "module",
               "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}}
    files = {"package.json": json.dumps(package).encode(),
             "cordis.patch.yml": json.dumps([{"insert": rows}]).encode(), **modules}
    adapted = adapt_harness_package(files)
    directory.mkdir()
    for name, content in adapted.items():
        target = directory / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(content)
    return manager.install(directory)["id"]


def native_tools(manager, tmp_path):
    fixtures = RUNTIME / "compat/tests/fixtures"
    return install(manager, tmp_path / "source", {
        "ask.mjs": (fixtures / "harness-ask-user.mjs").read_bytes(),
        "todo.mjs": (fixtures / "harness-todo.mjs").read_bytes(),
    }, [{"id": "ask", "name": "./ask.mjs"},
        {"id": "todo", "name": "./todo.mjs", "config": {"allowParallelInProgress": True}}])


async def test_unchanged_harness_tools_activate_after_install_then_broker_questions_and_todos(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = native_tools(manager, tmp_path)
    before = manager.details(plugin_id, workspace)
    assert before["format"] == "deepseek-harness" and before["enabled"] is False and before["tools"] == []
    assert not (manager.root / "state").exists()
    active = await manager.enable_async(plugin_id, workspace)
    assert set(active["tools"]) == {"ask_user_question", "todo_write"}
    assert active["grants"]["allow_model"] is False
    assert active["runtime_lifetime"] == "per-call"
    session = Session()
    asked, persisted = [], []
    async def answer(questions):
        asked.extend(questions)
        return {"answers": [{"id": "choice", "selected": ["Yes"]}]}
    async def checkpoint(current):
        persisted.append(json.loads(json.dumps(current.checkpoint)))
    context = ToolContext(workspace, shared_state={"agent_session": session, "user_question_handler": answer,
                                                 "checkpoint_callback": checkpoint})
    registry = ToolRegistry(manager.create_tools(context))
    question_tool = next(tool for tool in registry.tools() if tool.tool_name == "ask_user_question")
    result = await registry.execute(ToolCall("question-call", question_tool.name, {"questions": [
        {"id": "choice", "question": "Continue?", "options": [{"label": "Yes"}, {"label": "No"}]},
    ]}))
    assert not result.is_error, result.error
    assert asked[0]["question"] == "Continue?" and "Yes" in result.content
    todo_tool = next(tool for tool in registry.tools() if tool.tool_name == "todo_write")
    result = await registry.execute(ToolCall("todo-call", todo_tool.name, {"todos": [
        {"content": "Finish integration", "status": "in_progress"},
        {"content": "Review privacy", "status": "completed"},
    ]}))
    assert not result.is_error, result.error
    assert session.plan_steps == [{"text": "Finish integration", "status": "running"}, {"text": "Review privacy", "status": "done"}]
    assert session.checkpoint["outstanding"] == ["Finish integration"]
    assert persisted[-1]["cordis_events"][plugin_id][0]["type"] == "todo/write"
    assert (await manager.inspect(plugin_id, workspace))["runtime_components"][0]["state"] == "ACTIVE"


async def test_harness_configuration_changes_catalog_and_disable_revokes_loaded_tools(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = native_tools(manager, tmp_path)
    await manager.enable_async(plugin_id, workspace)
    tools = manager.create_tools(ToolContext(workspace, shared_state={"agent_session": Session()}))
    configured = await manager.configure_async(plugin_id, workspace, {"components": {"ask": {"enabled": False}}})
    assert configured["tools"] == ["todo_write"]
    manager.disable(plugin_id, workspace)
    result = await tools[0].execute()
    assert result.is_error and "not enabled" in result.error
    assert manager.create_tools(ToolContext(workspace)) == []


async def test_failed_activation_does_not_publish_grants_or_catalog(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = install(manager, tmp_path / "source", {
        "plugin.mjs": b"export const inject=['unavailableService']; export function apply() {}",
    }, [{"id": "missing", "name": "./plugin.mjs"}])
    with pytest.raises(CordisError, match="unavailable|initialize|activation failed"):
        await manager.enable_async(plugin_id, workspace, allow_model=True)
    details = manager.details(plugin_id, workspace)
    assert details["enabled"] is False and details["tools"] == []


async def test_concurrent_disable_cancels_pending_activation_even_without_prior_grants(tmp_path, workspace, monkeypatch):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = install(manager, tmp_path / "source", {"service.mjs": b"export function apply() {}"},
                        [{"id": "service", "name": "./service.mjs"}])
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def activation(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return {"tool_definitions": [], "runtime_components": []}
    monkeypatch.setattr(manager, "_invoke", activation)
    task = asyncio.create_task(manager.enable_async(plugin_id, workspace))
    await entered.wait()
    manager.disable(plugin_id, workspace)
    proceed.set()
    with pytest.raises(CordisError, match="changed during activation"):
        await task
    assert manager.details(plugin_id, workspace)["enabled"] is False


async def test_failed_configuration_exposes_real_fields_without_granting_access(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = install(manager, tmp_path / "source", {
        "todo.mjs": (RUNTIME / "compat/tests/fixtures/harness-todo.mjs").read_bytes(),
    }, [{"id": "todo", "name": "./todo.mjs"}])
    with pytest.raises(CordisError, match="activation failed"):
        await manager.enable_async(plugin_id, workspace)
    details = manager.details(plugin_id, workspace)
    assert details["enabled"] is False
    schema = details["config_schema"]["properties"]["components"]["properties"]["todo"]["properties"]["config"]
    assert schema["properties"]["allowParallelInProgress"]["type"] == "boolean"
    await manager.configure_async(plugin_id, workspace, {"components": {"todo": {"config": {"allowParallelInProgress": False}}}})
    assert (await manager.enable_async(plugin_id, workspace))["tools"] == ["todo_write"]


async def test_discovered_secret_fields_are_concealed_and_preserved(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = install(manager, tmp_path / "source", {
        "plugin.mjs": b"import z from '@deepseek-ai/schemastery'; export const Config=z.object({apiKey:z.string().required()}); export function apply() {}",
    }, [{"id": "secret", "name": "./plugin.mjs"}])
    with pytest.raises(CordisError, match="activation failed"):
        await manager.enable_async(plugin_id, workspace)
    await manager.configure_async(plugin_id, workspace, {"components": {"secret": {"config": {"apiKey": "private-value"}}}})
    await manager.enable_async(plugin_id, workspace)
    details = manager.details(plugin_id, workspace)
    assert "private-value" not in repr(details)
    assert details["configured_secrets"]
    await manager.configure_async(plugin_id, workspace, {"components": {"secret": {"config": {"apiKey": ""}}}})
    assert "private-value" not in repr(manager.details(plugin_id, workspace))


async def test_nested_component_configuration_uses_scoped_fields_and_conceals_secrets(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = install(manager, tmp_path / "source", {
        "plugin.mjs": b"import z from '@deepseek-ai/schemastery'; export const Config=z.object({apiKey:z.string().required()}); export function apply() {}",
    }, [{"id": "group", "group": True, "config": [{"id": "secret", "name": "./plugin.mjs"}]}])
    with pytest.raises(CordisError):
        await manager.enable_async(plugin_id, workspace)
    await manager.configure_async(plugin_id, workspace, {"components": {"group/secret": {"config": {"apiKey": "nested-private-value"}}}})
    await manager.enable_async(plugin_id, workspace)
    assert "nested-private-value" not in repr(manager.details(plugin_id, workspace))


async def test_disabling_one_component_preserves_its_secret_annotations(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    module = b"import z from '@deepseek-ai/schemastery'; export const Config=z.object({apiKey:z.string().required()}); export function apply() {}"
    plugin_id = install(manager, tmp_path / "source", {"first.mjs": module, "second.mjs": module}, [
        {"id": "first", "name": "./first.mjs", "config": {"apiKey": "first-private"}},
        {"id": "second", "name": "./second.mjs", "config": {"apiKey": "second-private"}},
    ])
    await manager.enable_async(plugin_id, workspace)
    await manager.configure_async(plugin_id, workspace, {"components": {"first": {"enabled": False}}})
    details = manager.details(plugin_id, workspace)
    assert "first-private" not in repr(details) and "second-private" not in repr(details)


async def test_changed_code_reinstallation_clears_grants_configuration_and_catalog(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = native_tools(manager, tmp_path)
    await manager.enable_async(plugin_id, workspace)
    await manager.configure_async(plugin_id, workspace, {"components": {"ask": {"enabled": False}}})
    (tmp_path / "source" / "todo.mjs").write_bytes((tmp_path / "source" / "todo.mjs").read_bytes() + b"\n// changed build\n")
    manager.install(tmp_path / "source")
    details = manager.details(plugin_id, workspace)
    assert not details["enabled"] and details["tools"] == []
    assert details["config"]["components"]["ask"]["enabled"] is True


async def test_waiting_for_user_answer_pauses_only_plugin_timeout(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry", tool_timeout=2)
    plugin_id = native_tools(manager, tmp_path)
    await manager.enable_async(plugin_id, workspace)
    async def answer(questions):
        await asyncio.sleep(2.2)
        return {"answers": [{"id": "q", "selected": [], "custom": "Continue"}]}
    context = ToolContext(workspace, shared_state={"agent_session": Session(), "user_question_handler": answer})
    tool = next(tool for tool in manager.create_tools(context) if tool.tool_name == "ask_user_question")
    result = await asyncio.wait_for(tool.execute(questions=[{"id": "q", "question": "Next?"}]), timeout=5)
    assert not result.is_error, result.error


async def test_task_cancellation_still_interrupts_waiting_plugin_question(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry")
    plugin_id = native_tools(manager, tmp_path)
    await manager.enable_async(plugin_id, workspace)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def answer(questions):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    context = ToolContext(workspace, shared_state={"agent_session": Session(), "user_question_handler": answer})
    tool = next(tool for tool in manager.create_tools(context) if tool.tool_name == "ask_user_question")
    task = asyncio.create_task(tool.execute(questions=[{"id": "q", "question": "Next?"}]))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_host_session_scope_cannot_read_transcripts_or_impersonate_other_sessions(workspace):
    session = Session()
    session.add_user_message("Private conversation must never be serialized to plugin.")
    context = ToolContext(workspace, shared_state={"agent_session": session})
    before = dict(session.checkpoint)
    host = CordisHost("example", lambda: {}, context=context)
    assert "Private conversation" not in repr(host.execution)
    for method, params in [("session.read", {}), ("tools.execute", {"name": "exec"}),
                           ("session.append", {"session_id": "someone-else", "type": "todo/write", "data": {"todos": []}}),
                           ("session.append", {"session_id": host.execution["session_id"], "type": "turn/start", "data": {}})]:
        with pytest.raises(PermissionError):
            await host.dispatch(method, params)
    assert session.checkpoint == before


async def test_host_model_calls_remain_denied_without_explicit_model_grant(workspace):
    host = CordisHost("example", lambda: {}, context=ToolContext(workspace))
    assert "llm" not in (await host.initialize())["host_services"]
    with pytest.raises(PermissionError):
        await host.dispatch("llm.listModels", {"provider": "deepseek"})


async def test_failed_checkpoint_write_does_not_publish_todo_projection(workspace):
    session = Session()
    session.plan_steps = [{"text": "Existing plan", "status": "pending"}]
    async def fail_checkpoint(current):
        raise OSError("disk unavailable")
    host = CordisHost("example", lambda: {}, context=ToolContext(workspace, shared_state={
        "agent_session": session, "checkpoint_callback": fail_checkpoint,
    }))
    with pytest.raises(OSError):
        await host.dispatch("session.append", {"session_id": host.execution["session_id"], "type": "todo/write",
                                                "data": {"todos": [{"content": "Not committed", "status": "completed"}]}})
    assert session.plan_steps == [{"text": "Existing plan", "status": "pending"}]
    assert "cordis_events" not in session.checkpoint and "outstanding" not in session.checkpoint


async def test_invalid_harness_metadata_cannot_escape_snapshot(tmp_path):
    files = adapt_harness_package({"package.json": b'{"name":"simple","version":"1.0.0","main":"index.js"}', "index.js": b"export function apply(){}"})
    manifest = json.loads(files[MANIFEST_NAME])
    manifest["harness"]["packages"][0]["path"] = "../private"
    files[MANIFEST_NAME] = json.dumps(manifest).encode()
    source = tmp_path / "source"
    source.mkdir()
    for name, content in files.items():
        (source / name).write_bytes(content)
    with pytest.raises(CordisError, match="remain inside"):
        CordisManager(tmp_path / "registry").install(source)


async def test_reviewed_native_provider_adapts_to_host_service_without_node_execution(tmp_path, workspace, monkeypatch):
    fixture = Path(__file__).parent / "fixtures/native-provider-0.1.1"
    files = _package_files(fixture)
    assert json.loads(files[MANIFEST_NAME])["harness"]["adapter"] == "native-provider"
    source = tmp_path / "source"
    source.mkdir()
    for name, content in files.items():
        target = source / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(content)
    # Native sockets have a 100-byte portable path ceiling; use a short private
    # registry for this test, independently of pytest's long workspace names.
    with tempfile.TemporaryDirectory(prefix="lc-", dir="/tmp") as directory:
        manager = CordisManager(Path(directory) / "r", node_executable="must-not-run-node")
        plugin_id = manager.install(source)["id"]
        with pytest.raises(CordisError, match="model access grant"):
            await manager.enable_async(plugin_id, workspace)
        assert manager.details(plugin_id, workspace)["enabled"] is False
        active = await manager.enable_async(plugin_id, workspace, allow_model=True)
        assert active["state"] == "HOST_SERVICE" and active["tools"] == []
        spec = manager.host_service_spec(plugin_id, workspace)
        assert spec["adapter_version"] == 1 and spec["enabled"] is True
        assert spec["config"]["socketPath"].startswith(spec["state_dir"] + "/")
        assert not Path(spec["config"]["socketPath"]).exists()
        assert (await manager.inspect(plugin_id, workspace))["state"] == "HOST_SERVICE"
        manager.disable(plugin_id, workspace)
        with pytest.raises(CordisError, match="model access grant"):
            manager.host_service_spec(plugin_id, workspace)


async def test_bundled_bridge_can_be_reviewed_and_installed_without_network_or_autogrants(workspace, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("The included bridge must not contact npm")
    from libre_claw.core import cordis_packages
    monkeypatch.setattr(cordis_packages.httpx, "AsyncClient", no_network)
    assert next(item for item in catalog() if item["source"] == "builtin:native-provider")["requires_model_access"] is True
    with tempfile.TemporaryDirectory(prefix="lc-", dir="/tmp") as directory:
        manager = CordisManager(Path(directory) / "r", node_executable="must-not-run-node")
        previews = CordisPackagePreviews(manager)
        try:
            preview = await previews.preview("builtin:native-provider", workspace)
            assert preview["name"] == "Libre WebUI bridge" and preview["source_kind"] == "builtin"
            installed = previews.install(preview["token"], workspace)
            assert installed["enabled"] is False and installed["tool_count"] == 0
            with pytest.raises(CordisError, match="model access grant"):
                await manager.enable_async(installed["id"], workspace)
            assert (await manager.enable_async(installed["id"], workspace, allow_model=True))["state"] == "HOST_SERVICE"
        finally:
            previews.close()


def test_bundled_native_bridge_keeps_the_reviewed_source_bytes():
    fixture = Path(__file__).parent / "fixtures/native-provider-0.1.1"
    included = RUNTIME / "examples/native-provider"
    for relative in ("package.json", "cordis.patch.yml", "LICENSE", "runtime/native-provider-plugin.js", "runtime/native-provider-protocol.js"):
        assert (included / relative).read_bytes() == (fixture / relative).read_bytes()


@pytest.mark.parametrize("change", ["code", "version", "component"])
def test_native_host_adapter_rejects_unreviewed_implementations(tmp_path, change):
    fixture = Path(__file__).parent / "fixtures/native-provider-0.1.1"
    source = tmp_path / "source"
    shutil.copytree(fixture, source)
    if change == "code":
        with (source / "runtime/native-provider-plugin.js").open("ab") as handle:
            handle.write(b"\n// changed implementation\n")
    elif change == "version":
        package = json.loads((source / "package.json").read_text())
        package["version"] = "0.2.0"
        (source / "package.json").write_text(json.dumps(package))
    else:
        (source / "other.mjs").write_text("export function apply() {}")
        (source / "cordis.patch.yml").write_text("- insert: [{id: other, name: ./other.mjs}]\n")
    with pytest.raises(CordisError, match="not the reviewed"):
        _package_files(source)


def test_inline_images_are_verified_without_reading_paths_or_fetching_urls():
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    data = base64.b64encode(output.getvalue()).decode()
    attachments = tool_attachments({"content_blocks": [{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": data,
    }}]})
    assert len(attachments) == 1 and attachments[0].data == data and attachments[0].path == ""
    for block in [
        {"type": "image", "source": {"type": "url", "url": "https://private.invalid"}},
        {"type": "image", "data": "bad", "mediaType": "image/png"},
        {"type": "image", "data": data, "mediaType": "image/jpeg"},
        {"type": "image", "attachment": {"attachmentId": "/private/file"}},
    ]:
        with pytest.raises((ValueError, PermissionError)):
            tool_attachments({"content_blocks": [block]})


async def test_application_owned_worker_retains_plugin_state_and_closes_after_disable(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        plugin_id = install(manager, tmp_path / "source", {
            "counter.mjs": b"""import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools'];
export function apply(ctx, config) {
 let count=config.initial ?? 0;
 ctx.tools.register(defineTool({name:'count',description:'Count calls',parameters:{},
 output:{schema:{type:'integer'},render:(_,value)=>[{type:'text',text:String(value)}]},
 async execute(){return ++count;}}));
}""",
        }, [{"id": "counter", "name": "./counter.mjs", "config": {"initial": 0}}])
        active = await manager.enable_async(plugin_id, workspace)
        assert active["state"] == "ACTIVE" and active["runtime_lifetime"] == "persistent"
        tool = manager.create_tools(ToolContext(workspace))[0]
        assert (await tool.execute()).content == "1"
        assert (await tool.execute()).content == "2"
        first_pid = (await manager.inspect(plugin_id, workspace))["process_id"]
        assert first_pid == (await manager.inspect(plugin_id, workspace))["process_id"]
        updated = await manager.configure_async(plugin_id, workspace, {"components": {"counter": {"config": {"initial": 10}}}})
        assert updated["process_id"] != first_pid
        assert (await manager.create_tools(ToolContext(workspace))[0].execute()).content == "11"
        worker = next(iter(manager._workers.values()))["worker"]
        manager.disable(plugin_id, workspace)
        await manager.reconcile_workers()
        assert not worker.running
        assert manager.details(plugin_id, workspace)["state"] == "DISABLED"
    finally:
        await manager.aclose()


async def test_app_startup_restores_services_with_no_tools_under_existing_grants(tmp_path, workspace, runtime_supported):
    registry = CordisManager(tmp_path / "registry")
    plugin_id = install(registry, tmp_path / "source", {"service.mjs": b"export function apply(ctx) { ctx.provide('customService', {ready:true}); }"},
                        [{"id": "service", "name": "./service.mjs"}])
    await registry.enable_async(plugin_id, workspace)
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        assert not manager._workers
        manager.list_plugins(workspace)
        assert not manager._workers
        await manager.reconcile_workers(workspace)
        assert manager.details(plugin_id, workspace)["state"] == "ACTIVE"
        worker = next(iter(manager._workers.values()))["worker"]
        await manager.aclose()
        assert not worker.running
    finally:
        await manager.aclose()


async def test_failed_start_is_not_retried_until_settings_revision(tmp_path, workspace, runtime_supported, monkeypatch):
    registry = CordisManager(tmp_path / "registry")
    plugin_id = native_tools(registry, tmp_path)
    await registry.enable_async(plugin_id, workspace)
    manager = CordisManager(tmp_path / "registry", persistent=True)
    attempts = []
    async def fail(plugin_id, directory):
        attempts.append(plugin_id)
        raise CordisError("simulated startup failure")
    monkeypatch.setattr(manager, "inspect", fail)
    await manager.reconcile_workers(workspace)
    await manager.reconcile_workers(workspace)
    assert attempts == [plugin_id]
    await registry.configure_async(plugin_id, workspace, {"components": {"ask": {"enabled": False}}})
    await manager.reconcile_workers(workspace)
    assert attempts == [plugin_id, plugin_id]
    await manager.aclose()


async def test_persistent_worker_questions_use_current_task_and_pause_timeout(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry", persistent=True, tool_timeout=2)
    try:
        plugin_id = native_tools(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        async def answer(questions):
            await asyncio.sleep(2.2)
            return {"answers": [{"id": "q", "selected": [], "custom": "Current caller"}]}
        context = ToolContext(workspace, shared_state={"agent_session": Session(), "user_question_handler": answer})
        tool = next(tool for tool in manager.create_tools(context) if tool.tool_name == "ask_user_question")
        result = await asyncio.wait_for(tool.execute(questions=[{"id": "q", "question": "Proceed?"}]), timeout=5)
        assert not result.is_error and "Current caller" in result.content
        # An inspection has no task callback, even though the process is reused.
        assert (await manager.inspect(plugin_id, workspace))["state"] == "ACTIVE"
    finally:
        await manager.aclose()


@pytest.mark.parametrize("operation", ["configure", "disable"])
async def test_reconfiguration_or_disable_releases_busy_worker_without_cancelling_task(tmp_path, workspace, runtime_supported, operation):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    entered, released = asyncio.Event(), asyncio.Event()
    task = None
    try:
        plugin_id = native_tools(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        async def answer(questions):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        context = ToolContext(workspace, shared_state={"agent_session": Session(), "user_question_handler": answer})
        tool = next(tool for tool in manager.create_tools(context) if tool.tool_name == "ask_user_question")
        task = asyncio.create_task(tool.execute(questions=[{"id": "q", "question": "Waiting"}]))
        await asyncio.wait_for(entered.wait(), 5)
        if operation == "configure":
            await asyncio.wait_for(manager.configure_async(plugin_id, workspace, {"components": {"ask": {"enabled": False}}}), 5)
        else:
            manager.disable(plugin_id, workspace)
            await asyncio.wait_for(manager.reconcile_workers(), 5)
        result = await asyncio.wait_for(task, 2)
        assert result.is_error and not task.cancelled()
        assert released.is_set()
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.aclose()


async def test_plugin_model_operations_traverse_core_provider_service(workspace):
    calls = []
    class Engine:
        async def call(self, service, method, *, handler):
            calls.append((service, method))
            return await handler()
        async def stream(self, service, method, *, handler):
            calls.append((service, method))
            async for chunk in handler():
                yield chunk
    class Models:
        async def list_providers(self):
            return [{"id": "configured"}]
        async def list_configurable_providers(self):
            return []
        async def list_models(self, provider):
            return [{"id": "model"}]
        async def stream(self, options):
            yield {"type": "finish", "reason": {"kind": "stop"}}
    host = CordisHost("example", lambda: {"allow_model": True}, engine=Engine())
    host.llm = Models()
    await host.initialize()
    assert await host.dispatch("llm.listModels", {"provider": "configured"}) == [{"id": "model"}]
    assert [chunk async for chunk in host.stream("llm.stream", {})] == [{"type": "finish", "reason": {"kind": "stop"}}]
    assert calls == [("providers", "models")] * 3 + [("providers", "stream")]
    host.engine = lambda: None
    with pytest.raises(PermissionError, match="engine is unavailable"):
        await host.dispatch("llm.listModels", {"provider": "configured"})


async def test_worker_capacity_is_bounded_and_freed_capacity_retries_pending_service(tmp_path, workspace, runtime_supported, monkeypatch):
    from libre_claw.core import cordis
    monkeypatch.setattr(cordis, "MAX_PERSISTENT_WORKERS", 1)
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        ids = []
        for number in range(2):
            ids.append(install(manager, tmp_path / f"source-{number}", {"service.mjs": b"export function apply() {}"},
                               [{"id": "service", "name": "./service.mjs"}], package_name=f"fixture-{number}"))
        await manager.enable_async(ids[0], workspace)
        with pytest.raises(CordisError, match="worker limit"):
            await manager.enable_async(ids[1], workspace)
        assert len(manager._workers) == 1
        manager.disable(ids[0], workspace)
        await manager.reconcile_workers(workspace)
        assert len(manager._workers) == 1
        assert manager.details(ids[1], workspace)["state"] == "ACTIVE"
    finally:
        await manager.aclose()


async def test_global_plugin_disable_stops_warm_workers_and_prevents_restart(tmp_path, workspace, runtime_supported, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(workspace)
    config = load_config()
    manager = CordisManager(tmp_path / "registry", persistent=True, config=config)
    try:
        plugin_id = native_tools(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        worker = next(iter(manager._workers.values()))["worker"]
        manager.config = replace(config, cordis=replace(config.cordis, enabled=False))
        with pytest.raises(CordisError, match="disabled in configuration"):
            await manager.inspect(plugin_id, workspace)
        await manager.reconcile_workers(workspace)
        assert not worker.running and not manager._workers
        await manager.reconcile_workers(workspace)
        assert not manager._workers
        manager.config = config
        await manager.reconcile_workers(workspace)
        assert manager.details(plugin_id, workspace)["state"] == "ACTIVE"
    finally:
        await manager.aclose()


def disposer_plugin(manager, tmp_path):
    return install(manager, tmp_path / "source", {"disposer.mjs": b"""
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
export function apply(ctx) { ctx.effect(() => async () => {
 await new Promise(resolve=>setTimeout(resolve,30));
 await mkdir(process.env.HOME,{recursive:true});
 await writeFile(join(process.env.HOME,'dispose.json'),'"private plugin state"');
}); }
"""}, [{"id": "disposer", "name": "./disposer.mjs"}])


@pytest.mark.parametrize("outside_process", [False, True])
async def test_removal_cleans_state_recreated_by_real_async_disposer(tmp_path, workspace, runtime_supported, monkeypatch, outside_process):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        plugin_id = disposer_plugin(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        state = manager.root / "state" / _workspace(workspace)[1] / plugin_id
        observed = []
        cleanup = manager._cleanup_removed
        def observe(identifier):
            observed.append((state / "dispose.json").exists())
            return cleanup(identifier)
        monkeypatch.setattr(manager, "_cleanup_removed", observe)
        if outside_process:
            CordisManager(tmp_path / "registry").remove(plugin_id)
            await manager.reconcile_workers()
        else:
            await manager.remove_async(plugin_id)
        assert observed == [True]
        assert not state.exists() and not manager._workers
    finally:
        await manager.aclose()


async def test_removal_preserves_a_concurrent_reinstallation(tmp_path, workspace, runtime_supported, monkeypatch):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        plugin_id = disposer_plugin(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        worker = next(iter(manager._workers.values()))["worker"]
        close = worker.aclose
        async def reinstall_during_close():
            manager.install(tmp_path / "source")
            await close()
        monkeypatch.setattr(worker, "aclose", reinstall_during_close)
        await manager.remove_async(plugin_id)
        assert manager.details(plugin_id, workspace)["integrity"] == "valid"
        state = manager.root / "state" / _workspace(workspace)[1] / plugin_id
        assert (state / "dispose.json").exists()
    finally:
        await manager.aclose()


async def test_removal_finishes_cleanup_when_request_is_cancelled(tmp_path, workspace, runtime_supported, monkeypatch):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    try:
        plugin_id = disposer_plugin(manager, tmp_path)
        await manager.enable_async(plugin_id, workspace)
        worker = next(iter(manager._workers.values()))["worker"]
        close = worker.aclose
        entered, finish = asyncio.Event(), asyncio.Event()
        async def delayed_close():
            entered.set()
            await finish.wait()
            await close()
        monkeypatch.setattr(worker, "aclose", delayed_close)
        task = asyncio.create_task(manager.remove_async(plugin_id))
        await entered.wait()
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not worker.running and not manager._workers
        assert not (manager.root / "state" / _workspace(workspace)[1] / plugin_id).exists()
    finally:
        await manager.aclose()
