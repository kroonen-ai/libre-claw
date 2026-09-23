# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import threading
from pathlib import Path

import pytest

from libre_claw.core.cordis_ptc import CordisPtcPool


@pytest.fixture
async def pool(tmp_path):
    value = CordisPtcPool(tmp_path, lambda: {"read_paths": [], "write_paths": []})
    try:
        yield value
    finally:
        await value.aclose()


async def finished(pool, identity):
    async with asyncio.timeout(10):
        while True:
            row = await pool.dispatch("read", {"id": identity})
            if row["type"] != "pending":
                return row


async def test_typescript_ptc_calls_actual_binding_and_returns_typed_value(pool):
    run = await pool.start({"program": "const value: number = await tools.double(21); console.log('finished'); return {value};",
                            "bindings": [{"global": "tools", "members": ["double"]}]})
    call = await finished(pool, run["id"])
    assert call == {"type": "call", "id": 1, "namespace": "tools", "member": "double", "args": [21]}
    await pool.dispatch("respond", {"id": run["id"], "call_id": call["id"], "value": 42})
    result = await finished(pool, run["id"])
    assert result == {"type": "done", "result": {"value": {"value": 42}, "logs": ["finished"]}}
    job = pool.jobs[run["id"]]
    assert job["process"].returncode is not None
    assert not Path(job["temporary"].name).exists()


async def test_typescript_runtime_enums_and_constructor_parameter_properties(pool):
    run = await pool.start({"program": """
enum Stage { Pending = 1, Ready }
class Result {
  constructor(public stage: Stage, private value: number) {}
  read(): number { return this.value; }
}
const result = new Result(Stage.Ready, 42);
return {stage: result.stage, name: Stage[result.stage], value: result.read()};
""", "bindings": []})
    assert (await finished(pool, run["id"]))["result"]["value"] == {"stage": 2, "name": "Ready", "value": 42}


async def test_typescript_compilation_rejects_syntax_without_type_checking(pool):
    typed = await pool.start({"program": "const value: number = 'runtime value'; return value;", "bindings": []})
    assert (await finished(pool, typed["id"]))["result"]["value"] == "runtime value"
    invalid = await pool.start({"program": "const value: = 1; return value;", "bindings": []})
    assert (await finished(pool, invalid["id"]))["result"]["error"]["kind"] == "exception"


async def test_binding_rejection_preserves_declared_error_class(pool):
    run = await pool.start({"program": "try { await tools.read({}); } catch (e) { return {typed:e instanceof ToolError,name:e.name,member:e.toolName}; }",
        "bindings": [{"global": "tools", "members": ["read"], "errorClass": {"name": "ToolError", "memberNameProperty": "toolName"}}]})
    call = await finished(pool, run["id"])
    await pool.dispatch("respond", {"id": run["id"], "call_id": call["id"], "error": "Permission denied"})
    assert (await finished(pool, run["id"]))["result"]["value"] == {"typed": True, "name": "ToolError", "member": "read"}


async def test_infinite_program_is_killed_and_joined(pool):
    run = await pool.start({"program": "while (true) {}", "bindings": [], "timeoutMs": 400})
    result = await finished(pool, run["id"])
    assert result["result"]["error"]["kind"] == "timeout"
    assert pool.jobs[run["id"]]["process"].returncode is not None


async def test_ptc_cannot_read_host_credentials_or_ungranted_files(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "parent-only-secret")
    private = tmp_path / "private.txt"
    private.write_text("workspace-private")
    run = await pool.start({"program": f"const fs = await import('node:fs/promises'); let denied=false; try {{await fs.readFile({json.dumps(str(private))});}} catch {{denied=true;}} return {{key:process.env.DEEPSEEK_API_KEY??null,denied}};", "bindings": []})
    assert (await finished(pool, run["id"]))["result"]["value"] == {"key": None, "denied": True}


