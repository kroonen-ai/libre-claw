# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from libre_claw.core.cordis import CordisError, CordisManager, MANIFEST_NAME, MAX_PACKAGE_BYTES, MAX_RPC_LINE
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.tools import ToolContext


DEFINITION = {"name": "echo", "description": "Echo text", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}


def make_plugin(path: Path, *, source: str | None = None, **updates) -> Path:
    path.mkdir()
    manifest = {"id": "example", "name": "Example", "version": "1.0.0", "entry": "plugin.mjs", "tools": [DEFINITION], "config": {"greeting": "configured"}, **updates}
    (path / MANIFEST_NAME).write_text(json.dumps(manifest))
    if source is None:
        source = f"export default {{inject:['libre'],apply(ctx,config){{ctx.libre.registerTool({json.dumps(DEFINITION)},args=>config.greeting+':'+args.text);}}}};"
    (path / "plugin.mjs").write_text(source)
    return path


@pytest.fixture
def manager(tmp_path):
    return CordisManager(root=tmp_path / "registry")


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def runtime_supported(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    plugin = tmp_path / "probe-plugin"
    plugin.mkdir()
    runtime = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/runtime.mjs"
    try:
        prepare_cordis_process(node, runtime, plugin, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(f"An enforced offline Node runtime is unavailable: {exc}")
    return node


def test_install_is_nonexecuting_immutable_snapshot_without_private_files(manager, tmp_path, workspace):
    source = make_plugin(tmp_path / "source", source="throw new Error('must not execute on install');")
    (source / ".env").write_text("PRIVATE_SECRET=hidden")
    (source / "private.key").write_text("private key")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("remote credentials")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "dependency.js").write_text("untrusted dependency")

    installed = manager.install(source)
    record = manager._record("example")
    snapshot = manager._snapshot("example", record)

    assert installed["integrity"] == "valid"
    assert not installed["enabled"]
    assert sorted(path.name for path in snapshot.iterdir()) == [MANIFEST_NAME, "plugin.mjs"]
    assert snapshot.name == record["digest"]
    assert (snapshot / "plugin.mjs").stat().st_mode & 0o222 == 0
    (source / "plugin.mjs").write_text("changed source")
    assert manager.list_plugins(workspace)[0]["integrity"] == "valid"
    assert manager.create_tools(ToolContext(working_directory=workspace)) == []
    assert "config" not in installed and "entry" not in installed


def test_workspace_grants_are_explicit_and_do_not_cross_projects(manager, tmp_path, workspace):
    manager.install(make_plugin(tmp_path / "source"))
    other = tmp_path / "other"
    other.mkdir()
    enabled = manager.enable("example", workspace)
    assert enabled["grants"] == {"allow_network": False, "read_paths": [], "write_paths": []}
    assert manager.list_plugins(other)[0]["enabled"] is False
    context = ToolContext(working_directory=workspace, shared_state={"private": "must not be passed"})
    tools = manager.create_tools(context)
    assert len(tools) == 1
    assert tools[0].name == "cordis__example__echo"
    assert tools[0].permission_level == "ask"
    assert tools[0].is_read_only({}) is False
    assert tools[0].schema()["input_schema"] == DEFINITION["input_schema"]
    assert manager.create_tools(ToolContext(working_directory=other)) == []
    linked = tmp_path / "alias"
    linked.symlink_to(workspace, target_is_directory=True)
    assert manager.list_plugins(linked)[0]["enabled"] is True


async def test_disabling_revokes_already_registered_tools_without_starting_node(manager, tmp_path, workspace):
    manager.install(make_plugin(tmp_path / "source"))
    manager.enable("example", workspace)
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    manager.disable("example", workspace)
    result = await tool.execute(text="no")
    assert result.is_error and "not enabled" in result.error
    with pytest.raises(CordisError, match="not enabled"):
        await manager.inspect("example", workspace)


def test_updated_code_revokes_every_workspace_grant(manager, tmp_path, workspace):
    source = make_plugin(tmp_path / "source")
    manager.install(source)
    manager.enable("example", workspace, allow_network=True, read_paths=(workspace,), write_paths=(workspace / "output",))
    same = manager.install(source)
    assert manager.list_plugins(workspace)[0]["enabled"]
    (source / "plugin.mjs").write_text("export default {apply(){}}")
    changed = manager.install(source)
    assert changed["integrity"] == same["integrity"] == "valid"
    assert manager.list_plugins(workspace)[0]["enabled"] is False
    assert manager._record("example")["workspaces"] == {}
    assert len(list((manager.root / "plugins" / "example").iterdir())) == 2


async def test_reenabled_update_cannot_replace_existing_task_tool_code(manager, tmp_path, workspace):
    source = make_plugin(tmp_path / "source")
    manager.install(source)
    manager.enable("example", workspace)
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    (source / "plugin.mjs").write_text("export default {apply(){}}")
    manager.install(source)
    manager.enable("example", workspace)

    result = await tool.execute(text="old task")

    assert result.is_error and "Start a new task" in result.error
    assert manager.create_tools(ToolContext(working_directory=workspace))[0]._digest != tool._digest


async def test_snapshot_tampering_fails_closed_at_tool_execution(manager, tmp_path, workspace):
    manager.install(make_plugin(tmp_path / "source"))
    manager.enable("example", workspace)
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    snapshot = manager._snapshot("example", manager._record("example"))
    (snapshot / "extra.mjs").write_text("unreviewed code")
    status = manager.list_plugins(workspace)[0]
    assert status["integrity"] == "changed"
    assert manager.create_tools(ToolContext(working_directory=workspace)) == []
    assert "changed after installation" in (await tool.execute(text="no")).error
    with pytest.raises(CordisError, match="changed after installation"):
        manager.enable("example", workspace)


def test_remove_purges_only_this_plugin_snapshots_and_private_state(manager, tmp_path, workspace):
    manager.install(make_plugin(tmp_path / "source"))
    manager.enable("example", workspace)
    key = hashlib.sha256(os.fsencode(workspace.resolve())).hexdigest()
    state = manager.root / "state" / key
    (state / "example").mkdir(parents=True)
    (state / "example" / "private.json").write_text("private")
    (state / "unrelated").mkdir()
    (state / "unrelated" / "preserve").write_text("preserve")
    removed = manager.remove("example")
    assert removed == {"id": "example", "removed": True}
    assert manager.list_plugins(workspace) == []
    assert not (manager.root / "plugins" / "example").exists()
    assert not (state / "example").exists()
    assert (state / "unrelated" / "preserve").read_text() == "preserve"


@pytest.mark.parametrize("updates", [
    {"permissions": {"network": True}}, {"allow_network": True}, {"grants": {}},
    {"id": "../outside"}, {"id": "ambiguous__namespace"}, {"entry": "../outside.mjs"}, {"entry": "/absolute.mjs"},
    {"tools": [{**DEFINITION, "description": "x" * 4001}]},
    {"tools": [{**DEFINITION, "input_schema": {"type": "object", "description": "x" * 32768}}]},
    {"config": {"huge": "x" * 65536}},
])
def test_rejects_manifest_self_grants_traversal_and_unbounded_context(manager, tmp_path, updates):
    with pytest.raises(CordisError):
        manager.install(make_plugin(tmp_path / "source", **updates))


def test_rejects_symlinks_and_oversize_packages(manager, tmp_path):
    source = make_plugin(tmp_path / "source")
    secret = tmp_path / "private"
    secret.write_text("private")
    (source / "linked.mjs").symlink_to(secret)
    with pytest.raises(CordisError, match="symlink"):
        manager.install(source)
    (source / "linked.mjs").unlink()
    (source / "large.bin").write_bytes(b"x" * MAX_PACKAGE_BYTES)
    with pytest.raises(CordisError, match="size"):
        manager.install(source)


def test_corrupt_registry_does_not_break_builtin_tool_startup(manager, workspace):
    manager.root.mkdir()
    (manager.root / "registry.json").write_text("{broken")
    assert manager.create_tools(ToolContext(working_directory=workspace)) == []
    with pytest.raises(CordisError):
        manager.list_plugins(workspace)


async def test_actual_cordis_runtime_tool_inspection_and_private_state(manager, tmp_path, workspace, runtime_supported, monkeypatch):
    manager.node_executable = runtime_supported
    monkeypatch.setenv("LIBRE_PRIVATE_TEST_SECRET", "must-not-reach-plugin")
    source = f"""export default {{inject:['libre'],apply(ctx,config){{
      ctx.libre.registerTool({json.dumps(DEFINITION)},async args=>{{
        const previous=await ctx.libre.storage.get('count')||0;
        await ctx.libre.storage.set('count',previous+1);
        return JSON.stringify({{text:config.greeting+':'+args.text,count:previous+1,secret:process.env.LIBRE_PRIVATE_TEST_SECRET||null}});
      }});
      ctx.effect(()=>async()=>{{await ctx.libre.storage.set('disposed',true);}});
    }}}};"""
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)
    status = await manager.inspect("example", workspace)
    assert status["state"] == "ACTIVE" and status["runtime_version"] == "4.0.2"
    assert "denied" in status["isolation"]
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    first = await tool.execute(text="one")
    second = await tool.execute(text="two")
    assert not first.is_error and not second.is_error
    assert json.loads(first.content) == {"text": "configured:one", "count": 1, "secret": None}
    assert json.loads(second.content)["count"] == 2
    key = hashlib.sha256(os.fsencode(workspace.resolve())).hexdigest()
    assert json.loads((manager.root / "state" / key / "example" / "disposed.json").read_text()) is True
    other = tmp_path / "other"
    other.mkdir()
    manager.enable("example", other)
    other_tool = manager.create_tools(ToolContext(working_directory=other))[0]
    assert json.loads((await other_tool.execute(text="separate")).content)["count"] == 1


