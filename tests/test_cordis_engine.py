# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from libre_claw.core import cordis_engine
from libre_claw.core.cordis_engine import CordisEngine, CordisEngineError
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process


@pytest.fixture
def runtime_supported(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    try:
        prepare_cordis_process(node, cordis_engine._RUNTIME, cordis_engine._RUNTIME.parent, tmp_path / "probe")
    except CordisSecurityError as exc:
        pytest.skip(f"Enforced offline Node permissions unavailable: {exc}")
    return node


@pytest.fixture
async def engine(runtime_supported):
    async with CordisEngine(node_executable=runtime_supported) as engine:
        yield engine


async def test_real_persistent_cordis_services_activate_and_dispose(engine):
    snapshot = await engine.inspect()
    pid = engine.pid
    assert snapshot["engine"] == "cordis"
    assert snapshot["runtime_version"] == "4.0.2"
    assert snapshot["active_operations"] == 0
    assert {component["id"] for component in snapshot["components"]} == {"agent", "providers", "tools", "sessions", "memory", "workflows"}
    assert all(component["state"] == "ACTIVE" for component in snapshot["components"])
    assert "network-denied" in snapshot["isolation"]
    assert await engine.call("sessions", "checkpoint", handler=lambda: 42) == 42
    assert engine.pid == pid
    private_state = Path(engine._temporary.name)
    await engine.aclose()
    assert not engine.running
    assert engine._process.returncode is not None
    assert not private_state.exists()
    with pytest.raises(CordisEngineError, match="closed"):
        await engine.call("tools", "execute", handler=lambda: pytest.fail("Closed engine bypassed"))


async def test_component_configuration_enforces_real_dependencies_atomically(engine):
    before = await engine.inspect()
    with pytest.raises(CordisEngineError, match="requires"):
        await engine.configure({"providers": False})
    assert (await engine.inspect())["components"] == before["components"]
    disabled = await engine.configure({"memory": False, "workflows": False})
    assert not engine.is_enabled("memory")
    assert engine.is_enabled("agent")
    assert not engine.is_enabled("unknown")
    assert {item["id"] for item in disabled["components"] if item["state"] == "DISABLED"} == {"memory", "workflows"}
    with pytest.raises(CordisEngineError, match="disabled"):
        await engine.call("memory", "load", handler=lambda: pytest.fail("Disabled service bypassed"))
    activated = await engine.configure({"memory": True})
    assert engine.is_enabled("memory")
    assert next(item for item in activated["components"] if item["id"] == "memory")["state"] == "ACTIVE"
    assert await engine.call("memory", "load", handler=lambda: "remembered") == "remembered"


@pytest.mark.parametrize("components", [{"unknown": True}, {"tools": "yes"}, {"agent": False}])
async def test_invalid_configuration_does_not_change_services(engine, components):
    with pytest.raises(CordisEngineError):
        await engine.configure(components)
    assert all(item["enabled"] for item in (await engine.inspect())["components"])


async def test_concurrent_nested_operations_keep_original_python_objects(engine):
    outputs = [object() for _ in range(8)]

    async def outer(index):
        async def provider():
            yield await engine.call("tools", "execute", handler=lambda: outputs[index])
        async for result in engine.stream("providers", "complete", handler=provider):
            yield result

    async def consume(index):
        return [value async for value in engine.stream("agent", "run", handler=lambda: outer(index))]

    results = await asyncio.wait_for(asyncio.gather(*(consume(index) for index in range(8))), timeout=5)
    assert all(results[index][0] is outputs[index] for index in range(8))
    assert (await engine.inspect())["active_operations"] == 0


async def test_stream_backpressure_and_early_close_release_only_own_callback(engine):
    produced = []
    disposed = asyncio.Event()

    async def events():
        try:
            for index in range(100):
                produced.append(index)
                yield index
        finally:
            disposed.set()

    stream = engine.stream("agent", "run", handler=events)
    assert await anext(stream) == 0
    assert produced == [0]
    assert await engine.call("tools", "execute", handler=lambda: "parallel") == "parallel"
    assert produced == [0]
    assert engine.busy
    with pytest.raises(CordisEngineError, match="active"):
        await engine.aclose(cancel_active=False)
    assert engine.running
    with pytest.raises(CordisEngineError, match="active"):
        await engine.configure({"memory": False})
    await stream.aclose()
    await asyncio.wait_for(disposed.wait(), timeout=2)
    assert (await engine.inspect())["active_operations"] == 0
    assert await engine.call("sessions", "checkpoint", handler=lambda: "still healthy") == "still healthy"


async def test_consumer_cancellation_joins_host_cleanup(engine):
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def waiting():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(engine.call("tools", "execute", handler=waiting))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert (await engine.inspect())["active_operations"] == 0


async def test_callback_failure_and_self_cancellation_do_not_break_engine(engine):
    failure = ValueError("private error stays in Python")

    async def fail():
        raise failure

    with pytest.raises(ValueError) as result:
        await engine.call("tools", "execute", handler=fail)
    assert result.value is failure

    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(engine.call("tools", "execute", handler=cancel), timeout=2)
    assert await engine.call("tools", "execute", handler=lambda: "alive") == "alive"
    tools = next(component for component in (await engine.inspect())["components"] if component["id"] == "tools")
    assert tools["completed"] == 1
    assert tools["failed"] == 2


async def test_stream_failure_preserves_error_and_previous_items(engine):
    failure = RuntimeError("provider interrupted")

    async def events():
        yield "first"
        raise failure

    stream = engine.stream("providers", "complete", handler=events)
    assert await anext(stream) == "first"
    with pytest.raises(RuntimeError) as result:
        await anext(stream)
    assert result.value is failure
    assert (await engine.inspect())["active_operations"] == 0


async def test_transport_sends_only_scoped_handles_not_provider_data(engine, monkeypatch):
    original_send = engine._send
    frames = []
    private = {"api_key": "do-not-send-this", "transcript": "my private conversation"}

    async def capture(frame):
        frames.append(frame)
        await original_send(frame)

    monkeypatch.setattr(engine, "_send", capture)
    assert await engine.call("tools", "execute", handler=lambda: private) is private

    async def events():
        yield private

    assert [value async for value in engine.stream("providers", "complete", handler=events)] == [private]
    encoded = json.dumps(frames)
    assert "api_key" not in encoded and "transcript" not in encoded
    assert "do-not-send-this" not in encoded and "my private conversation" not in encoded
    assert all(set(frame) <= {"type", "id", "service", "method", "mode", "sequence"} for frame in frames)


async def test_unknown_method_never_invokes_callback(engine):
    with pytest.raises(CordisEngineError, match="Unknown"):
        await engine.call("providers", "read_credentials", handler=lambda: pytest.fail("Unauthorized operation"))
    assert (await engine.inspect())["active_operations"] == 0


async def test_process_crash_fails_pending_operations_closed(engine):
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def waiting():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    pending = asyncio.create_task(engine.call("tools", "execute", handler=waiting))
    await asyncio.wait_for(started.wait(), timeout=2)
    engine._process.kill()
    with pytest.raises(CordisEngineError, match="stopped"):
        await asyncio.wait_for(pending, timeout=3)
    assert cleaned.is_set()
    with pytest.raises(CordisEngineError):
        await engine.call("tools", "execute", handler=lambda: pytest.fail("A crashed engine bypassed service dispatch"))


async def test_close_cancels_pending_callbacks_and_wakes_consumers(engine):
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def waiting():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    pending = asyncio.create_task(engine.call("tools", "execute", handler=waiting))
    await asyncio.wait_for(started.wait(), timeout=2)
    await engine.aclose()
    with pytest.raises((CordisEngineError, asyncio.CancelledError)):
        await asyncio.wait_for(pending, timeout=2)
    assert cleaned.is_set()


async def test_oversized_control_rejected_without_killing_healthy_runtime(engine):
    with pytest.raises(CordisEngineError, match="64 KiB"):
        await engine.configure({"x" * cordis_engine.MAX_ENGINE_FRAME: True})
    assert (await engine.inspect())["state"] == "running"


async def test_core_process_has_clean_environment_private_cwd_and_offline_permissions(runtime_supported, monkeypatch):
    actual_prepare = cordis_engine.prepare_cordis_process
    recorded = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "parent-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--inspect=0.0.0.0:9229")

    def capture(*args, **kwargs):
        prepared = actual_prepare(*args, **kwargs)
        recorded.append(prepared)
        return prepared

    monkeypatch.setattr(cordis_engine, "prepare_cordis_process", capture)
    async with CordisEngine(node_executable=runtime_supported) as core:
        prepared = recorded[0]
        assert "DEEPSEEK_API_KEY" not in prepared.env and "NODE_OPTIONS" not in prepared.env
        assert prepared.env["HOME"] == str(prepared.cwd)
        assert prepared.env["DO_NOT_TRACK"] == "1"
        assert prepared.cwd.stat().st_mode & 0o777 == 0o700
        assert prepared.cwd != Path.cwd()
        assert not any(flag.startswith("--allow-net") for flag in prepared.command)
        assert "network-denied" in core.isolation


