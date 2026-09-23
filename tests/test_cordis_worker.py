# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.cordis_worker import CordisWorker, CordisWorkerError


RUNTIME = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/runtime.mjs"


@pytest.fixture
def node_executable():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is unavailable")
    return executable


def worker_fixture(tmp_path, node_executable, source, *, harness=False, background=None):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    entry = plugin / "plugin.mjs"
    entry.write_text(source)
    state = tmp_path / "state"
    try:
        prepared = prepare_cordis_process(node_executable, RUNTIME, plugin, state)
    except CordisSecurityError as error:
        pytest.skip(f"Enforced offline Node permissions unavailable: {error}")
    initialize = {"entry": str(entry.resolve()), "plugin_id": "worker_test", "state_dir": str(state.resolve())}
    if harness:
        initialize.update(harness={"packages": []}, host_services=["llm", "userQuestions", "sessionProjections"])
    return CordisWorker(prepared, initialize, background_host_handler=background), state


COUNTER = """
export const inject=['libre'];
export function apply(ctx) {
 let count=0;
 ctx.libre.registerTool({name:'count',description:'Counter',input_schema:{type:'object'}}, async () => String(++count));
 ctx.libre.registerTool({name:'large',description:'Large',input_schema:{type:'object'}}, () => 'x'.repeat(400000));
 ctx.effect(() => async () => { await ctx.libre.storage.set('disposed', {count}); });
}
"""


async def test_persistent_real_cordis_retains_state_and_runs_async_disposer(tmp_path, node_executable):
    worker, state = worker_fixture(tmp_path, node_executable, COUNTER)
    async with worker:
        pid = worker.pid
        assert worker.startup["state"] == "ACTIVE"
        assert "network-denied" in worker.isolation
        for value in range(1, 5):
            assert await worker.request("tools/call", {"name": "count", "arguments": {}}) == {"content": str(value), "error": False}
            assert worker.pid == pid == worker.processPid
        assert worker.running
    assert not worker.running
    assert worker._process.returncode is not None
    assert json.loads((state / "disposed.json").read_text()) == {"count": 4}
    assert not worker._host_tasks
    assert worker._reader.done()
    with pytest.raises(CordisWorkerError, match="closed"):
        await worker.request("inspect")


async def test_concurrent_requests_are_serialized_and_traffic_budget_resets(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, COUNTER)
    async with worker:
        results = await asyncio.gather(*[worker.request("tools/call", {"name": "count", "arguments": {}}) for _ in range(20)])
        assert [int(result["content"]) for result in results] == list(range(1, 21))
        # More than 4 MiB over the worker lifetime remains valid: each RPC has
        # its own budget, so a healthy long-running extension does not age out.
        for _ in range(12):
            result = await worker.request("tools/call", {"name": "large", "arguments": {}})
            assert len(result["content"]) == 400000


async def test_cancellation_terminates_hung_plugin_and_does_not_leave_reader_tasks(tmp_path, node_executable):
    source = """export const inject=['libre'];export function apply(ctx){ctx.libre.registerTool(
 {name:'hang',description:'Hang',input_schema:{type:'object'}},()=>new Promise(()=>{}));}"""
    worker, _ = worker_fixture(tmp_path, node_executable, source)
    await worker.start()
    pending = asyncio.create_task(worker.request("tools/call", {"name": "hang", "arguments": {}}))
    await asyncio.sleep(0.03)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not worker.running
    assert worker._process.returncode is not None
    assert worker._reader.done()
    assert not worker._host_tasks
    await worker.aclose()


async def test_request_timeout_closes_worker(tmp_path, node_executable):
    source = """export const inject=['libre'];export function apply(ctx){ctx.libre.registerTool(
 {name:'hang',description:'Hang',input_schema:{type:'object'}},()=>new Promise(()=>{}));}"""
    worker, _ = worker_fixture(tmp_path, node_executable, source)
    await worker.start()
    with pytest.raises(CordisWorkerError, match="timed out"):
        await worker.request("tools/call", {"name": "hang", "arguments": {}}, timeout=0.02)
    assert not worker.running
    await worker.aclose()


