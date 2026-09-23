# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shutil
import stat
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest

from libre_claw.config import load_config
from libre_claw.core import cordis_ipc
from libre_claw.core.cordis_ipc import CordisIpcError, CordisNativeProviderServer, NATIVE_PROVIDER_INSTANCE_HEADER, validate_native_config
from libre_claw.core.cordis_llm import CordisLlmBridge
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.providers.base import Done, TextDelta
from libre_claw.providers.model_catalog import ModelCatalog, ModelInfo


@pytest.fixture
def socket_path():
    # pytest's full test-name directories exceed macOS's Unix socket limit.
    with tempfile.TemporaryDirectory(prefix="lc-ipc-") as directory:
        yield Path(directory).resolve() / "native.sock"


class FakeProvider:
    def __init__(self, config):
        self.config = config
        self.granted = True
        self.calls = 0
        self.wait = False
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.bridge = CordisLlmBridge(config, authorize=lambda: self.granted, provider_factory=lambda *args, **kwargs: self, model_discovery=self.discover)

    async def discover(self, config, provider):
        return ModelCatalog((ModelInfo("deepseek", "test-model", "Test model", context_window_tokens=10000, max_completion_tokens=1000),), "test")

    async def complete(self, messages, **kwargs):
        self.calls += 1
        self.started.set()
        try:
            yield TextDelta("private reply")
            if self.wait:
                await asyncio.Event().wait()
            yield Done(stop_reason="stop")
        finally:
            self.finished.set()


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = replace(load_config(), providers={"deepseek": {"api_key": "never-send-private-key", "default_model": "test-model"}})
    return FakeProvider(config)


def request(**changes):
    return {"provider": "deepseek", "model": "test-model", "messages": [
        {"id": "u1", "role": "user", "content": [{"type": "text", "text": "Explicit user input"}], "source": {"kind": "user"}},
    ], **changes}


async def test_private_socket_catalog_generate_and_cleanup_match_native_protocol(socket_path, provider):
    async with CordisNativeProviderServer(socket_path, provider.bridge) as server:
        assert server.running and server.active_requests == 0
        assert stat.S_ISSOCK(socket_path.lstat().st_mode)
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            async with client.post("http://localhost/catalog", json={}) as response:
                assert response.status == 200
                catalog = await response.json()
                assert response.headers[NATIVE_PROVIDER_INSTANCE_HEADER] == catalog["instanceId"] == server.instance_id
                assert catalog["models"] == [{"providerId": "deepseek", "providerName": "DeepSeek", "model": "test-model", "name": "Test model", "contextWindow": 10000, "defaultMaxTokens": 1000}]
                assert "never-send-private-key" not in json.dumps(catalog)
            async with client.post("http://localhost/generate", json=request(instanceId=catalog["instanceId"])) as response:
                assert response.status == 200
                assert response.headers[NATIVE_PROVIDER_INSTANCE_HEADER] == catalog["instanceId"]
                assert response.content_type == "application/x-ndjson"
                chunks = [json.loads(line) for line in (await response.text()).splitlines()]
                assert any(chunk.get("text") == "private reply" for chunk in chunks)
                assert chunks[-1] == {"type": "finish", "reason": {"kind": "stop"}}
                node = shutil.which("node")
                if node:
                    protocol = Path(__file__).parent / "fixtures/native-provider-0.1.1/runtime/native-provider-protocol.js"
                    fixture = socket_path.parent / "responses.json"
                    fixture.write_text(json.dumps({"catalog": catalog, "chunks": chunks}))
                    script = f"""
import {{readFileSync}} from 'node:fs';
import {{parseNativeProviderCatalog,parseNativeProviderChunk}} from {json.dumps(protocol.as_uri())};
const data=JSON.parse(readFileSync(process.argv[1],'utf8'));
parseNativeProviderCatalog(data.catalog); data.chunks.forEach(parseNativeProviderChunk);
"""
                    completed = subprocess.run([node, '--input-type=module', '--eval', script, str(fixture)], capture_output=True, text=True, timeout=10)
                    assert completed.returncode == 0, completed.stderr
        assert provider.calls == 1 and provider.finished.is_set()
    assert not socket_path.exists() and not server.running


