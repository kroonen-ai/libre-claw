# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import shutil
import socket
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from libre_claw.config import ConfigError, load_config
from libre_claw.core.cordis_lsp import CordisLspPool, _LspChannel, _stop_process, normalize_lsp_servers
from libre_claw.core.cordis import CordisManager
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolContext
from test_cordis_harness_integration import RUNTIME, install, runtime_supported


SERVER = r'''
import json, os, pathlib, socket, sys, time
root = None
document = None
def send(value):
    data = json.dumps(value).encode()
    sys.stdout.buffer.write(('Content-Length: %d\r\n\r\n' % len(data)).encode() + data)
    sys.stdout.buffer.flush()
while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line == b'\r\n':
            break
        key, value = line.split(b':', 1)
        headers[key.lower()] = value.strip()
    message = json.loads(sys.stdin.buffer.read(int(headers[b'content-length'])))
    method, params = message.get('method'), message.get('params')
    if method == 'initialize':
        root = params['rootUri']
        result = {'capabilities': {'positionEncoding': 'utf-16'}}
    elif method == 'textDocument/didOpen':
        document = params['textDocument']
        continue
    elif method == 'textDocument/hover':
        if document['text'].startswith('WAIT'):
            time.sleep(120)
        network = 'not-probed'
        if len(sys.argv) == 3:
            try:
                with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1) as connection:
                    connection.sendall(b'confined-language-server')
                network = 'allowed'
            except OSError:
                network = 'denied'
        try:
            pathlib.Path('forbidden.txt').write_text('must not write')
            writes = 'allowed'
        except (PermissionError, OSError):
            writes = 'denied'
        result = {'contents': {'kind': 'plaintext', 'value':
            'source=' + document['text'] + ';env=' + str(os.getenv('LSP_PRIVATE_TEST')) + ';network=' + network + ';writes=' + writes}}
    elif method in ('textDocument/definition', 'textDocument/references', 'textDocument/implementation'):
        if method == 'textDocument/references':
            assert params['context']['includeDeclaration'] is True
        result = [{'uri': document['uri'], 'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 3}}}]
    elif method == 'shutdown':
        result = None
    elif method == 'exit':
        sys.exit(0)
    else:
        continue
    send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
'''


@pytest.fixture
def confined_lsp():
    if not (sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()
            or sys.platform == "linux" and shutil.which("bwrap")):
        pytest.skip("No supported OS-confined language-server launcher is installed")


def lsp_setup(tmp_path, *, network_endpoint=None):
    root = tmp_path / "workspace"
    root.mkdir()
    script = root / "server.py"
    script.write_text(SERVER)
    (root / "example.test").write_text("abc value")
    configured = {"fixture": {"command": [sys.executable, "-I", "-S", str(script)],
                              "extensions": {".test": "fixture"},
                              "runtime_read_paths": [sys.base_prefix], "timeout_seconds": 10}}
    if network_endpoint is not None:
        configured["fixture"]["command"].extend(map(str, network_endpoint))
    allowed = [True]
    effects = []

    def authorize():
        if not allowed[0]:
            raise PermissionError("The grant was revoked")
        return {"read_paths": [str(root)], "write_paths": [], "allow_network": True}

    async def effect(name, arguments, operation, *, read_only):
        effects.append((name, arguments, read_only))
        return await operation()

    pool = CordisLspPool(root, authorize, effect, configured)
    request = {"provider": "fixture", "request": {"operation": "hover", "filePath": "example.test",
               "position": {"line": 0, "character": 1}, "workspaceRoot": str(root), "languageId": "fixture"}}
    return pool, request, allowed, effects


@pytest.mark.parametrize("change", [
    {"command": "pylsp"}, {"command": []}, {"env": {"PRIVATE": "secret"}},
    {"extensions": {"py": "python"}}, {"runtime_read_paths": ["/"]},
    {"runtime_read_paths": ["relative"]}, {"timeout_seconds": True},
])
def test_lsp_server_configuration_is_explicit_and_bounded(change):
    with pytest.raises(ValueError):
        normalize_lsp_servers({"test": {"command": ["/server"], "extensions": {".py": "python"}, **change}})