async def test_runtime_catalog_mismatch_blocks_handler_execution(manager, tmp_path, workspace, runtime_supported):
    manager.node_executable = runtime_supported
    changed = {**DEFINITION, "description": "Unreviewed runtime schema"}
    source = f"export default {{inject:['libre'],apply(ctx){{ctx.libre.registerTool({json.dumps(changed)},async()=>{{await ctx.libre.storage.set('called',true);return 'no';}});}}}};"
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)
    result = await manager.create_tools(ToolContext(working_directory=workspace))[0].execute(text="no")
    assert result.is_error and "differ" in result.error
    assert list((manager.root / "state").rglob("called.json")) == []


async def test_runtime_output_is_bounded_without_returning_partial_sensitive_content(manager, tmp_path, workspace, runtime_supported):
    manager.node_executable = runtime_supported
    source = f"export default {{inject:['libre'],apply(ctx){{ctx.libre.registerTool({json.dumps(DEFINITION)},()=>{{process.stdout.write('private-output'+'x'.repeat({MAX_RPC_LINE}));return 'no';}});}}}};"
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)
    result = await manager.create_tools(ToolContext(working_directory=workspace))[0].execute(text="too large")
    assert result.is_error and "output limit" in result.error
    assert "private-output" not in result.error


async def test_success_waits_for_async_cordis_disposers(manager, tmp_path, workspace, runtime_supported):
    manager.node_executable = runtime_supported
    source = f"""export default {{inject:['libre'],apply(ctx){{
      ctx.libre.registerTool({json.dumps(DEFINITION)},()=> 'done');
      ctx.effect(()=>async()=>{{
        await new Promise(resolve=>setTimeout(resolve,800));
        await ctx.libre.storage.set('cleaned',true);
      }});
    }}}};"""
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)

    result = await manager.create_tools(ToolContext(working_directory=workspace))[0].execute(text="work")

    assert not result.is_error
    files = list((manager.root / "state").rglob("cleaned.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) is True


async def test_incomplete_disposal_is_reported_and_runtime_reaped(manager, tmp_path, workspace, runtime_supported, monkeypatch):
    manager.node_executable = runtime_supported
    source = f"""export default {{inject:['libre'],apply(ctx){{
      ctx.libre.registerTool({json.dumps(DEFINITION)},()=> 'done');
      ctx.effect(()=>async()=>new Promise(()=>{{}}));
    }}}};"""
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)
    monkeypatch.setattr("libre_claw.core.cordis.CLEANUP_GRACE_SECONDS", 0.05)
    processes = []
    real_spawn = asyncio.create_subprocess_exec

    async def tracked_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)

    result = await manager.create_tools(ToolContext(working_directory=workspace))[0].execute(text="work")

    assert result.is_error and "cleanup did not finish" in result.error
    assert processes and all(process.returncode is not None for process in processes)