async def test_core_process_network_is_rejected_by_real_os_or_node_policy(runtime_supported, tmp_path, monkeypatch):
    received = []

    async def receiver(reader, writer):
        received.append(await reader.read(4096))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receiver, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        # Positive control proves this receiver can observe an allowed request.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /control HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()
        assert len(received) == 1
        received.clear()
        probe = tmp_path / "probe.mjs"
        probe.write_text(f"""
import {{ createInterface }} from 'node:readline';
let blocked = false;
try {{ await fetch('http://127.0.0.1:{port}/must-not-leave', {{signal: AbortSignal.timeout(2000)}}); }}
catch {{ blocked = true; }}
for await (const line of createInterface({{input:process.stdin}})) {{
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({{type:'result',id:request.id,result:{{blocked}}}})+'\\n');
  if (request.type === 'close') break;
}}
""")
        monkeypatch.setattr(cordis_engine, "_RUNTIME", probe)
        async with CordisEngine(node_executable=runtime_supported) as core:
            assert (await core.inspect())["blocked"] is True
        assert received == []
    finally:
        server.close()
        await server.wait_closed()


async def test_startup_failure_is_not_retried_as_unrestricted_process():
    engine = CordisEngine(node_executable="/missing/libre-claw-node")
    with pytest.raises(CordisEngineError, match="Node.js was not found"):
        await engine.start()
    with pytest.raises(CordisEngineError, match="could not start"):
        await engine.call("tools", "execute", handler=lambda: pytest.fail("Security failure bypassed"))
    await engine.aclose()


async def test_cancelled_dispatch_does_not_leave_remote_operation(engine, monkeypatch):
    original_send = engine._send
    dispatched = asyncio.Event()

    async def interrupted_send(frame):
        await original_send(frame)
        if frame["type"] == "invoke":
            dispatched.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_send", interrupted_send)
    task = asyncio.create_task(engine.call("tools", "execute", handler=lambda: None))
    await asyncio.wait_for(dispatched.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not engine.busy
    assert (await engine.inspect())["active_operations"] == 0
