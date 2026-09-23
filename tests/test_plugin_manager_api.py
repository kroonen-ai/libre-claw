# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from click.testing import CliRunner

from libre_claw.cli import main
from libre_claw.config import load_config
from libre_claw.cordis_cli import plugin_command
from libre_claw.core.cordis import CordisManager
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer
from libre_claw.tools_builtin import create_builtin_registry, refresh_cordis_tools


@pytest.fixture
async def plugin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = load_config(working_directory=workspace)
    config = replace(config, automations=replace(config.automations, enabled=False),
                     petdex=replace(config.petdex, enabled=False))
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"), start_telegram_bridge=False)
    async with TestClient(TestServer(server.app())) as client:
        yield client, server


async def install_example(client):
    checked = await client.post("/plugins/preview", json={"source": "builtin:text-utilities"})
    assert checked.status == 200, await checked.text()
    preview = await checked.json()
    installed = await client.post("/plugins/install", json={"token": preview["token"]})
    assert installed.status == 200, await installed.text()
    return (await installed.json())["plugin"]


async def test_dashboard_installs_configures_and_removes_without_autogrants(plugin_client):
    client, server = plugin_client
    inventory = await (await client.get("/plugins")).json()
    assert inventory["plugins"] == []
    assert inventory["catalog"][0]["source"] == "builtin:text-utilities"
    plugin = await install_example(client)
    assert plugin["enabled"] is False
    manager = CordisManager()
    assert not (manager.root / "state").exists()

    details = await client.get("/plugins/text-utilities")
    assert details.headers["Cache-Control"] == "no-store"
    payload = (await details.json())["plugin"]
    assert payload["config"]["include_characters"] is True
    assert payload["config_schema"]["properties"]["include_characters"]["type"] == "boolean"
    assert payload["tool_definitions"][0]["name"] == "count_text"

    invalid = await client.put("/plugins/text-utilities/config", json={"config": {"include_characters": "no"}})
    assert invalid.status == 400
    saved = await client.put("/plugins/text-utilities/config", json={"config": {"include_characters": False}})
    assert saved.status == 200, await saved.text()
    assert (await saved.json())["plugin"]["config"] == {"include_characters": False}
    assert manager.list_plugins(server.config.general.working_directory)[0]["enabled"] is False
    assert not (manager.root / "state").exists()

    enabled = await client.patch("/plugins/text-utilities", json={"enabled": True})
    assert enabled.status == 200
    record = manager.list_plugins(server.config.general.working_directory)[0]
    assert record["grants"] == {"allow_network": False, "read_paths": [], "write_paths": []}
    disabled = await client.patch("/plugins/text-utilities", json={"enabled": False})
    assert disabled.status == 200
    assert manager.details("text-utilities", server.config.general.working_directory)["config"] == {"include_characters": False}
    removed = await client.delete("/plugins/text-utilities")
    assert removed.status == 200
    assert (await removed.json())["removed"] is True
    assert manager.list_plugins(server.config.general.working_directory) == []


async def test_preview_can_be_cancelled_and_is_required_before_install(plugin_client):
    client, server = plugin_client
    preview = await (await client.post("/plugins/preview", json={"source": "builtin:text-utilities"})).json()
    assert len(preview["digest"]) == 64
    assert CordisManager().list_plugins(server.config.general.working_directory) == []
    discarded = await client.delete(f"/plugins/preview/{preview['token']}")
    assert discarded.status == 200
    expired = await client.post("/plugins/install", json={"token": preview["token"]})
    assert expired.status == 400
    unchecked = await client.post("/plugins/install", json={"source": "builtin:text-utilities"})
    assert unchecked.status == 400
    assert CordisManager().list_plugins(server.config.general.working_directory) == []


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/plugins/preview", {"source": "builtin:text-utilities"}),
    ("post", "/plugins/install", {"token": "not-reviewed"}),
    ("delete", "/plugins/preview/not-reviewed", None),
    ("get", "/plugins/text-utilities", None),
    ("put", "/plugins/text-utilities/config", {"config": {}}),
    ("post", "/plugins/text-utilities/inspect", {}),
    ("delete", "/plugins/text-utilities", None),
])
async def test_plugin_management_rejects_foreign_origins(plugin_client, method, path, payload):
    client, _ = plugin_client
    response = await getattr(client, method)(path, json=payload, headers={"Origin": "https://attacker.invalid"})
    assert response.status == 403
    assert not (CordisManager().root / "registry.json").exists()


async def test_management_rejects_extra_grants_and_malformed_payloads(plugin_client):
    client, _ = plugin_client
    for payload in ([], {"source": "builtin:text-utilities", "allow_network": True}, {"source": 123}):
        rejected = await client.post("/plugins/preview", json=payload)
        assert rejected.status == 400
    bad_json = await client.post("/plugins/preview", data="{", headers={"Content-Type": "application/json"})
    assert bad_json.status == 400
    wrong_type = await client.post("/plugins/preview", data='{"source":"builtin:text-utilities"}')
    assert wrong_type.status == 415
    await install_example(client)
    extra = await client.put("/plugins/text-utilities/config", json={"config": {}, "allow_network": True})
    assert extra.status == 400
    inactive = await client.post("/plugins/text-utilities/inspect", json={})
    assert inactive.status == 400


def test_plugin_changes_refresh_between_turns_and_preserve_other_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    registry = create_builtin_registry(config)
    original_tools = registry.tools()
    runner = CliRunner()
    result = runner.invoke(main, ["--working-directory", str(tmp_path), "cordis", "install", "builtin:text-utilities"])
    assert result.exit_code == 0, result.output
    name = "cordis__text-utilities__count_text"
    assert name not in refresh_cordis_tools(config, registry)
    manager = CordisManager()
    manager.enable("text-utilities", tmp_path)
    refreshed = refresh_cordis_tools(config, registry)
    assert name in refreshed
    assert refreshed.get(name).context is registry.context
    assert all(refreshed.get(tool.name) is tool for tool in original_tools)
    denied = replace(config, agent=replace(config.agent, tool_denylist=(name,)))
    assert name not in refresh_cordis_tools(denied, refreshed)
    manager.disable("text-utilities", tmp_path)
    assert name not in refresh_cordis_tools(config, refreshed)


async def test_cli_and_tui_offer_install_catalog_and_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    runner = CliRunner()
    catalog = await plugin_command(config, "catalog")
    assert "Text utilities" in catalog
    installed = await plugin_command(config, "install builtin:text-utilities")
    assert "disabled" in installed
    enabled = await plugin_command(config, "enable text-utilities")
    assert "next message" in enabled
    reinstalled = await plugin_command(config, "install builtin:text-utilities")
    assert "remains enabled" in reinstalled
    details = json.loads(await plugin_command(config, "details text-utilities"))
    assert details["config"] == {"include_characters": True}
    result = await asyncio.to_thread(runner.invoke, main,
        ["--working-directory", str(tmp_path), "cordis", "config", "text-utilities", "--file", "-"],
        input='{"include_characters":false}')
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["config"] == {"include_characters": False}


def test_cli_relative_package_paths_keep_shell_directory_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    runner = CliRunner()
    created = runner.invoke(main, ["cordis", "new", "./plugin"])
    assert created.exit_code == 0, created.output
    command = ["--working-directory", str(workspace), "cordis"]
    checked = runner.invoke(main, command + ["preview", "./plugin"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.stdout)["id"] == "local-word-count"
    installed = runner.invoke(main, command + ["install", "./plugin"])
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.stdout)["id"] == "local-word-count"
