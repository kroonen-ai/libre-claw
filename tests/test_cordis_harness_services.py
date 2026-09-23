# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from libre_claw.core.cordis import CordisManager
from libre_claw.core.cordis_harness_services import HarnessHostServices
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolContext, ToolResult
from test_cordis_harness_integration import RUNTIME, install, runtime_supported, workspace


@pytest.fixture
async def host(workspace):
    grants = {"read_paths": [str(workspace)], "write_paths": [str(workspace)], "allow_network": False}
    context = ToolContext(workspace, shared_state={"agent_session": Session()})
    effects = []
    async def execute(tool, arguments):
        effects.append((tool.name, arguments))
        return await tool.invoke(arguments)
    context.shared_state["harness_tool_executor"] = execute
    service = HarnessHostServices("fixture", context, lambda: grants)
    yield service, grants, effects
    await service.aclose()


async def test_filesystem_versions_edits_and_explicit_grants(host, workspace, tmp_path):
    service, grants, effects = host
    target = await service.dispatch("fs.resolve", {"path": "a.txt"})
    created = await service.dispatch("fs.writeText", {"target": target, "content": "one\r\ntwo", "expected": {"kind": "createIfAbsent"}})
    assert created["operation"] == "create" and created["before"] is None
    assert await service.dispatch("fs.readText", {"target": target}) == "one\ntwo"
    edited = await service.dispatch("fs.editText", {"target": target,
        "edit": {"oldString": "two", "newString": "three", "replaceAll": False}, "expected": {"version": created["version"]}})
    assert edited["after"] == "one\nthree"
    with pytest.raises(ValueError, match="FS_STALE_VERSION"):
        await service.dispatch("fs.writeText", {"target": target, "content": "old", "expected": {"kind": "replaceIfVersion", "version": created["version"]}})
    with pytest.raises(PermissionError):
        await service.dispatch("fs.resolve", {"path": str(tmp_path / "private")})
    (workspace / "escape").symlink_to(tmp_path)
    with pytest.raises(PermissionError):
        await service.dispatch("fs.resolve", {"path": "escape/private"})
    grants["write_paths"] = []
    with pytest.raises(PermissionError):
        await service.dispatch("fs.writeText", {"target": target, "content": "denied"})
    assert [effect[0] for effect in effects] == ["write_file", "read_file", "edit_file", "write_file"]


async def test_file_mutation_denial_does_not_touch_disk(host, workspace):
    service, _, _ = host
    async def deny(tool, arguments):
        return ToolResult(error="User denied this action")
    service.context.shared_state["harness_tool_executor"] = deny
    target = await service.dispatch("fs.resolve", {"path": "never.txt"})
    with pytest.raises(PermissionError, match="User denied"):
        await service.dispatch("fs.writeText", {"target": target, "content": "never"})
    assert not (workspace / "never.txt").exists()


def shell_supported():
    if sys.platform != "darwin" and not (sys.platform == "linux" and shutil.which("bwrap")):
        pytest.skip("No OS-confined shell backend installed")


async def test_confined_shell_has_no_inherited_secret_or_ungranted_file_access(host, workspace, tmp_path, monkeypatch):
    shell_supported()
    service, _, effects = host
    monkeypatch.setenv("LIBRE_TEST_PRIVATE_KEY", "must-not-leak")
    secret = tmp_path / "private.txt"
    secret.write_text("must-not-read")
    command = 'printf "secret=%s\\n" "$LIBRE_TEST_PRIVATE_KEY"; /bin/cat ' + str(secret)
    result = await service.dispatch("shell.run", {"spec": {"command": command, "workdir": str(workspace), "timeoutMs": 2000}})
    assert result["exitCode"] != 0
    assert "secret=\n" in result["stdout"]["text"]
    assert "must-not-leak" not in json.dumps(result) and "must-not-read" not in json.dumps(result)
    assert effects[-1][0] == "bash"


async def test_background_shell_is_owned_and_stops_on_disposal(host, workspace):
    shell_supported()
    service, _, _ = host
    result = await service.dispatch("shell.start", {"spec": {"command": "/bin/sleep 30", "workdir": str(workspace), "timeoutMs": 30000}})
    record = service.processes[result["id"]]
    assert record["process"].returncode is None
    await service.aclose()
    assert record["process"].returncode is not None and record["task"].done()
    with pytest.raises(PermissionError, match="ended"):
        await service.dispatch("shell.read", {"id": result["id"]})


