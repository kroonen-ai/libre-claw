# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest

from libre_claw.config import load_config
from libre_claw.core.cordis import CordisManager
from libre_claw.core.cordis_packages import _package_files
from libre_claw.core.cordis_ipc import CordisIpcError
from libre_claw.core.cordis_llm import CordisLlmBridge
from libre_claw.core.cordis_services import CordisServicePool
from libre_claw.providers.base import Done, TextDelta
from libre_claw.providers.model_catalog import ModelCatalog, ModelInfo


class FakeModels:
    def __init__(self):
        self.calls = 0

    async def discover(self, config, provider):
        return ModelCatalog((ModelInfo("deepseek", "model", "Model"),), "test")

    async def complete(self, messages, **kwargs):
        self.calls += 1
        yield TextDelta("Hello")
        yield Done(stop_reason="stop")

    def bridge(self, config, *, authorize):
        return CordisLlmBridge(config, authorize=authorize, provider_factory=lambda *args, **kwargs: self, model_discovery=self.discover)


@pytest.fixture
async def services(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = replace(load_config(), providers={"deepseek": {"default_model": "model", "api_key": "never-export-model-key"}})
    current = [config]
    workspace = tmp_path / "project"
    workspace.mkdir()
    files = _package_files(Path(__file__).parent / "fixtures/native-provider-0.1.1")
    source = tmp_path / "package"
    source.mkdir()
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with tempfile.TemporaryDirectory(prefix="lc-pool-", dir="/tmp") as directory:
        manager = CordisManager(Path(directory) / "registry", node_executable="must-not-execute-node", config=config)
        plugin_id = manager.install(source)["id"]
        models = FakeModels()
        pool = CordisServicePool(lambda: current[0], manager_factory=lambda _: manager, bridge_factory=models.bridge)
        try:
            yield pool, manager, plugin_id, workspace, models, current
        finally:
            await pool.aclose()


async def catalog(path):
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(path))) as client:
        async with client.post("http://localhost/catalog", json={}) as response:
            return response.status, await response.json()


async def test_pool_starts_only_explicitly_enabled_reviewed_model_service(services):
    pool, manager, plugin_id, workspace, models, _ = services
    disabled = await pool.sync(workspace)
    assert disabled[0]["state"] == "disabled"
    assert not (manager.root / "ipc").exists()
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    live = await pool.sync(workspace)
    assert live[0]["state"] == "running" and live[0]["active_requests"] == 0
    assert "never-export-model-key" not in json.dumps(live)
    path = Path(live[0]["socket_path"])
    status, first = await catalog(path)
    assert status == 200 and first["models"][0]["model"] == "model"
    await pool.sync(workspace)
    assert (await catalog(path))[1]["instanceId"] == first["instanceId"]
    assert models.calls == 0
    await pool.aclose()
    assert not path.exists() and pool.status(workspace)[0]["state"] == "stopped"
    with pytest.raises(CordisIpcError, match="closed"):
        await pool.sync(workspace)


async def test_native_model_generation_uses_host_bridge_without_child_execution(services):
    pool, manager, plugin_id, workspace, models, _ = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    row = (await pool.sync(workspace))[0]
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=row["socket_path"])) as client:
        async with client.post("http://localhost/generate", json={"provider": "deepseek", "model": "model", "messages": [
            {"id": "u1", "role": "user", "source": {"kind": "user"}, "content": [{"type": "text", "text": "Explicit input"}]},
        ]}) as response:
            assert response.status == 200
            chunks = [json.loads(line) for line in (await response.text()).splitlines()]
            assert chunks[-1]["reason"]["kind"] == "stop"
            assert models.calls == 1


async def test_registry_revocation_blocks_requests_before_periodic_reconciliation(services):
    pool, manager, plugin_id, workspace, _, _ = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    path = Path((await pool.sync(workspace))[0]["socket_path"])
    manager.disable(plugin_id, workspace)
    assert (await catalog(path))[0] == 403
    assert (await pool.sync(workspace))[0]["state"] == "disabled"
    assert not path.exists()