@pytest.mark.parametrize("kind", ["file", "socket", "symlink"])
async def test_existing_path_is_never_replaced(socket_path, provider, kind):
    listener = None
    if kind == "file":
        socket_path.write_text("keep")
    elif kind == "symlink":
        socket_path.symlink_to(socket_path.parent / "nonexistent")
    else:
        import socket
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
    original = socket_path.lstat()
    try:
        server = CordisNativeProviderServer(socket_path, provider.bridge)
        with pytest.raises(CordisIpcError, match="already exists"):
            await server.start()
        await server.aclose()
        assert socket_path.lstat().st_ino == original.st_ino
    finally:
        if listener:
            listener.close()


async def test_socket_parent_must_be_physical_private_and_owned(socket_path, provider):
    socket_path.parent.chmod(0o755)
    with pytest.raises(CordisIpcError, match="0700"):
        await CordisNativeProviderServer(socket_path, provider.bridge).start()
    socket_path.parent.chmod(0o700)
    physical = socket_path.parent / "physical"
    physical.mkdir(mode=0o700)
    alias = socket_path.parent / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    with pytest.raises(CordisIpcError, match="symbolic"):
        await CordisNativeProviderServer(alias / "s", provider.bridge).start()


@pytest.mark.parametrize("path", ["relative.sock", "/tmp/../native.sock", "/" + "x" * 101, "/"])
def test_invalid_socket_path_rejected(path, provider):
    with pytest.raises(CordisIpcError):
        CordisNativeProviderServer(path, provider.bridge)


def test_native_config_expands_only_private_state_or_explicit_write_grants(socket_path):
    config = {"socketPath": {"$libreStatePath": "native.sock"}}
    assert validate_native_config(config, state_dir=socket_path.parent) == {
        "socketPath": str(socket_path), "requestTimeoutMs": 600000, "maxConcurrentRequests": 8,
    }
    outside = socket_path.parent.parent / "other.sock"
    with pytest.raises(CordisIpcError, match="write grant"):
        validate_native_config({"socketPath": str(outside)}, state_dir=socket_path.parent)
    assert validate_native_config({"socketPath": str(outside)}, state_dir=socket_path.parent, write_paths=(outside,))["socketPath"] == str(outside)
    for value in ("../escape.sock", "/absolute.sock", "a//b", "a/./b", "a\\b"):
        with pytest.raises(CordisIpcError):
            validate_native_config({"socketPath": {"$libreStatePath": value}}, state_dir=socket_path.parent)
    for value in ({"socketPath": str(socket_path), "network": True}, {"socketPath": str(socket_path), "maxConcurrentRequests": True}, {"socketPath": str(socket_path), "requestTimeoutMs": 0}):
        with pytest.raises(CordisIpcError):
            validate_native_config(value, state_dir=socket_path.parent)


async def test_no_model_grant_means_no_socket_or_provider_call(socket_path, provider):
    provider.granted = False
    with pytest.raises(PermissionError):
        await CordisNativeProviderServer(socket_path, provider.bridge).start()
    assert not socket_path.exists() and provider.calls == 0


async def test_revoked_grant_and_stale_instances_fail_before_generation(socket_path, provider):
    async with CordisNativeProviderServer(socket_path, provider.bridge) as server:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            old = server.instance_id
            await server.rotate()
            async with client.post("http://localhost/generate", json=request(instanceId=old)) as response:
                assert response.status == 409
            async with client.post("http://localhost/generate", json=request(), headers={NATIVE_PROVIDER_INSTANCE_HEADER: old}) as response:
                assert response.status == 409
            provider.granted = False
            async with client.post("http://localhost/catalog", json={}) as response:
                assert response.status == 403
            async with client.post("http://localhost/generate", json=request()) as response:
                assert response.status == 403
        assert provider.calls == 0