def test_lsp_config_is_loaded_and_rejects_shell_strings(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[cordis.lsp_servers.python]\ncommand=["/server", "--stdio"]\nextensions={".py"="python"}\n')
    config = load_config(config_path=config_file, working_directory=tmp_path)
    assert config.cordis.lsp_servers["python"]["command"] == ["/server", "--stdio"]
    config_file.write_text('[cordis.lsp_servers.python]\ncommand="server --stdio"\nextensions={".py"="python"}\n')
    with pytest.raises(ConfigError, match="argv"):
        load_config(config_path=config_file, working_directory=tmp_path)


def test_lsp_document_read_rejects_replaced_parent_directory(tmp_path):
    pool, _, _, _ = lsp_setup(tmp_path)
    directory = pool.workspace / "source"
    directory.mkdir()
    (directory / "example.test").write_text("approved source")
    approved = pool._path("source/example.test")
    private = tmp_path / "private"
    private.mkdir()
    (private / "example.test").write_text("ungranted data")
    directory.rename(pool.workspace / "old-source")
    directory.symlink_to(private, target_is_directory=True)
    with pytest.raises(OSError):
        pool._read_document(approved)


async def test_actual_stdio_lsp_hover_is_offline_read_only_and_has_no_inherited_credentials(tmp_path, monkeypatch, confined_lsp):
    monkeypatch.setenv("LSP_PRIVATE_TEST", "NEVER-INHERIT")
    with socket.socket() as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.listen()
        receiver.settimeout(1)
        endpoint = receiver.getsockname()
        # Prove the host endpoint accepts a connection and payload before testing
        # confinement. A socket bind inside Linux's private namespace is harmless.
        with socket.create_connection(endpoint, timeout=1) as control:
            control.sendall(b"positive-control")
        connection, _ = receiver.accept()
        with connection:
            control_payload = connection.recv(1024)
        assert control_payload == b"positive-control"

        pool, request, _, effects = lsp_setup(tmp_path, network_endpoint=endpoint)
        try:
            result = await pool.dispatch("query", request)
            assert result == {"kind": "hover", "hover": {"contents": "source=abc value;env=None;network=denied;writes=denied"}}
            receiver.settimeout(0.1)
            try:
                unexpected, _ = await asyncio.to_thread(receiver.accept)
            except TimeoutError:
                unexpected = None
            if unexpected is not None:
                unexpected.close()
            assert unexpected is None, "The confined language server reached the host receiver"
            assert not (pool.workspace / "forbidden.txt").exists()
            assert effects[0][0] == "lsp" and effects[0][2] is True
            assert effects[0][1]["command"][0] == sys.executable
            assert not pool._tasks
        finally:
            await pool.aclose()


@pytest.mark.parametrize("operation", ["goToDefinition", "findReferences", "goToImplementation"])
async def test_actual_stdio_lsp_navigation_preserves_utf16_ranges_and_workspace(operation, tmp_path, confined_lsp):
    pool, request, _, _ = lsp_setup(tmp_path)
    request["request"]["operation"] = operation
    try:
        result = await pool.dispatch("query", request)
        assert result["kind"] == "locations"
        assert result["resolvedWorkspaceUri"] == pool.workspace.as_uri()
        assert result["locations"][0]["uri"] == (pool.workspace / "example.test").as_uri()
        assert result["locations"][0]["range"]["end"] == {"line": 0, "character": 3}
    finally:
        await pool.aclose()


@pytest.mark.parametrize("ending", ["cancel", "revoke", "close", "startup"])
async def test_lsp_cancellation_revocation_and_shutdown_join_owned_process(tmp_path, monkeypatch, ending, confined_lsp):
    pool, request, allowed, _ = lsp_setup(tmp_path)
    (pool.workspace / "example.test").write_text("WAIT")
    original_request, original_spawn = _LspChannel.request, asyncio.create_subprocess_exec
    entered = asyncio.Event()
    release = asyncio.Event()
    processes = []

    async def tracked_request(channel, method, params):
        if method == "textDocument/hover":
            entered.set()
        return await original_request(channel, method, params)

    async def tracked_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        if ending == "startup":
            entered.set()
            await release.wait()
        return process

    monkeypatch.setattr(_LspChannel, "request", tracked_request)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)
    task = asyncio.create_task(pool.dispatch("query", request))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if ending in {"cancel", "startup"}:
            task.cancel()
            release.set()
        elif ending == "revoke":
            allowed[0] = False
        else:
            await pool.aclose()
        with pytest.raises(PermissionError if ending == "revoke" else asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert len(processes) == 1 and processes[0].returncode is not None
        process = processes[0]
        assert process.stdout.at_eof() and process.stderr.at_eof()
        assert process.stdin.is_closing() and process._transport.is_closing()
        await process.stdin.wait_closed()
        assert not pool._tasks
    finally:
        release.set()
        await pool.aclose()
        await asyncio.gather(task, return_exceptions=True)


async def test_lsp_shutdown_drains_paused_pipes_before_returning():
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-c",
        "import os,time; os.write(1,b'x'*(3*1024*1024)); time.sleep(30)",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=1024,
    )
    try:
        async with asyncio.timeout(3):
            while not process.stdout._paused:
                await asyncio.sleep(0.01)
            await _stop_process(process)
        assert process.returncode is not None
        assert process.stdout.at_eof() and process.stderr.at_eof()
        assert process.stdin.is_closing() and process._transport.is_closing()
        await process.stdin.wait_closed()
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


