# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from libre_claw.cli import main
from libre_claw.config import load_config
from libre_claw.core.cordis import CordisManager
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer, _cordis_local_request
from libre_claw.tools_builtin import create_builtin_registry


@pytest.fixture
def plugin_project(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    source = tmp_path / "example"
    runner = CliRunner()
    result = runner.invoke(main, ["cordis", "new", str(source)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["cordis", "install", str(source)])
    assert result.exit_code == 0, result.output
    return workspace, source, runner


def test_cli_install_does_not_execute_and_enable_is_project_scoped(plugin_project, tmp_path):
    workspace, source, runner = plugin_project
    manager = CordisManager()
    status = manager.list_plugins(workspace)[0]
    assert status["enabled"] is False
    assert status["integrity"] == "valid"
    assert not (manager.root / "state").exists()
    result = runner.invoke(main, ["cordis", "enable", "local-word-count"])
    assert result.exit_code == 0, result.output
    enabled = manager.list_plugins(workspace)[0]
    assert enabled["enabled"] is True
    assert enabled["grants"] == {"allow_network": False, "read_paths": [], "write_paths": []}
    another = tmp_path / "another"
    another.mkdir()
    assert manager.list_plugins(another)[0]["enabled"] is False


async def test_scaffold_plugin_runs_through_real_cordis_and_normal_registry(plugin_project, tmp_path):
    workspace, source, runner = plugin_project
    manager = CordisManager()
    try:
        prepare_cordis_process("node", manager.runtime_path, source, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(str(exc))
    manager.enable("local-word-count", workspace)
    config = load_config()
    registry = create_builtin_registry(config)
    name = "cordis__local-word-count__count_words"
    tool = registry.get(name)
    assert tool.permission_level == "ask"
    assert tool.is_read_only({"text": "hello"}) is False
    result = await tool.execute(text="one two three")
    assert result.error is None, result.error
    assert json.loads(result.content) == {"words": 3}
    status = await manager.inspect("local-word-count", workspace)
    assert status["runtime_version"] == "4.0.2"
    assert "count_words" in status["tools"]
    assert "network-denied" in status["isolation"]
    manager.disable("local-word-count", workspace)
    revoked = await tool.execute(text="must not run")
    assert revoked.error


def test_cordis_tools_obey_global_configuration_and_tool_filters(plugin_project):
    workspace, _, _ = plugin_project
    CordisManager().enable("local-word-count", workspace)
    config = load_config()
    name = "cordis__local-word-count__count_words"
    assert name in create_builtin_registry(config)
    disabled = replace(config, cordis=replace(config.cordis, enabled=False))
    assert name not in create_builtin_registry(disabled)
    denied = replace(config, agent=replace(config.agent, tool_denylist=(name,)))
    assert name not in create_builtin_registry(denied)
    allowed = replace(config, agent=replace(config.agent, tool_allowlist=(name,)))
    assert [tool.name for tool in create_builtin_registry(allowed).tools()] == [name]


def test_cli_inspects_real_runtime_without_a_model_or_network(plugin_project, tmp_path):
    workspace, source, runner = plugin_project
    manager = CordisManager()
    try:
        prepare_cordis_process("node", manager.runtime_path, source, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(str(exc))
    manager.enable("local-word-count", workspace)
    result = runner.invoke(main, ["cordis", "inspect", "local-word-count"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state"] == "ACTIVE"
    assert payload["runtime_version"] == "4.0.2"
    assert "network-denied" in payload["isolation"]


def request(payload=None, *, remote="127.0.0.1", host="127.0.0.1:8766", origin=None):
    async def body():
        return payload
    return SimpleNamespace(remote=remote, host=host, scheme="http", content_type="application/json",
        headers={"Origin": origin} if origin else {}, json=body,
        match_info={"plugin_id": "local-word-count"})


@pytest.mark.parametrize("req", [
    request(remote="192.168.1.1"),
    request(host="rebound.example:8766"),
    request(origin="https://evil.example"),
    request(origin="null"),
    request(origin="http://127.0.0.1:9999"),
    request(origin="http://localhost:8766"),
])
def test_plugin_management_rejects_remote_rebinding_and_cross_origin_requests(req):
    assert _cordis_local_request(req) is False


async def test_dashboard_only_grants_offline_access_to_installed_plugins(plugin_project, tmp_path):
    workspace, _, _ = plugin_project
    server = DaemonServer(load_config(), run_store=RunStore(tmp_path / "runs"))
    enabled = await server.update_plugin(request({"enabled": True}, origin="http://127.0.0.1:8766"))
    assert enabled.status == 200
    plugin = json.loads(enabled.body)["plugins"][0]
    assert plugin["enabled"] is True
    assert plugin["grants"]["allow_network"] is False
    extra = await server.update_plugin(request({"enabled": True, "allow_network": True}))
    assert extra.status == 400
    denied = await server.update_plugin(request({"enabled": False}, origin="https://evil.example"))
    assert denied.status == 403
    assert CordisManager().list_plugins(workspace)[0]["enabled"] is True
    disabled = await server.update_plugin(request({"enabled": False}))
    assert disabled.status == 200
    assert json.loads(disabled.body)["plugins"][0]["enabled"] is False
