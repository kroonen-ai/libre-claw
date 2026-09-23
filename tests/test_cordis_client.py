# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from libre_claw.config import load_config
from libre_claw.core.cordis import CordisError, CordisManager, MANIFEST_NAME
from libre_claw.core.cordis_client import CordisClientError, CordisClientPool, _public_client_config, client_spec, validate_event, validate_snapshot
from libre_claw.core.cordis_client_manifest import validate_client_declaration
from libre_claw.core.cordis_harness import adapt_harness_package
from libre_claw.daemon import DaemonServer


CLIENT_SOURCE = """
window.__ModuleLoader__.load({id:'client-test',factory(require){
  const React=require('react');
  return {inject:['slots'],apply(ctx,config){
    ctx.slots.register({name:'shell.overlay',id:'counter'},function Counter(){
      const [count,setCount]=React.useState(0);
      return React.createElement('button',{onClick:()=>setCount(count+1)},
        'Count '+count+' '+config.message+' '+String(config.api_key)+' '+String(process.env.CLIENT_PRIVATE_TEST));
    });
  }};
}});
"""


def install_client(tmp_path, *, source=CLIENT_SOURCE, extra_files=None):
    package = tmp_path / "package"
    package.mkdir()
    (package / MANIFEST_NAME).write_text(json.dumps({
        "id": "client-test", "name": "Client test", "version": "1.0.0", "entry": "backend.mjs", "tools": [],
        "client": {"entry": "client.mjs", "package_name": "client-test"},
        "config": {"message": "hello", "api_key": "PRIVATE-CONFIG-VALUE"},
    }))
    (package / "backend.mjs").write_text("export default {apply(){}};")
    (package / "client.mjs").write_text(source)
    for name, content in (extra_files or {}).items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    manager = CordisManager(tmp_path / "registry")
    manager.install(package)
    return manager


def snapshot():
    return {"revision": 1, "status": "active", "views": [{
        "id": "view", "slot": "shell.overlay", "mode": "panel", "tree": [{
            "id": "button", "tag": "button", "props": {}, "events": {"click": "click"}, "children": [
                {"id": "text", "tag": "#text", "text": "Safe text", "props": {}, "events": {}, "children": []},
            ],
        }],
    }]}


def text_of(value):
    pending = [node for view in value["views"] for node in view["tree"]]
    result = []
    while pending:
        node = pending.pop()
        result.append(node.get("text", ""))
        pending.extend(node["children"])
    return " ".join(result)


def test_public_client_config_redacts_marked_fields_url_credentials_and_known_tokens():
    source = {"connection_url": "https://alice:short@service.example/path?access_token=x&view=public",
              "nested": [{"refreshToken": "another-secret", "note": "Bearer abcdefghijklmnop"}],
              "message": "Visible label", "token_budget": 4096}
    public = _public_client_config(source)
    encoded = json.dumps(public)
    assert all(value not in encoded for value in ("alice", "short", "access_token=x", "another-secret", "abcdefghijklmnop"))
    assert "view=public" in public["connection_url"]
    assert public["message"] == "Visible label" and public["token_budget"] == 4096


def test_client_only_package_import_is_nonexecuting_and_retains_original_source():
    package = {"name": "client-test", "version": "1.0.0", "type": "module",
               "exports": {"./client": "./client.mjs"}, "dsh": {"client": {"platform": "web"}}}
    files = {"package.json": json.dumps(package).encode(), "client.mjs": b"throw new Error('not during installation');"}
    result = adapt_harness_package(files)
    manifest = json.loads(result[MANIFEST_NAME])
    assert manifest["client"] == {"entry": "client.mjs", "package_name": "client-test"}
    assert manifest["harness"]["components"] == []
    assert result["client.mjs"] == files["client.mjs"]
    with pytest.raises(ValueError):
        validate_client_declaration({"entry": "../outside.mjs", "package_name": "client-test"})


@pytest.mark.parametrize("change", ["script", "handler", "url", "duplicate", "style", "depth"])
def test_parent_rejects_active_or_unbounded_render_data(change):
    value = snapshot()
    node = value["views"][0]["tree"][0]
    if change == "script":
        node["tag"] = "script"
    elif change == "handler":
        node["props"]["onclick"] = "alert(1)"
    elif change == "url":
        node["props"]["src"] = "https://example.invalid/private"
    elif change == "duplicate":
        node["children"][0]["id"] = node["id"]
    elif change == "style":
        node["props"]["style"] = {"backgroundColor": "url(https://example.invalid)"}
    else:
        for index in range(35):
            child = {"id": f"nested-{index}", "tag": "div", "props": {}, "events": {}, "children": []}
            node["children"] = [child]
            node = child
    with pytest.raises(CordisClientError):
        validate_snapshot(value)