async def test_noncanonical_parent_paths_match_runtime_permission_grants(tmp_path, workspace, runtime_supported):
    canonical_parent = tmp_path / "canonical"
    canonical_parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(canonical_parent, target_is_directory=True)
    manager = CordisManager(root=alias / "registry", node_executable=runtime_supported)
    manager.install(make_plugin(tmp_path / "source"))
    manager.enable("example", workspace)

    result = await manager.create_tools(ToolContext(working_directory=workspace))[0].execute(text="canonical")

    assert manager.root == canonical_parent.resolve() / "registry"
    assert not result.is_error and result.content == "configured:canonical"


async def test_large_valid_catalog_fits_runtime_transport(manager, tmp_path, workspace, runtime_supported):
    manager.node_executable = runtime_supported
    definitions = [{**DEFINITION, "name": f"echo{index}", "description": "x" * 4000} for index in range(64)]
    assert len(json.dumps({"tools": definitions}).encode()) > 256 * 1024
    source = f"export default {{inject:['libre'],apply(ctx){{for(const definition of {json.dumps(definitions)})ctx.libre.registerTool(definition,()=> 'works');}}}};"
    manager.install(make_plugin(tmp_path / "source", source=source, tools=definitions))
    manager.enable("example", workspace)

    status = await manager.inspect("example", workspace)

    assert status["state"] == "ACTIVE" and status["tool_count"] == 64