@pytest.mark.parametrize("ending", ["timeout", "revoke"])
async def test_shell_large_unread_stdin_still_obeys_timeout_and_revocation(host, workspace, ending):
    shell_supported()
    service, _, _ = host
    pending = asyncio.create_task(service.dispatch("shell.run", {"spec": {
        "command": "/bin/sleep 30", "workdir": str(workspace), "stdin": "x" * (256 * 1024),
        "timeoutMs": 200 if ending == "timeout" else 30000,
    }}))
    async with asyncio.timeout(3):
        while not service.processes:
            await asyncio.sleep(0.01)
        if ending == "revoke":
            service.context.shared_state["agent_session"] = Session()
            with pytest.raises(PermissionError, match="ended or changed"):
                await pending
        else:
            result = await pending
            assert result["timedOut"] is True
    assert all(record["process"].returncode is not None and record["task"].done()
               for record in service.processes.values())


async def test_unchanged_upstream_file_and_shell_plugins_use_real_host_effects(tmp_path, workspace, runtime_supported):
    shell_supported()
    manager = CordisManager(tmp_path / "registry", persistent=True)
    fixtures = RUNTIME / "compat/tests/fixtures"
    modules = {f"{name}.mjs": (fixtures / f"harness-{name}.mjs").read_bytes() for name in ("fs", "bash", "jobs")}
    identifier = install(manager, tmp_path / "package", modules, [
        {"id": "fs", "name": "./fs.mjs"}, {"id": "bash", "name": "./bash.mjs"},
        {"id": "jobs", "name": "./jobs.mjs", "config": {"completionDelivery": "quiet"}}])
    context = ToolContext(workspace, shared_state={"agent_session": Session()})
    async def execute(tool, arguments):
        return await tool.invoke(arguments)
    context.shared_state["harness_tool_executor"] = execute
    try:
        active = await manager.enable_async(identifier, workspace, read_paths=[str(workspace)], write_paths=[str(workspace)])
        assert {"read", "write", "edit", "bash", "job_list", "job_output", "job_kill"} <= set(active["tools"])
        tools = {tool.tool_name: tool for tool in manager.create_tools(context)}
        written = await tools["write"].execute(file_path="new.txt", content="from an unchanged upstream plugin")
        assert not written.is_error, written.error or written.content
        read = await tools["read"].execute(file_path="new.txt")
        assert not read.is_error and "unchanged upstream" in read.content, read.error or read.content
        foreground = await tools["bash"].execute(command="printf foreground", description="Print a foreground result")
        assert not foreground.is_error and "foreground" in foreground.content, foreground.error or foreground.content
        started = await tools["bash"].execute(command="printf background", description="Print a background result", run_in_background=True)
        assert not started.is_error, started.error or started.content
        jobs = await tools["job_list"].execute()
        assert not jobs.is_error and "bash-1" in jobs.content, jobs.error or jobs.content
        output = await tools["job_output"].execute(job_id="bash-1", wait=True, timeout_ms=2000)
        assert not output.is_error and "background" in output.content and "completed" in output.content, output.error or output.content
        prompts = await manager.prompt_contributions(context)
        assert "Use the read tool" in prompts[0]["text"]
        assert all(service.processes for service in context.shared_state["harness_host_services"].values())
    finally:
        await manager.aclose()