async def test_lsp_rejects_ungranted_paths_foreign_routes_and_injected_commands_before_approval(tmp_path):
    pool, request, _, effects = lsp_setup(tmp_path)
    cases = [
        {**request, "command": ["/bin/sh"]},
        {**request, "provider": "unknown"},
        {**request, "request": {**request["request"], "filePath": "../outside.test"}},
        {**request, "request": {**request["request"], "workspaceRoot": str(tmp_path)}},
        {**request, "request": {**request["request"], "languageId": "other"}},
    ]
    for case in cases:
        with pytest.raises((ValueError, PermissionError)):
            await pool.dispatch("query", case)
    assert effects == []
    await pool.aclose()


async def test_unchanged_harness_lsp_tool_uses_configured_stdio_provider_and_host_approval(tmp_path, runtime_supported, confined_lsp):
    configured, _, _, _ = lsp_setup(tmp_path)
    root = configured.workspace
    base = load_config(working_directory=root)
    config = replace(base, cordis=replace(base.cordis, lsp_servers=configured.servers))
    manager = CordisManager(tmp_path / "registry", config=config, persistent=True)
    fixture = RUNTIME / "compat/tests/fixtures/harness-lsp.mjs"
    plugin_id = install(manager, tmp_path / "package", {"lsp.mjs": fixture.read_bytes()},
                        [{"id": "lsp", "name": "./lsp.mjs"}])
    context = ToolContext(root, shared_state={"agent_session": Session()})
    approved = []

    async def execute(tool, arguments):
        approved.append((tool.name, arguments))
        return await tool.invoke(arguments)

    context.shared_state["harness_tool_executor"] = execute
    try:
        active = await manager.enable_async(plugin_id, root, read_paths=[str(root)])
        assert "lsp" in active["tools"]
        tool = next(tool for tool in manager.create_tools(context) if tool.tool_name == "lsp")
        result = await tool.execute(operation="goToDefinition", file_path="example.test", line=1, character=2)
        assert not result.is_error, result.error or result.content
        assert "example.test:1:1" in result.content
        assert approved[0][0] == "lsp"
        assert approved[0][1]["position"] == {"line": 0, "character": 1}
        assert approved[0][1]["command"] == configured.servers["fixture"]["command"]
        assert context.shared_state["harness_host_services"][plugin_id].lsp.providers() == [
            {"id": "fixture", "extensionToLanguage": {".test": "fixture"}},
        ]
    finally:
        await manager.aclose()
        for service in context.shared_state.get("harness_host_services", {}).values():
            await service.aclose()
        await configured.aclose()