def test_aggregate_catalog_is_rejected_before_install_when_it_exceeds_rpc_budget(manager, tmp_path):
    definitions = [{**DEFINITION, "name": f"echo{index}", "input_schema": {"type": "object", "description": "x" * 20000}} for index in range(64)]
    assert len(json.dumps({"tools": definitions}).encode()) > MAX_RPC_LINE
    source = make_plugin(tmp_path / "source", source="export default {apply(){}};", tools=definitions)

    with pytest.raises(CordisError, match="catalog exceeds"):
        manager.install(source)
    assert manager._read_registry()["plugins"] == {}


async def test_actual_runtime_timeout_and_cancellation_reap_process(manager, tmp_path, workspace, runtime_supported, monkeypatch):
    manager.node_executable = runtime_supported
    source = f"export default {{inject:['libre'],apply(ctx){{ctx.libre.registerTool({json.dumps(DEFINITION)},async()=>new Promise(()=>{{}}));}}}};"
    manager.install(make_plugin(tmp_path / "source", source=source))
    manager.enable("example", workspace)
    processes = []
    real_spawn = asyncio.create_subprocess_exec

    async def tracked_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)
    manager.tool_timeout = 1.5
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    result = await tool.execute(text="wait")
    assert result.is_error and "timed out" in result.error
    assert processes and all(process.returncode is not None for process in processes)
    manager.tool_timeout = 30
    operation = asyncio.create_task(tool.execute(text="wait"))
    for _ in range(100):
        if len(processes) == 2:
            break
        await asyncio.sleep(0.02)
    assert len(processes) == 2
    await asyncio.sleep(0.1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert all(process.returncode is not None for process in processes)