MODEL_TOOL = """
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','llm'];
export function apply(ctx){ctx.tools.register(defineTool({
 name:'model',description:'Model',parameters:{},
 output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
 async execute(){let count=0;for await(const chunk of ctx.llm.stream({provider:'fake',model:'fake',messages:[]})){count++;}return String(count);}
}));}
"""


async def test_stream_cancellation_closes_host_generator_and_revokes_callback(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, MODEL_TOOL, harness=True)
    opened, closed = asyncio.Event(), asyncio.Event()

    async def stream_chunks():
        try:
            opened.set()
            yield {"type": "text-delta", "index": 0, "text": "first"}
            await asyncio.Event().wait()
        finally:
            closed.set()

    def handler(method, params, *, stream, timeout):
        assert method == "llm.stream" and stream is True and timeout is not None
        return stream_chunks()

    await worker.start()
    pending = asyncio.create_task(worker.request("tools/call", {"name": "model", "arguments": {}}, handler))
    await asyncio.wait_for(opened.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed.is_set()
    assert not worker.running
    assert not worker._host_tasks
    await worker.aclose()


async def test_cumulative_host_stream_budget_is_per_operation_and_closes_generator(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, MODEL_TOOL, harness=True)
    closed = asyncio.Event()

    async def stream_chunks():
        try:
            for _ in range(20):
                yield {"type": "text-delta", "index": 0, "text": "x" * 450000}
        finally:
            closed.set()

    def handler(_method, _params, *, stream, timeout):
        return stream_chunks()

    await worker.start()
    with pytest.raises(CordisWorkerError, match="limit"):
        await worker.request("tools/call", {"name": "model", "arguments": {}}, handler)
    assert closed.is_set()
    assert not worker.running
    await worker.aclose()


async def test_interactive_host_can_pause_the_request_deadline(tmp_path, node_executable):
    source = """
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','userQuestions'];export function apply(ctx){ctx.tools.register(defineTool({
 name:'ask',description:'Ask',parameters:{},output:{schema:{type:'string'},render:(_args,v)=>[{type:'text',text:v}]},
 async execute(){const answer=await ctx.userQuestions.ask({questions:[{id:'q',question:'Continue?'}]});return answer.answers[0].selected[0];}
}));}
"""
    worker, _ = worker_fixture(tmp_path, node_executable, source, harness=True)

    async def handler(method, _params, *, stream, timeout):
        assert method == "userQuestions.ask" and not stream
        assert timeout is not None
        remaining = timeout.when() - asyncio.get_running_loop().time()
        timeout.reschedule(None)
        await asyncio.sleep(0.1)
        timeout.reschedule(asyncio.get_running_loop().time() + remaining)
        return {"answers": [{"id": "q", "selected": ["continue"]}]}

    async with worker:
        result = await worker.request("tools/call", {"name": "ask", "arguments": {}}, handler, timeout=0.05)
        assert result == {"content": "continue", "error": False}


async def test_cancelled_host_binding_is_joined_without_terminating_the_plugin(tmp_path, node_executable):
    source = """
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','userQuestions'];export function apply(ctx){ctx.tools.register(defineTool({
 name:'ask',description:'Cancel a question',parameters:{},output:{schema:{type:'string'},render:(_args,v)=>[{type:'text',text:v}]},
 async execute(){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),40);
 try{await ctx.userQuestions.ask({questions:[{id:'q',question:'Continue?'}],signal:controller.signal});return 'unexpected';}
 catch{return 'cancelled';}finally{clearTimeout(timer);}}
}));}
"""
    worker, _ = worker_fixture(tmp_path, node_executable, source, harness=True)
    opened, closed = asyncio.Event(), asyncio.Event()
    async def handler(method, params, *, stream, timeout):
        assert method == "userQuestions.ask"
        try:
            opened.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
    async with worker:
        result = await worker.request("tools/call", {"name": "ask", "arguments": {}}, handler)
        assert result == {"content": "cancelled", "error": False}
        assert opened.is_set() and closed.is_set()
        assert worker.running and not worker._host_tasks


BACKGROUND = """
export const inject=['llm','libre'];export function apply(ctx){
 ctx.libre.registerTool({name:'schedule',description:'Schedule',input_schema:{type:'object'}},async()=>{
   await ctx.llm.listModels('fake');
   const timer=setTimeout(async()=>{try{const result=await ctx.llm.listModels('fake');await ctx.libre.storage.set('background',result);}catch{await ctx.libre.storage.set('background','denied');}},70);
   ctx.effect(()=>()=>clearTimeout(timer));
   return 'scheduled';
 });
}
"""


async def wait_json(path):
    for _ in range(100):
        if path.is_file():
            return json.loads(path.read_text())
        await asyncio.sleep(0.01)
    raise AssertionError("Background fixture did not complete")


async def test_request_host_handler_is_revoked_before_late_background_calls(tmp_path, node_executable):
    worker, state = worker_fixture(tmp_path, node_executable, BACKGROUND, harness=True)
    calls = []

    async def handler(method, _params, *, stream, timeout):
        calls.append(method)
        return [{"id": "foreground"}]

    async with worker:
        await worker.request("tools/call", {"name": "schedule", "arguments": {}}, handler)
        assert await wait_json(state / "background.json") == "denied"
        assert calls == ["llm.listModels"]
        assert worker.running


async def test_explicit_background_handler_is_separate_from_task_handler(tmp_path, node_executable):
    background_calls = []

    async def background(method, _params, *, stream, timeout):
        background_calls.append(method)
        return [{"id": "background"}]

    worker, state = worker_fixture(tmp_path, node_executable, BACKGROUND, harness=True, background=background)

    async def foreground(_method, _params, *, stream, timeout):
        return [{"id": "foreground"}]

    async with worker:
        await worker.request("tools/call", {"name": "schedule", "arguments": {}}, foreground)
        assert await wait_json(state / "background.json") == [{"id": "background"}]
        assert background_calls == ["llm.listModels"]


async def test_worker_uses_only_prepared_environment_and_conceals_host_errors(tmp_path, node_executable, monkeypatch):
    monkeypatch.setenv("PRIVATE_TEST_CREDENTIAL", "must-not-inherit")
    source = """
export const inject=['libre','llm'];export function apply(ctx){
 ctx.libre.registerTool({name:'env',description:'Environment',input_schema:{type:'object'}},()=>String(process.env.PRIVATE_TEST_CREDENTIAL));
 ctx.libre.registerTool({name:'failure',description:'Failure',input_schema:{type:'object'}},async()=>{try{await ctx.llm.listModels('fake');}catch(error){return error.message;}});
}
"""
    worker, _ = worker_fixture(tmp_path, node_executable, source, harness=True)

    async def handler(_method, _params, *, stream, timeout):
        raise ValueError("private exception credential")

    async with worker:
        assert (await worker.request("tools/call", {"name": "env", "arguments": {}}))["content"] == "undefined"
        result = await worker.request("tools/call", {"name": "failure", "arguments": {}}, handler)
        assert "credential" not in result["content"]
        assert "rejected" in result["content"]


async def test_queued_request_timeout_does_not_cancel_current_owner(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, MODEL_TOOL, harness=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def chunks():
        entered.set()
        await release.wait()
        yield {"type": "finish", "reason": {"kind": "stop"}}

    def handler(_method, _params, *, stream, timeout):
        return chunks()

    async with worker:
        current = asyncio.create_task(worker.request("tools/call", {"name": "model", "arguments": {}}, handler))
        await entered.wait()
        with pytest.raises(CordisWorkerError, match="timed out"):
            await worker.request("inspect", timeout=0.02)
        assert worker.running
        assert not current.done()
        release.set()
        assert (await current)["content"] == "1"
        assert (await worker.request("inspect"))["state"] == "ACTIVE"


async def test_stable_session_identity_preserves_plugin_weakmap_state_without_cross_session_leaks(tmp_path, node_executable):
    source = """
import {defineTool} from '@deepseek-ai/dsh-tools';export const inject=['tools'];
export function apply(ctx){const seen=new WeakMap();ctx.tools.register(defineTool({
 name:'session',description:'Session state',parameters:{},output:{schema:{type:'integer'},render:(_args,v)=>[{type:'text',text:String(v)}]},
 async execute(_args,exec){const value=(seen.get(exec.agent.session)||0)+1;seen.set(exec.agent.session,value);return value;}
}));}
"""
    worker, _ = worker_fixture(tmp_path, node_executable, source, harness=True)
    async with worker:
        async def call(session):
            return (await worker.request("tools/call", {"name": "session", "arguments": {}, "context": {"session_id": session, "agent_id": session}}))["content"]
        assert await call("first") == "1"
        assert await call("second") == "1"
        assert await call("first") == "2"
        assert await call("second") == "2"


async def test_oversized_frame_fails_closed_without_echoing_private_payload(tmp_path, node_executable):
    source = """export const inject=['libre'];export function apply(ctx){ctx.libre.registerTool(
 {name:'bad',description:'Bad frame',input_schema:{type:'object'}},()=>{process.stdout.write('private-value'+'x'.repeat(1100000)+'\\n');return 'bad';});}"""
    worker, _ = worker_fixture(tmp_path, node_executable, source)
    await worker.start()
    with pytest.raises(CordisWorkerError) as caught:
        await worker.request("tools/call", {"name": "bad", "arguments": {}})
    assert "private-value" not in str(caught.value)
    assert not worker.running
    assert worker._reader.done()
    await worker.aclose()


async def test_oversized_request_is_bounded_and_does_not_leave_an_unread_future(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, COUNTER)
    await worker.start()
    with pytest.raises(CordisWorkerError, match="size limit"):
        await worker.request("tools/call", {"name": "count", "arguments": {"text": "x" * 1100000}})
    assert not worker.running
    await worker.aclose()


async def test_idle_process_exit_closes_transport_automatically(tmp_path, node_executable):
    source = """export function apply(ctx){const timer=setTimeout(()=>process.exit(0),70);ctx.effect(()=>()=>clearTimeout(timer));}"""
    worker, _ = worker_fixture(tmp_path, node_executable, source)
    await worker.start()
    for _ in range(100):
        if worker._closed:
            break
        await asyncio.sleep(0.01)
    assert worker._closed
    assert not worker.running
    assert worker._reader.done()
    assert not worker._host_tasks
    await worker.aclose()


async def test_cancelled_worker_start_joins_created_process_and_pipes(tmp_path, node_executable, monkeypatch):
    worker, _ = worker_fixture(tmp_path, node_executable, COUNTER)
    original = asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    starting = asyncio.create_task(worker.start())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        starting.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 5)
        assert processes and processes[0].returncode is not None
        assert processes[0].stdin.is_closing()
        assert processes[0].stdout.at_eof()
    finally:
        release.set()
        await worker.aclose()


async def test_joined_host_tasks_retire_before_deferred_done_callbacks(tmp_path, node_executable):
    worker, _ = worker_fixture(tmp_path, node_executable, COUNTER)
    async def completed():
        return None
    task = asyncio.create_task(completed())
    await task
    task._cordis_host_id = "reused-after-join"
    worker._host_tasks[task] = None
    worker._active_host_ids.add(task._cordis_host_id)
    await worker._join_host_tasks(all_tasks=True)
    assert not worker._host_tasks
    assert not worker._active_host_ids
    # A late callback cannot revoke an identifier claimed by a later operation.
    worker._active_host_ids.add(task._cordis_host_id)
    worker._host_done(task, task._cordis_host_id)
    assert worker._active_host_ids == {task._cordis_host_id}
