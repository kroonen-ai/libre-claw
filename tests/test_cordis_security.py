# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import select
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from libre_claw.core import cordis_security
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process


_NODE_HELP = """
--permission
--allow-fs-read
--allow-fs-write
--no-experimental-sqlite
--report-exclude-env
--report-exclude-network
"""


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "bridge" / "runtime.cjs"
    runtime.parent.mkdir()
    runtime.write_text("process.stdout.write('ready');", encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    return runtime, plugin, tmp_path / "state"


def _mock_node(monkeypatch, *, version="v26.10.0", help_text=_NODE_HELP + "--allow-net"):
    calls = []
    monkeypatch.setattr(cordis_security, "_node_path", lambda _: "/trusted/node")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--version" in command:
            output = version
        elif "--help" in command:
            output = help_text
        else:
            output = "cordis-network-denied"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(cordis_security.subprocess, "run", run)
    return calls


def test_process_uses_explicit_grants_and_no_inherited_environment(tmp_path, monkeypatch):
    runtime, plugin, state = _paths(tmp_path)
    read = tmp_path / "reference.txt"
    write = tmp_path / "exports"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-plugin")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/private/preload.js")
    monkeypatch.setenv("HTTP_PROXY", "http://private-proxy.invalid")
    calls = _mock_node(monkeypatch)

    result = prepare_cordis_process("node", runtime, plugin, state, read_paths=(read,), write_paths=(write,))

    assert result.command[0] == "/trusted/node"
    assert result.command[-1] == str(runtime.resolve())
    assert "--permission" in result.command
    assert "--no-experimental-sqlite" in result.command
    assert "--max-old-space-size=256" in result.command
    assert "--allow-net" not in result.command
    assert not set(result.command) & {"--allow-child-process", "--allow-worker", "--allow-addons", "--allow-wasi"}
    assert {part for part in result.command if part.startswith("--allow-fs-read=")} == {
        f"--allow-fs-read={path.resolve()}" for path in (runtime.parent, plugin, state, read)
    }
    assert {part for part in result.command if part.startswith("--allow-fs-write=")} == {
        f"--allow-fs-write={path.resolve()}" for path in (state, write)
    }
    assert result.cwd == state.resolve()
    assert result.env["HOME"] == str(state.resolve())
    assert result.env["TMPDIR"] == str(state.resolve() / "tmp")
    assert not set(result.env) & {"DEEPSEEK_API_KEY", "NODE_OPTIONS", "HTTP_PROXY", "SSH_AUTH_SOCK"}
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert result.isolation == "node-permissions-network-denied"
    for _, kwargs in calls:
        assert kwargs["env"] == result.env
        assert kwargs["timeout"] == 5
        assert kwargs["close_fds"] is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert "shell" not in kwargs


@pytest.mark.parametrize("version,help_text", [
    ("v20.20.0", _NODE_HELP),
    ("not-a-version", _NODE_HELP),
    ("v22.22.3", "--experimental-permission"),
    ("v22.22.3", "--permission"),
])
def test_incompatible_runtime_fails_closed(tmp_path, monkeypatch, version, help_text):
    _mock_node(monkeypatch, version=version, help_text=help_text)
    with pytest.raises(CordisSecurityError):
        prepare_cordis_process("node", *_paths(tmp_path))


def test_offline_node22_on_unsupported_platform_fails_closed(tmp_path, monkeypatch):
    _mock_node(monkeypatch, version="v22.22.3", help_text=_NODE_HELP)
    monkeypatch.setattr(cordis_security.sys, "platform", "linux")
    with pytest.raises(CordisSecurityError, match="do not restrict network"):
        prepare_cordis_process("node", *_paths(tmp_path))


@pytest.mark.parametrize("version,help_text,network_flag", [
    ("v22.22.3", _NODE_HELP, False),
    ("v26.10.0", _NODE_HELP + "--allow-net", True),
])
def test_explicit_network_grant(tmp_path, monkeypatch, version, help_text, network_flag):
    _mock_node(monkeypatch, version=version, help_text=help_text)
    monkeypatch.setattr(cordis_security.sys, "platform", "linux")
    result = prepare_cordis_process("node", *_paths(tmp_path), allow_network=True)
    assert ("--allow-net" in result.command) is network_flag
    assert result.isolation == "node-permissions-network-allowed"


def test_wildcard_paths_cannot_broaden_permissions(tmp_path, monkeypatch):
    _mock_node(monkeypatch)
    with pytest.raises(CordisSecurityError, match="wildcards"):
        prepare_cordis_process("node", *_paths(tmp_path), read_paths=(tmp_path / "*.txt",))