async def test_tampered_source_revokes_live_service_and_closes_it(services):
    pool, manager, plugin_id, workspace, _, _ = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    path = Path((await pool.sync(workspace))[0]["socket_path"])
    snapshot = manager._snapshot(plugin_id, manager._record(plugin_id))
    source = snapshot / "runtime/native-provider-plugin.js"
    source.chmod(0o600)
    source.write_bytes(source.read_bytes() + b"\n// unreviewed change\n")
    assert (await catalog(path))[0] == 403
    await pool.sync(workspace)
    assert not path.exists()


async def test_configuration_changes_and_forced_credential_refresh_rotate_instance(services):
    pool, manager, plugin_id, workspace, _, current = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    path = Path((await pool.sync(workspace))[0]["socket_path"])
    first = (await catalog(path))[1]["instanceId"]
    current[0] = replace(current[0], providers={"deepseek": {"default_model": "model", "api_key": "rotated-private-key"}})
    assert (await catalog(path))[0] == 403
    rows = await pool.sync(workspace)
    second = (await catalog(path))[1]["instanceId"]
    assert second != first
    assert "rotated-private-key" not in json.dumps(rows)
    await pool.sync(workspace, force=True)
    assert (await catalog(path))[1]["instanceId"] != second


async def test_component_configuration_can_stop_a_service_without_revoking_plugin(services):
    pool, manager, plugin_id, workspace, _, _ = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    path = Path((await pool.sync(workspace))[0]["socket_path"])
    await manager.configure_async(plugin_id, workspace, {"components": {"libre-webui-native-provider": {"enabled": False}}})
    assert (await catalog(path))[0] == 403
    assert (await pool.sync(workspace))[0]["state"] == "disabled"
    assert not path.exists()
    assert manager.details(plugin_id, workspace)["enabled"] is True


async def test_multiple_workspaces_keep_authorization_scoped_to_their_own_service(services, tmp_path):
    pool, manager, plugin_id, workspace, _, _ = services
    other = tmp_path / "other"
    other.mkdir()
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    await manager.enable_async(plugin_id, other, allow_model=True)
    first = Path((await pool.sync(workspace))[0]["socket_path"])
    second = Path((await pool.sync(other))[0]["socket_path"])
    assert first != second
    assert (await catalog(first))[0] == (await catalog(second))[0] == 200
    manager.disable(plugin_id, workspace)
    assert (await catalog(first))[0] == 403
    assert (await catalog(second))[0] == 200
    await pool.sync(workspace)
    assert not first.exists() and second.exists()


async def test_global_disable_closes_services_without_recreating_private_state(services):
    pool, manager, plugin_id, workspace, _, current = services
    await manager.enable_async(plugin_id, workspace, allow_model=True)
    path = Path((await pool.sync(workspace))[0]["socket_path"])
    current[0] = replace(current[0], cordis=replace(current[0].cordis, enabled=False))
    assert (await catalog(path))[0] == 403
    assert await pool.sync(workspace) == []
    assert not path.exists()


@pytest.mark.parametrize("change", ["disable", "provider", "credential"])
async def test_global_invalidation_closes_other_workspaces_too(services, tmp_path, change):
    pool, manager, plugin_id, workspace, _, current = services
    other = tmp_path / "other"
    other.mkdir()
    for project in (workspace, other):
        await manager.enable_async(plugin_id, project, allow_model=True)
    await pool.sync(workspace)
    path = Path((await pool.sync(other))[0]["socket_path"])
    if change == "disable":
        current[0] = replace(current[0], cordis=replace(current[0].cordis, enabled=False))
    elif change == "provider":
        current[0] = replace(current[0], providers={"deepseek": {"default_model": "changed"}})
    await pool.sync(workspace, force=change == "credential")
    assert not path.exists()
    assert pool.status(other)[0]["state"] == "stopped"
