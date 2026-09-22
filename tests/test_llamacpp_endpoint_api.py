# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer
from libre_claw.providers.llamacpp import LlamaCppModel


def make_server(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return DaemonServer(load_config(), run_store=RunStore(tmp_path / "runs"))


async def test_dashboard_discovery_accepts_a_browser_ui_url(monkeypatch, tmp_path):
    server = make_server(monkeypatch, tmp_path)
    seen = []

    async def discover(base_url, **kwargs):
        seen.append(base_url)
        return (LlamaCppModel("future-local-model", "Local model"),)

    monkeypatch.setattr("libre_claw.daemon.discover_llamacpp_models", discover)
    response = await server.list_llamacpp_models(SimpleNamespace(
        query={"base_url": "http://192.168.1.188:8080/ui/?tab=models#settings"},
    ))
    assert response.status == 200
    payload = json.loads(response.body)
    assert seen == ["http://192.168.1.188:8080"]
    assert payload["base_url"] == seen[0]
    assert payload["models"][0]["model"] == "future-local-model"
    assert server.config.providers["llamacpp"]["base_url"] == "http://localhost:8080"


async def test_save_normalizes_ui_url_and_preserves_other_provider_settings(monkeypatch, tmp_path):
    server = make_server(monkeypatch, tmp_path)
    original = dict(server.config.providers["llamacpp"])

    async def body():
        return {"base_url": "https://local.test/llama/ui/", "persist_global": True}

    response = await server.update_llamacpp_config(SimpleNamespace(json=body))
    assert response.status == 200
    assert json.loads(response.body)["base_url"] == "https://local.test/llama"
    expected = {**original, "base_url": "https://local.test/llama"}
    assert server.config.providers["llamacpp"] == expected
    assert load_config().providers["llamacpp"] == expected


@pytest.mark.parametrize("url", ["", "file:///tmp/models", "http://", "http://[invalid", "http://host:bad/ui/"])
async def test_invalid_endpoints_return_validation_errors_without_network(monkeypatch, tmp_path, url):
    server = make_server(monkeypatch, tmp_path)

    async def unexpected(*args, **kwargs):
        raise AssertionError("Invalid URLs must not be requested")

    monkeypatch.setattr("libre_claw.daemon.discover_llamacpp_models", unexpected)

    async def body():
        return {"base_url": url, "persist_global": True}

    response = await server.update_llamacpp_config(SimpleNamespace(json=body))
    assert response.status == 400
    assert "base_url" in json.loads(response.body)["error"]
    if url:
        response = await server.list_llamacpp_models(SimpleNamespace(query={"base_url": url}))
        assert response.status == 400
    assert not (tmp_path / ".libre-claw" / "config.toml").exists()