def test_client_events_are_scoped_to_displayed_revision_and_form_fields():
    value = validate_snapshot(snapshot())
    assert validate_event({"revision": 1, "event_id": "click"}, value)
    assert validate_event({"revision": 1, "event_id": "click", "target_id": "text"}, value)
    for event in [
        {"revision": 0, "event_id": "click"},
        {"revision": 1, "event_id": "outside"},
        {"revision": 1, "event_id": "click", "target_id": "outside"},
        {"revision": 1, "event_id": "click", "values": {"outside": {"value": "secret"}}},
    ]:
        with pytest.raises(CordisClientError):
            validate_event(event, value)


async def test_real_offline_react_guest_updates_state_and_rechecks_grants(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_PRIVATE_TEST", "PRIVATE-ENV-VALUE")
    manager = install_client(tmp_path)
    pool = CordisClientPool(manager)
    with pytest.raises(CordisClientError, match="explicit"):
        await pool.open("client-test", tmp_path)
    manager.enable("client-test", tmp_path)
    with pytest.raises(CordisClientError, match="explicit"):
        await pool.open("client-test", tmp_path)
    manager.enable("client-test", tmp_path, allow_client=True)
    assert "api_key" not in client_spec(manager, "client-test", tmp_path)["config"]
    try:
        opened = await pool.open("client-test", tmp_path)
        token = opened["ui_session_id"]
        assert "Count 0 hello undefined undefined" in text_of(opened["snapshot"])
        node = opened["snapshot"]["views"][0]["tree"][0]
        clicked = await pool.request("client-test", token, {
            "revision": opened["snapshot"]["revision"], "event_id": node["events"]["click"],
        })
        assert "Count 1" in text_of(clicked["snapshot"])
        with pytest.raises(CordisClientError, match="stale"):
            await pool.request("client-test", token, {
                "revision": opened["snapshot"]["revision"], "event_id": node["events"]["click"],
            })
        assert "Count 1" in text_of((await pool.request("client-test", token))["snapshot"])
        guest = pool._guests[token]
        manager.disable("client-test", tmp_path)
        with pytest.raises(CordisClientError, match="explicit"):
            await pool.request("client-test", token)
        assert not guest.worker.running
        assert not Path(guest.temporary.name).exists()
    finally:
        await pool.aclose()


async def test_client_configuration_change_revokes_old_guest(tmp_path):
    manager = install_client(tmp_path)
    manager.enable("client-test", tmp_path, allow_client=True)
    pool = CordisClientPool(manager)
    try:
        opened = await pool.open("client-test", tmp_path)
        guest = pool._guests[opened["ui_session_id"]]
        manager.configure("client-test", tmp_path, {"message": "changed"})
        await pool.reconcile()
        assert not guest.worker.running
        assert not pool._guests
    finally:
        await pool.aclose()


async def test_unchanged_harness_client_bundle_renders_locale_and_configuration_slots(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/compat/tests/fixtures/client/live-client.mjs"
    package = {"name": "@fixture/live-client", "version": "1.0.0", "type": "module",
               "exports": {".": "./backend.mjs", "./client": "./client.mjs"},
               "dsh": {"client": {"platform": "web"}, "bundle": {"patch": "cordis.patch.yml"}}}
    source = {
        "package.json": json.dumps(package).encode(), "client.mjs": fixture.read_bytes(),
        "backend.mjs": b"export default {apply(){}};",
        "cordis.patch.yml": b"- insert:\n  - id: fixture-live-client\n    name: ./backend.mjs\n",
    }
    folder = tmp_path / "source"
    folder.mkdir()
    for name, content in adapt_harness_package(source).items():
        (folder / name).write_bytes(content)
    manager = CordisManager(tmp_path / "registry")
    plugin = manager.install(folder)
    await manager.enable_async(plugin["id"], tmp_path, allow_client=True)
    pool = CordisClientPool(manager)
    try:
        opened = await pool.open(plugin["id"], tmp_path)
        assert len(opened["snapshot"]["views"]) == 3
        assert "Live plugin enabled" in text_of(opened["snapshot"])
        assert "Greeting" in text_of(opened["snapshot"])
        assert opened["snapshot"]["warnings"]
    finally:
        await pool.aclose()


async def test_client_api_requires_explicit_grant_and_disposes_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = install_client(tmp_path)
    config = load_config(working_directory=tmp_path)
    config = replace(config, automations=replace(config.automations, enabled=False),
                     petdex=replace(config.petdex, enabled=False))
    server = DaemonServer(config, start_telegram_bridge=False)
    server.cordis_manager = manager
    server.client_plugins.pool.manager = manager
    async with TestClient(TestServer(server.app())) as client:
        endpoint = "/plugins/client-test/ui"
        denied = await client.post(endpoint + "/open", json={})
        assert denied.status == 400
        assert not server.client_plugins.pool._guests
        enabled = await client.patch("/plugins/client-test", json={"enabled": True, "allow_client": True})
        assert enabled.status == 200, await enabled.text()
        opened_response = await client.post(endpoint + "/open", json={})
        assert opened_response.status == 200, await opened_response.text()
        opened = await opened_response.json()
        path = endpoint + "/" + opened["ui_session_id"]
        node = opened["snapshot"]["views"][0]["tree"][0]
        changed = await client.post(path + "/events", json={
            "revision": opened["snapshot"]["revision"], "event_id": node["events"]["click"],
        })
        assert changed.status == 200, await changed.text()
        assert "Count 1" in text_of((await changed.json())["snapshot"])
        assert (await client.get(path)).status == 200
        assert (await client.delete(path)).status == 200
        assert not server.client_plugins.pool._guests


async def test_client_guest_capacity_is_bounded_and_idle_guests_expire(tmp_path, monkeypatch):
    monkeypatch.setattr("libre_claw.core.cordis_client.MAX_CLIENT_GUESTS", 1)
    manager = install_client(tmp_path)
    manager.enable("client-test", tmp_path, allow_client=True)
    pool = CordisClientPool(manager)
    try:
        first = await pool.open("client-test", tmp_path)
        with pytest.raises(CordisClientError, match="Close an existing"):
            await pool.open("client-test", tmp_path)
        guest = pool._guests[first["ui_session_id"]]
        guest.touched -= 1000
        await pool.reconcile()
        assert not guest.worker.running
        assert not pool._guests
    finally:
        await pool.aclose()


def test_client_chunks_come_only_from_verified_snapshot_siblings(tmp_path):
    manager = install_client(tmp_path, extra_files={
        "client.panel.js": "// reviewed sibling", "client.panel.js.map": "{}",
        "nested/client.foreign.js": "// different directory", "other.js": "// not a compiler chunk",
    })
    manager.enable("client-test", tmp_path, allow_client=True)
    spec = client_spec(manager, "client-test", tmp_path)
    assert spec["chunks"] == {"client.panel.js": str(spec["root"] / "client.panel.js")}
    assert Path(spec["chunks"]["client.panel.js"]).read_text() == "// reviewed sibling"
    # Source edits and files added after review never extend the guest map.
    (tmp_path / "package" / "client.unreviewed.js").write_text("// not in snapshot")
    assert client_spec(manager, "client-test", tmp_path)["chunks"] == spec["chunks"]
    (spec["root"] / "client.unreviewed.js").write_text("// snapshot tampering")
    with pytest.raises(CordisError, match="snapshot changed"):
        client_spec(manager, "client-test", tmp_path)


async def test_actual_harness_compiled_dynamic_chunk_loads_in_offline_guest(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/compat/tests/fixtures/client/dynamic"
    package = {"name": "@deepseek-ai/dsh-client-ui-conversation", "version": "1.0.0", "type": "module",
               "exports": {"./client": "./lib/client.js"}, "dsh": {"client": {"platform": "web"}}}
    source = {"package.json": json.dumps(package).encode(),
              **{f"lib/{name}": (fixture / name).read_bytes() for name in ("client.js", "client.panel.js")}}
    folder = tmp_path / "source"
    folder.mkdir()
    for name, content in adapt_harness_package(source).items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manager = CordisManager(tmp_path / "registry")
    plugin = manager.install(folder)
    await manager.enable_async(plugin["id"], tmp_path, allow_client=True)
    pool = CordisClientPool(manager)
    try:
        spec = client_spec(manager, plugin["id"], tmp_path)
        assert spec["chunks"] == {"client.panel.js": str(spec["root"] / "lib/client.panel.js")}
        opened = await pool.open(plugin["id"], tmp_path)
        assert "Waiting for chunk" in text_of(opened["snapshot"])
        button = opened["snapshot"]["views"][0]["tree"][0]["children"][0]
        changed = await pool.request(plugin["id"], opened["ui_session_id"], {
            "revision": opened["snapshot"]["revision"], "event_id": button["events"]["click"],
        })
        assert "Compiled lazy panel" in text_of(changed["snapshot"])
        assert "Waiting for chunk" not in text_of(changed["snapshot"])
    finally:
        await pool.aclose()


async def test_cancelled_chunk_event_closes_offline_guest(tmp_path):
    source = """window.__ModuleLoader__.load({id:'client-test',factory(require){
      const React=require('react');return {inject:['slots'],apply(ctx){
        ctx.slots.register({name:'shell.overlay',id:'wait'},()=>React.createElement('button',
          {onClick:()=>require.async('./client.wait.js')},'Load'));
      }};
    }});"""
    manager = install_client(tmp_path, source=source, extra_files={
        "package.json": '{"type":"module"}', "client.wait.js": "await new Promise(()=>{});",
    })
    manager.enable("client-test", tmp_path, allow_client=True)
    pool = CordisClientPool(manager)
    try:
        opened = await pool.open("client-test", tmp_path)
        guest = pool._guests[opened["ui_session_id"]]
        button = opened["snapshot"]["views"][0]["tree"][0]
        request = asyncio.create_task(pool.request("client-test", opened["ui_session_id"], {
            "revision": opened["snapshot"]["revision"], "event_id": button["events"]["click"],
        }))
        await asyncio.sleep(0.1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not guest.worker.running
        assert not pool._guests
        assert not Path(guest.temporary.name).exists()
    finally:
        await pool.aclose()