async def test_ptc_sdk_executes_typescript_and_concurrent_actual_bindings(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    source = b'''
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','ptcRuntime'];export function apply(ctx){
 ctx.tools.register(defineTool({name:'program',description:'Run a typed program',parameters:{code:{type:'string',required:true}},
 output:{schema:{type:'object',properties:{},additionalProperties:true},render:(_args,v)=>[{type:'text',text:JSON.stringify(v)}]},
 execute:async(args,exec)=>ctx.ptcRuntime.run(ctx.ptcRuntime.resolve({program:args.code,signal:exec.signal,
 bindings:[{global:'math',functions:{double:async args=>args.value*2},errorClass:{name:'BindingError',memberNameProperty:'member'}}]}))}));
}'''
    identifier = install(manager, tmp_path / "package", {"ptc.mjs": source}, [{"id": "ptc", "name": "./ptc.mjs"}])
    context = ToolContext(workspace, shared_state={"agent_session": Session()})
    reviewed = []
    async def execute(tool, arguments):
        reviewed.append(arguments)
        return await tool.invoke(arguments)
    context.shared_state["harness_tool_executor"] = execute
    try:
        await manager.enable_async(identifier, workspace)
        tool = manager.create_tools(context)[0]
        result = await tool.execute(code="const values: number[] = await Promise.all([math.double({value:2}),math.double({value:3})]); return values;")
        assert not result.is_error, result.error
        payload = json.loads(result.content)
        assert payload == {"logs": [], "value": [4, 6]}, payload
        assert "Promise.all" in reviewed[0]["command"]
    finally:
        await manager.aclose()


@pytest.mark.parametrize("persistent", [False, True])
async def test_ptc_presentation_calls_unchanged_file_tools_through_approvals(tmp_path, workspace, runtime_supported, persistent):
    manager = CordisManager(tmp_path / "registry", persistent=persistent)
    mode = b"export const inject=['tools','ptcRuntime'];export function apply(ctx){ctx.tools.presentAs('ptc');}"
    identifier = install(manager, tmp_path / "package", {
        "fs.mjs": (RUNTIME / "compat/tests/fixtures/harness-fs.mjs").read_bytes(), "mode.mjs": mode},
        [{"id": "fs", "name": "./fs.mjs"}, {"id": "mode", "name": "./mode.mjs"}])
    reviewed = []
    context = ToolContext(workspace, shared_state={"agent_session": Session()})
    async def execute(tool, arguments):
        reviewed.append(tool.name)
        return await tool.invoke(arguments)
    context.shared_state["harness_tool_executor"] = execute
    try:
        active = await manager.enable_async(identifier, workspace, read_paths=[str(workspace)], write_paths=[str(workspace)])
        assert active["tools"] == ["run_code"]
        code = "await tools.write({file_path:'ptc.txt',content:'written by PTC'}); return await tools.read({file_path:'ptc.txt'});"
        result = await manager.create_tools(context)[0].execute(code=code)
        assert not result.is_error and "written by PTC" in result.content, result.error or result.content
        assert (workspace / "ptc.txt").read_text() == "written by PTC"
        assert "bash" in reviewed and "write_file" in reviewed and "read_file" in reviewed
    finally:
        await manager.aclose()
        for service in context.shared_state.get("harness_host_services", {}).values():
            await service.aclose()


async def test_native_host_executor_enforces_plan_and_shared_tool_budget(workspace):
    from libre_claw.config import load_config
    from libre_claw.core.agent import Agent
    from libre_claw.core.permissions import PermissionManager
    from libre_claw.core.tools import ToolRegistry
    from test_agent import ScriptedProvider
    from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool
    from libre_claw.core.cordis_harness_services import _HostEffect

    context = ToolContext(workspace)
    actor = Agent(session=Session(mode="plan"), provider=ScriptedProvider([]), system_prompt="Test",
                  tool_registry=ToolRegistry([ReadFileTool(context), WriteFileTool(context)]),
                  permission_manager=PermissionManager(load_config(working_directory=workspace).permissions), max_tool_calls_per_turn=2)
    actor.accepting_control = True
    executed = []
    async def operation():
        executed.append(True)
        return "done"
    denied = await actor.execute_harness_tool(_HostEffect(context, "write_file", False, operation), {"path": "a.txt", "content": "no"})
    assert denied.is_error and "Plan mode" in denied.error and not executed
    read = _HostEffect(context, "read_file", True, operation)
    allowed = await actor.execute_harness_tool(read, {"path": "a.txt"})
    assert not allowed.is_error and executed == [True]
    denied = await actor.execute_harness_tool(read, {"path": "a.txt"})
    assert denied.is_error and "budget" in denied.error and executed == [True]


async def test_ptc_completion_cancels_and_joins_unawaited_question_binding(tmp_path, workspace, runtime_supported):
    manager = CordisManager(tmp_path / "registry", persistent=True)
    mode = b"export const inject=['tools','ptcRuntime'];export function apply(ctx){ctx.tools.presentAs('ptc');}"
    identifier = install(manager, tmp_path / "package", {
        "ask.mjs": (RUNTIME / "compat/tests/fixtures/harness-ask-user.mjs").read_bytes(), "mode.mjs": mode},
        [{"id": "ask", "name": "./ask.mjs"}, {"id": "mode", "name": "./mode.mjs"}])
    opened, closed = asyncio.Event(), asyncio.Event()
    async def question(questions):
        try:
            opened.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
    async def execute(tool, arguments):
        return await tool.invoke(arguments)
    context = ToolContext(workspace, shared_state={"agent_session": Session(), "user_question_handler": question,
                                                 "harness_tool_executor": execute})
    try:
        await manager.enable_async(identifier, workspace)
        code = "void tools.ask_user_question({questions:[{id:'q',question:'Wait?'}]}); await new Promise(resolve=>setTimeout(resolve,100)); return 'settled';"
        result = await asyncio.wait_for(manager.create_tools(context)[0].execute(code=code), 5)
        assert not result.is_error, result.error
        assert json.loads(result.content)["value"] == "settled"
        assert opened.is_set() and closed.is_set()
        assert all(not entry["worker"]._host_tasks for entry in manager._workers.values())
    finally:
        await manager.aclose()


async def test_harness_agent_driver_uses_real_scoped_python_worker_and_approval(tmp_path, workspace, runtime_supported):
    from libre_claw.config import load_config
    from libre_claw.core.agent import Agent, AgentPermissionRequest
    from libre_claw.core.permissions import PermissionManager
    from libre_claw.core.tools import ToolRegistry
    from libre_claw.providers.base import LLMProvider, TextDelta, Done
    from libre_claw.tools_builtin.filesystem import ReadFileTool
    from libre_claw.tools_builtin.subagents import SubagentSpawnTool

    requests = []
    class Provider(LLMProvider):
        model = "test"
        async def complete(self, messages, **kwargs):
            requests.append(messages)
            yield TextDelta("Scoped worker result")
            yield Done()

    context = ToolContext(workspace, default_provider="fake", default_model="test", subagent_provider_factory=lambda *_: Provider())
    spawning = SubagentSpawnTool(context)
    spawning.permission_level = "ask"
    registry = ToolRegistry([ReadFileTool(context), spawning])
    parent = Agent(session=Session(), provider=Provider(), tool_registry=registry, system_prompt="Test worker boundaries",
                   permission_manager=PermissionManager(load_config(working_directory=workspace).permissions))
    parent.session.add_user_message("private parent transcript")
    parent.accepting_control = True
    manager = CordisManager(tmp_path / "registry", persistent=True)
    source = b'''
import {defineTool} from '@deepseek-ai/dsh-tools';import{createUserMessage}from'@deepseek-ai/dsh-llm';
export const inject=['tools','agents'];export function apply(ctx){
ctx.tools.register(defineTool({name:'delegate',description:'Run a scoped worker',parameters:{},
output:{schema:{type:'string'},render:(_args,v)=>[{type:'text',text:v}]},execute:async(_args,exec)=>{
 const handle=await ctx.agents.create({sessionId:'child-'+exec.callId,parentAgent:exec.agent});
 try{handle.agent.followup(createUserMessage({content:[{type:'text',text:'Inspect the explicit assignment only'}],source:{kind:'plugin',plugin:'fixture'}}));
 await handle.agent.whenIdle();return handle.agent.session.events.map(event=>event.data.content?.map(block=>block.text).join('')).join('');}
 finally{await handle.dispose();}
}}));}'''
    identifier = install(manager, tmp_path / "package", {"agent.mjs": source}, [{"id": "agent", "name": "./agent.mjs"}])
    pending = None
    try:
        await manager.enable_async(identifier, workspace, read_paths=[str(workspace)], allow_model=True)
        tool = manager.create_tools(context)[0]
        pending = asyncio.create_task(tool.execute())
        async with asyncio.timeout(10):
            while True:
                event = await parent.control_events.get()
                if isinstance(event, AgentPermissionRequest):
                    assert event.call.name == "subagent_spawn" and event.call.arguments["read_only"] is True
                    assert not requests
                    event.future.set_result("allow_once")
                    break
            result = await pending
        assert not result.is_error and "Scoped worker result" in result.content, result.error or result.content
        assert requests and "private parent transcript" not in repr(requests)
        assert all(state.status == "done" for state in parent.subagents.states.values())
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await manager.aclose()
        await parent.subagents.close()
