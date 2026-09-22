# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer
from libre_claw.providers.model_catalog import ModelCatalog


@pytest.mark.parametrize(
    ("selected_provider", "requested_provider", "expected_model"),
    [
        ("deepseek", "", "custom-current-model"),
        ("deepseek", "deepseek", "custom-current-model"),
        ("openrouter", "deepseek", "configured-flash-model"),
        ("openrouter", "local", "configured-local-model"),
        ("openrouter", "llama-swap", "configured-cpp-model"),
        ("ollama", "local", "custom-current-model"),
        ("openrouter", "unknown-provider", ""),
    ],
)
async def test_catalog_exposes_provider_default_when_discovery_is_unavailable(
    monkeypatch,
    tmp_path: Path,
    selected_provider: str,
    requested_provider: str,
    expected_model: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(
        config,
        general=replace(
            config.general,
            default_provider=selected_provider,
            default_model="custom-current-model",
        ),
        providers={
            **config.providers,
            "deepseek": {"default_model": "configured-flash-model"},
            "ollama": {"default_model": "configured-local-model"},
            "llamacpp": {"default_model": "configured-cpp-model"},
        },
    )
    calls = []

    async def discover(_config, provider, **_kwargs):
        calls.append(provider)
        return ModelCatalog((), "configured", "Discovery unavailable")

    monkeypatch.setattr("libre_claw.daemon.discover_models", discover)
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"))
    query = {"provider": requested_provider} if requested_provider else {}

    response = await server.list_models(SimpleNamespace(query=query))
    payload = json.loads(response.text)

    assert payload["default_model"] == expected_model
    assert payload["provider"] == (requested_provider or selected_provider)
    assert calls == [requested_provider or selected_provider]
    assert payload["models"] == []
    assert payload["source"] == "configured"
    assert payload["error"] == "Discovery unavailable"