async def test_rotation_cancels_live_generation_with_terminal_changed_error(socket_path, provider):
    provider.wait = True
    async with CordisNativeProviderServer(socket_path, provider.bridge) as server:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            async with client.post("http://localhost/generate", json=request(instanceId=server.instance_id)) as response:
                first = await response.content.readline()
                assert json.loads(first)["type"] == "block-start"
                previous = response.headers[NATIVE_PROVIDER_INSTANCE_HEADER]
                await asyncio.wait_for(server.rotate(), timeout=3)
                assert previous != server.instance_id
                chunks = [json.loads(line) for line in (await response.text()).splitlines()]
                assert chunks[-1]["reason"]["failure"]["code"] == "NATIVE_PROVIDER_CHANGED"
                assert provider.finished.is_set()


async def test_client_disconnect_cancels_provider_without_waiting_for_another_chunk(socket_path, provider):
    provider.wait = True
    async with CordisNativeProviderServer(socket_path, provider.bridge):
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            response = await client.post("http://localhost/generate", json=request())
            assert await response.content.readline()
            response.close()
            await asyncio.wait_for(provider.finished.wait(), timeout=3)


async def test_capacity_and_timeout_are_bounded(socket_path, provider):
    provider.wait = True
    async with CordisNativeProviderServer(socket_path, provider.bridge, max_concurrent_requests=1, request_timeout_ms=300) as server:
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            first = await client.post("http://localhost/generate", json=request())
            await first.content.readline()
            assert server.active_requests == 1
            async with client.post("http://localhost/catalog", json={}) as excess:
                assert excess.status == 503
            chunks = [json.loads(line) for line in (await first.text()).splitlines()]
            assert chunks[-1]["reason"]["kind"] == "error"
            assert "timed out" in chunks[-1]["reason"]["failure"]["message"]
            assert provider.finished.is_set()


async def test_invalid_bodies_and_unknown_models_do_not_call_provider(socket_path, provider, monkeypatch):
    async with CordisNativeProviderServer(socket_path, provider.bridge):
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
            for route, body, status in [('/catalog', {'unexpected': True}, 400), ('/generate', {}, 400), ('/generate', request(model='unknown'), 422), ('/unknown', {}, 404)]:
                async with client.post('http://localhost' + route, json=body) as response:
                    assert response.status == status
            async with client.get('http://localhost/catalog') as response:
                assert response.status == 404
            async with client.post('http://localhost/catalog', data='{}', headers={'Content-Type': 'text/plain'}) as response:
                assert response.status == 415
            async with client.post('http://localhost/catalog', data=b'\xff', headers={'Content-Type': 'application/json'}) as response:
                assert response.status == 400
            async with client.post('http://localhost/catalog', data='NaN', headers={'Content-Type': 'application/json'}) as response:
                assert response.status == 400
            monkeypatch.setattr(cordis_ipc, 'MAX_REQUEST_BYTES', 3)
            async with client.post('http://localhost/catalog', json={'large': True}) as response:
                assert response.status == 413
    assert provider.calls == 0


async def test_cleanup_preserves_a_replacement_file(socket_path, provider):
    server = CordisNativeProviderServer(socket_path, provider.bridge)
    await server.start()
    socket_path.unlink()
    socket_path.write_text('replacement')
    await server.aclose()
    assert socket_path.read_text() == 'replacement'


@pytest.mark.parametrize('node', list(dict.fromkeys(filter(None, [shutil.which('node'), '/opt/homebrew/bin/node' if Path('/opt/homebrew/bin/node').exists() else None]))))
def test_offline_node_unix_bind_is_denied_without_weakening_network_policy(node, socket_path):
    root = socket_path.parent
    script = root / 'probe.mjs'
    script.write_text("""
import net from 'node:net';
const finish = error => { process.stdout.write(JSON.stringify({code:error.code, permission:error.permission})); process.exit(0); };
process.once('uncaughtException', finish);
const server = net.createServer(); server.once('error', finish);
try { server.listen(process.env.HOME+'/s', () => { process.stdout.write('ALLOWED'); server.close(); }); }
catch (error) { finish(error); }
""")
    try:
        prepared = prepare_cordis_process(node, script, root, root / 'state')
    except CordisSecurityError as exc:
        pytest.skip(str(exc))
    result = subprocess.run(prepared.command, env=prepared.env, cwd=prepared.cwd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['code'] in {'EPERM', 'EACCES', 'ERR_ACCESS_DENIED'}
    assert not any(flag.startswith('--allow-net') for flag in prepared.command)
