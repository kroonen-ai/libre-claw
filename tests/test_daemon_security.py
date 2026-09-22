# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer
from libre_claw.web.request_security import control_api_middleware


@pytest.fixture
async def daemon_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LIBRE_CLAW_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for arguments in (
        ["init", "-b", "main"],
        ["config", "user.name", "Security Test"],
        ["config", "user.email", "test@example.invalid"],
        ["commit", "--allow-empty", "-m", "Create fixture"],
    ):
        subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)
    config = load_config(working_directory=repo)
    config = replace(
        config,
        automations=replace(config.automations, enabled=False),
        petdex=replace(config.petdex, enabled=False),
    )
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"), start_telegram_bridge=False)
    async with TestClient(TestServer(server.app())) as client:
        yield client, server


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.invalid"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:1"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Host": "rebound.attacker.invalid"},
    {"Host": "127.0.0.1.attacker.invalid"},
    {"Host": "attacker.invalid", "X-Forwarded-Host": "127.0.0.1"},
])
async def test_foreign_requests_cannot_create_worktrees(daemon_client, headers):
    client, server = daemon_client
    response = await client.post("/worktrees", json={}, headers=headers)
    assert response.status == 403
    assert await server.workflows.worktrees.list() == []
    assert await server.run_store.list_runs() == []


async def test_setup_rejects_cross_site_self_approval_and_preserves_approved_shell(daemon_client):
    client, _ = daemon_client
    response = await client.post("/worktrees", json={})
    assert response.status == 200, await response.text()
    worktree = (await response.json())["worktree"]
    path = Path(worktree["path"])
    endpoint = f"/worktrees/{worktree['worktree_id']}/setup"
    payload = {"commands": ["printf hacked > forbidden.txt"], "approved": True}

    # text/plain is a CORS-safelisted request; CORS response headers alone do
    # not stop its side effects. The Origin check must run before setup.
    attack = await client.post(endpoint, data=json.dumps(payload), headers={"Origin": "https://attacker.invalid"})
    assert attack.status == 403
    assert not (path / "forbidden.txt").exists()

    rebound = await client.post(endpoint, json=payload, headers={"Host": "rebound.attacker.invalid"})
    assert rebound.status == 403
    assert not (path / "forbidden.txt").exists()

    wrong_type = await client.post(endpoint, data=json.dumps(payload))
    assert wrong_type.status == 415
    assert not (path / "forbidden.txt").exists()

    denied = await client.post(endpoint, json={"commands": payload["commands"]})
    assert denied.status == 400
    assert not (path / "forbidden.txt").exists()

    approved = await client.post(
        endpoint,
        json={"commands": ["printf 'hello world\\n' | tr 'a-z' 'A-Z' > setup.txt"], "approved": True},
        headers={"Origin": str(client.make_url("/")).rstrip("/"), "Sec-Fetch-Site": "same-origin"},
    )
    assert approved.status == 200, await approved.text()
    assert not (await approved.json())["results"][0]["is_error"]
    assert (path / "setup.txt").read_text() == "HELLO WORLD\n"


async def test_dns_rebinding_cannot_read_worktree_ids_or_dashboard(daemon_client):
    client, _ = daemon_client
    for endpoint in ("/worktrees", "/runs", "/dashboard", "/health"):
        response = await client.get(endpoint, headers={"Host": "rebound.attacker.invalid"})
        assert response.status == 403


async def test_dashboard_links_work_but_foreign_frames_and_api_fetches_do_not(daemon_client):
    client, _ = daemon_client
    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    response = await client.get("/dashboard", headers=navigation)
    assert response.status == 200
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.headers["X-Frame-Options"] == "DENY"
    frame = await client.get("/dashboard", headers={**navigation, "Sec-Fetch-Dest": "iframe"})
    assert frame.status == 403
    data = await client.get("/worktrees", headers=navigation)
    assert data.status == 403


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
async def test_mutations_reject_browser_safelisted_body_types(daemon_client, content_type):
    client, server = daemon_client
    response = await client.post("/worktrees", data="{}", headers={"Content-Type": content_type})
    assert response.status == 415
    assert await server.workflows.worktrees.list() == []


async def test_native_clients_can_read_and_send_bodyless_commands(daemon_client):
    client, _ = daemon_client
    health = await client.get("/health")
    assert health.status == 200
    shutdown = await client.post("/shutdown")
    assert shutdown.status == 200


@pytest.mark.parametrize("bind_host,socket_host,host,origin", [
    ("0.0.0.0", "192.168.1.20", "192.168.1.20:8766", "http://192.168.1.20:8766"),
    ("192.168.1.20", "192.168.1.20", "192.168.1.20:8766", None),
    ("claw.internal", "192.168.1.20", "claw.internal:8766", "http://claw.internal:8766"),
    ("::", "fd00::1", "[fd00::1]:8766", "http://[fd00::1]:8766"),
    ("::", "::ffff:192.168.1.20", "192.168.1.20:8766", "http://192.168.1.20:8766"),
    ("::1", "::1", "[::1]:8766", "http://[0:0:0:0:0:0:0:1]:8766"),
    ("127.0.0.1", "127.0.0.1", "localhost:8766", "http://localhost:8766"),
])
async def test_explicit_lan_and_ipv6_clients_keep_working(bind_host, socket_host, host, origin):
    headers = CIMultiDict({"Host": host})
    if origin:
        headers["Origin"] = origin
    request = SimpleNamespace(
        headers=headers, scheme="http", method="POST", path="/worktrees/test/setup", can_read_body=True,
        content_type="application/json", transport=SimpleNamespace(get_extra_info=lambda _: (socket_host, 8766)),
    )

    async def handler(_request):
        return web.json_response({"accepted": True})

    response = await control_api_middleware(bind_host)(request, handler)
    assert response.status == 200


@pytest.mark.parametrize("headers", [
    [("Host", "localhost:8766"), ("Host", "attacker.invalid")],
    [("Host", "localhost:8766"), ("Origin", "http://localhost:8766"), ("Origin", "https://attacker.invalid")],
    [("Host", "localhost:8766"), ("Origin", "http://localhost:8766/path")],
    [("Host", "localhost:8766"), ("Origin", "http://user@localhost:8766")],
    [("Host", "localhost:8766"), ("Origin", "http://localhost:not-a-port")],
    [("Host", "localhost:8766"), ("Origin", "http://localhost:8766\\attacker.invalid")],
    [("Host", "localhost:8766"), ("Origin", "https://localhost:8766")],
    [("Host", "localhost:8766"), ("Origin", "http://127.0.0.1:8766")],
    [("Host", "localhost:0"), ("Origin", "http://localhost")],
    [("Host", "localhost"), ("Origin", "http://localhost:0")],
    [("Host", "127.0.0.1:8766"), ("Origin", "http://[::ffff:127.0.0.1]:8766")],
    [("Host", "attacker.invalid:8766"), ("Origin", "http://attacker.invalid:8766")],
])
async def test_ambiguous_or_foreign_authorities_fail_before_handler(headers):
    request = SimpleNamespace(
        headers=CIMultiDict(headers), scheme="http", method="POST", path="/worktrees/test/setup", can_read_body=True,
        content_type="application/json", transport=SimpleNamespace(get_extra_info=lambda _: ("127.0.0.1", 8766)),
    )

    async def handler(_request):
        pytest.fail("Untrusted requests must never reach shell approval or execution.")

    response = await control_api_middleware("0.0.0.0")(request, handler)
    assert response.status == 403