def test_private_temporary_directory_cannot_redirect_outside_state(tmp_path, monkeypatch):
    _mock_node(monkeypatch)
    runtime, plugin, state = _paths(tmp_path)
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    (state / "tmp").symlink_to(outside, target_is_directory=True)
    previous_mode = stat.S_IMODE(outside.stat().st_mode)
    with pytest.raises(CordisSecurityError, match="symbolic link"):
        prepare_cordis_process("node", runtime, plugin, state)
    assert stat.S_IMODE(outside.stat().st_mode) == previous_mode


def test_state_directory_cannot_change_filesystem_root_permissions(tmp_path, monkeypatch):
    _mock_node(monkeypatch)
    runtime, plugin, _ = _paths(tmp_path)
    with pytest.raises(CordisSecurityError, match="filesystem root"):
        prepare_cordis_process("node", runtime, plugin, Path(tmp_path.anchor))


def test_new_node_without_expected_network_flags_fails_closed(tmp_path, monkeypatch):
    _mock_node(monkeypatch, version="v26.10.0", help_text=_NODE_HELP)
    with pytest.raises(CordisSecurityError, match="network permission controls"):
        prepare_cordis_process("node", *_paths(tmp_path))


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_probe_failure_is_bounded_and_does_not_echo_stderr(tmp_path, monkeypatch, failure):
    _mock_node(monkeypatch)

    def fail(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 5, stderr="private-value")
        return subprocess.CompletedProcess(command, 1, "", "private-value")

    monkeypatch.setattr(cordis_security.subprocess, "run", fail)
    with pytest.raises(CordisSecurityError) as caught:
        prepare_cordis_process("node", *_paths(tmp_path))
    assert "private-value" not in str(caught.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec integration")
def test_macos_sandbox_failure_never_falls_back_to_unrestricted(tmp_path, monkeypatch):
    calls = _mock_node(monkeypatch, version="v22.22.3", help_text=_NODE_HELP)
    original = cordis_security.subprocess.run

    def run(command, **kwargs):
        if command[0] == "/usr/bin/sandbox-exec":
            return subprocess.CompletedProcess(command, 0, "network-was-allowed", "")
        return original(command, **kwargs)

    monkeypatch.setattr(cordis_security.subprocess, "run", run)
    with pytest.raises(CordisSecurityError, match="did not enforce"):
        prepare_cordis_process("node", *_paths(tmp_path))
    assert len(calls) == 2


def test_local_node_enforces_process_grants(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    runtime, plugin, state = _paths(tmp_path)
    secret = tmp_path / "outside-grants.txt"
    secret.write_text("private-file-value", encoding="utf-8")
    source = r"""
const fs = require('node:fs');
const child = require('node:child_process');
const net = require('node:net');
const result = { environmentSecret: process.env.CORDIS_PRIVATE_TEST ?? null,
  nodeOptions: process.env.NODE_OPTIONS ?? null };
try { fs.readFileSync(SECRET_PATH); result.read = 'allowed'; }
catch (error) { result.read = error.code; }
try { fs.writeFileSync(SECRET_PATH, 'overwritten'); result.write = 'allowed'; }
catch (error) { result.write = error.code; }
try { child.execFileSync(process.execPath, ['--version']); result.child = 'allowed'; }
catch (error) { result.child = error.code; }
try { require('node:sqlite'); result.sqlite = 'allowed'; }
catch (error) { result.sqlite = error.code; }
fs.writeFileSync('state.json', JSON.stringify({ saved: true }));
result.state = JSON.parse(fs.readFileSync('state.json', 'utf8'));
const server = net.createServer();
server.once('error', error => { result.network = error.code; finish(); });
function finish() { process.stdout.write(JSON.stringify(result)); }
// Node 26 can throw its Net permission denial from an asynchronous bind,
// outside both listen's try/catch and the server's error event.
process.once('uncaughtException', error => {
  if (error.code !== 'ERR_ACCESS_DENIED' || error.permission !== 'Net') throw error;
  result.network = error.code; finish();
});
try { server.listen(0, '127.0.0.1', () => {
  result.network = 'allowed'; server.close(finish);
}); } catch (error) { result.network = error.code; finish(); }
"""
    runtime.write_text(source.replace("SECRET_PATH", json.dumps(str(secret))), encoding="utf-8")
    monkeypatch.setenv("CORDIS_PRIVATE_TEST", "must-not-reach-plugin")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/must-not-run.js")
    try:
        process = prepare_cordis_process(node, runtime, plugin, state)
    except CordisSecurityError as exc:
        if "requires" in str(exc) or "does not support" in str(exc):
            pytest.skip(f"Local Node lacks required offline support: {exc}")
        raise

    completed = subprocess.run(process.command, env=process.env, cwd=process.cwd,
                               capture_output=True, text=True, timeout=5, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["environmentSecret"] is None
    assert result["nodeOptions"] is None
    assert result["read"] == "ERR_ACCESS_DENIED"
    assert result["write"] == "ERR_ACCESS_DENIED"
    assert result["child"] == "ERR_ACCESS_DENIED"
    assert result["sqlite"] == "ERR_UNKNOWN_BUILTIN_MODULE"
    assert result["network"] in {"ERR_ACCESS_DENIED", "EPERM", "EACCES"}
    assert result["state"] == {"saved": True}
    assert secret.read_text() == "private-file-value"
    assert json.loads((state / "state.json").read_text()) == {"saved": True}


@pytest.mark.parametrize("operation", ["fetch", "http", "tcp", "udp", "dns"])
def test_offline_process_denies_outbound_network(tmp_path, operation):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    runtime, plugin, state = _paths(tmp_path)
    socket_type = socket.SOCK_DGRAM if operation in {"udp", "dns"} else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, socket_type) as sink:
        sink.bind(("127.0.0.1", 0))
        sink.settimeout(2)
        if socket_type == socket.SOCK_STREAM:
            sink.listen(1)
        address = sink.getsockname()

        source = r"""
const operation = OPERATION;
const port = PORT;
function finish(error) {
  const codes = [];
  function collect(value) {
    if (!value) return;
    if (value.code) codes.push(value.code);
    collect(value.cause);
    for (const nested of value.errors ?? []) collect(nested);
  }
  collect(error);
  process.stdout.write(JSON.stringify({ codes }));
  process.exit(0);
}
// Some Node versions throw permission denials asynchronously instead of
// delivering an error event. The assertion below still requires denial.
process.once('uncaughtException', finish);
setTimeout(() => finish({ code: 'TEST_TIMEOUT' }), 3000);
try {
  if (operation === 'fetch') {
    fetch(`http://127.0.0.1:${port}/telemetry`).then(() => finish(), finish);
  } else if (operation === 'http') {
    require('node:http').get(`http://127.0.0.1:${port}/telemetry`, () => finish())
      .once('error', finish);
  } else if (operation === 'tcp') {
    require('node:net').connect({ host: '127.0.0.1', port }, () => finish())
      .once('error', finish);
  } else if (operation === 'udp') {
    const client = require('node:dgram').createSocket('udp4');
    client.once('error', finish);
    client.send('telemetry-probe', port, '127.0.0.1', finish);
  } else if (operation === 'dns') {
    const resolver = new (require('node:dns').Resolver)({ timeout: 500, tries: 1 });
    resolver.setServers([`127.0.0.1:${port}`]);
    resolver.resolve4('cordis-egress.test', finish);
  }
} catch (error) { finish(error); }
"""
        runtime.write_text(source.replace("OPERATION", json.dumps(operation))
                           .replace("PORT", str(address[1])), encoding="utf-8")
        try:
            process = prepare_cordis_process(node, runtime, plugin, state)
        except CordisSecurityError as exc:
            if "requires" in str(exc) or "does not support" in str(exc):
                pytest.skip(f"Local Node lacks required offline support: {exc}")
            raise

        # Run the identical probe unrestricted first, and answer its request.
        # Every sink must be reachable; DNS also must return a valid answer.
        # All traffic is loopback, including the explicitly configured resolver.
        with subprocess.Popen((node, str(runtime)), env=process.env, cwd=process.cwd,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as control:
            try:
                if socket_type == socket.SOCK_STREAM:
                    accepted, _ = sink.accept()
                    with accepted:
                        accepted.settimeout(2)
                        if operation in {"fetch", "http"}:
                            assert b"GET /telemetry " in accepted.recv(8192)
                            accepted.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n"
                                             b"Connection: close\r\n\r\n")
                else:
                    packet, sender = sink.recvfrom(4096)
                    if operation == "dns":
                        question = b"\x0dcordis-egress\x04test\x00\x00\x01\x00\x01"
                        assert packet[12:].startswith(question)
                        # A single A record for 127.0.0.1, with a compressed name.
                        response = (packet[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
                                    + question + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x00"
                                    b"\x00\x04\x7f\x00\x00\x01")
                        sink.sendto(response, sender)
                    else:
                        assert packet == b"telemetry-probe"
                stdout, stderr = control.communicate(timeout=5)
                assert control.returncode == 0, stderr
                assert json.loads(stdout) == {"codes": []}
            finally:
                if control.poll() is None:
                    control.kill()
                    control.communicate(timeout=5)

        completed = subprocess.run(process.command, env=process.env, cwd=process.cwd,
                                   capture_output=True, text=True, timeout=5, check=False)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert not select.select([sink], [], [], 0.05)[0], f"{operation} reached the local sink"
        denial_codes = {"ERR_ACCESS_DENIED", "EPERM", "EACCES"}
        if operation == "dns" and process.isolation == "node-permissions-macos-network-denied":
            # c-ares translates sandbox-exec's denial into ECONNREFUSED. The
            # successful identical lookup and empty sink above distinguish it
            # from a missing DNS server or an ordinary resolution failure.
            denial_codes.add("ECONNREFUSED")
        assert set(result["codes"]) & denial_codes, result