async def test_ptc_cannot_open_network_connections(pool):
    received = []
    async def receiver(reader, writer):
        received.append(True)
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(receiver, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
        assert received == [True]
        received.clear()
        run = await pool.start({"program": f"try {{ await fetch('http://127.0.0.1:{port}/',{{signal:AbortSignal.timeout(1000)}}); return false; }} catch {{ return true; }}", "bindings": []})
        assert (await finished(pool, run["id"]))["result"]["value"] is True
        assert received == []
    finally:
        server.close()
        await server.wait_closed()


async def test_revocation_and_disposal_kill_programs(tmp_path):
    grants = {"read_paths": [], "write_paths": []}
    allowed = True
    def authorize():
        if not allowed:
            raise PermissionError("Revoked")
        return grants
    pool = CordisPtcPool(tmp_path, authorize)
    run = await pool.start({"program": "while(true) {}", "bindings": []})
    allowed = False
    job = pool.jobs[run["id"]]
    async with asyncio.timeout(5):
        await job["watcher"]
    assert job["process"].returncode is not None
    assert job["result"]["error"]["kind"] == "abort"
    await pool.aclose()
    assert pool.jobs == {}


@pytest.mark.parametrize("program", ["return ()=>42;", "return 1n;", "return {x:undefined};", "return NaN;"])
async def test_ptc_rejects_non_json_results(pool, program):
    run = await pool.start({"program": program, "bindings": []})
    assert (await finished(pool, run["id"]))["result"]["error"]["kind"] == "invalid-output"


async def test_ptc_rejects_cross_run_binding_responses_and_invalid_grants(pool, tmp_path):
    first = await pool.start({"program": "return await tools.read({});", "bindings": [{"global": "tools", "members": ["read"]}]})
    second = await pool.start({"program": "return 0;", "bindings": []})
    call = await finished(pool, first["id"])
    with pytest.raises(ValueError, match="Unknown PTC binding"):
        await pool.dispatch("respond", {"id": second["id"], "call_id": call["id"], "value": "wrong-run"})
    with pytest.raises(PermissionError, match="workspace"):
        await pool.start({"program": "return 0", "bindings": [], "cwd": str(tmp_path.parent)})
    with pytest.raises(ValueError, match="global"):
        await pool.start({"program": "return 0", "bindings": [{"global": "$tools", "members": []}]})


@pytest.mark.parametrize("arguments", [[], [{}, {}]])
async def test_raw_protocol_cannot_bypass_binding_argument_contract(pool, arguments):
    frame = {"type": "call", "id": 1, "namespace": "tools", "member": "read", "args": arguments}
    run = await pool.start({
        "program": f"process.stdout.write({json.dumps(json.dumps(frame) + chr(10))}); await new Promise(() => {{}});",
        "bindings": [{"global": "tools", "members": ["read"]}],
    })
    result = await finished(pool, run["id"])
    assert result["type"] == "done"
    assert result["result"]["error"]["kind"] == "protocol"
    assert pool.jobs[run["id"]]["process"].returncode is not None


async def test_read_only_policy_narrows_existing_write_grants(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("original")
    pool = CordisPtcPool(tmp_path, lambda: {"read_paths": [], "write_paths": [str(tmp_path)]})
    try:
        run = await pool.start({"program": f"const fs=await import('node:fs/promises'); try {{ await fs.writeFile({json.dumps(str(target))}, 'changed'); return false; }} catch {{ return true; }}",
            "bindings": [], "sandboxPolicy": {"mode": "read-only", "workspaceRoot": str(tmp_path)}})
        assert (await finished(pool, run["id"]))["result"]["value"] is True
        assert target.read_text() == "original"
    finally:
        await pool.aclose()


async def test_prototype_like_member_names_remain_own_json_bindings(pool):
    run = await pool.start({"program": "return await tools['__proto__']({value:21});", "bindings": [{"global": "tools", "members": ["__proto__"]}]})
    call = await finished(pool, run["id"])
    assert call["member"] == "__proto__"
    await pool.dispatch("respond", {"id": run["id"], "call_id": call["id"], "value": 42})
    assert (await finished(pool, run["id"]))["result"]["value"] == 42


async def test_cancelled_start_joins_preparation_and_removes_private_files(tmp_path, monkeypatch):
    from libre_claw.core import cordis_ptc
    prepare = cordis_ptc.prepare_cordis_process
    entered, release = threading.Event(), threading.Event()
    directories = []
    def delayed(*args, **kwargs):
        directories.append(args[3])
        entered.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)
    monkeypatch.setattr(cordis_ptc, "prepare_cordis_process", delayed)
    pool = CordisPtcPool(tmp_path, lambda: {"read_paths": [], "write_paths": []})
    task = asyncio.create_task(pool.start({"program": "return 1", "bindings": []}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        close = asyncio.create_task(pool.aclose())
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await close
        assert task.cancelled()
        assert directories and all(not path.exists() for path in directories)
        assert pool.jobs == {}
    finally:
        release.set()
        await pool.aclose()
