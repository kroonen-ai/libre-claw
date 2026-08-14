# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from libre_claw.config import load_config
from libre_claw.providers import ProviderConfigurationError, create_provider
from libre_claw.providers.factory import _canonical_provider_name
from libre_claw.providers.llamacpp import (
    DEFAULT_LLAMACPP_BASE_URL,
    LlamaCppDiscoveryError,
    LlamaCppModel,
    LlamaCppProvider,
    discover_llamacpp_models,
)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://localhost:8080/v1/models")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requested_url = ""
        self.headers: dict[str, str] = {}

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.requested_url = url
        self.headers = dict(headers or {})
        return self.response


def test_llamacpp_provider_name_aliases() -> None:
    for alias in ("llamacpp", "llama-cpp", "llama_cpp", "llama.cpp", "llama-swap", "llamaswap", "LLAMA-SWAP"):
        assert _canonical_provider_name(alias) == "llamacpp"


def test_create_provider_supports_llamacpp(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "llama-swap"',
                "[providers.llamacpp]",
                'default_model = "qwen3-30b"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, LlamaCppProvider)
    assert provider.model == "qwen3-30b"
    assert provider.api_format == "openai"
    assert provider.base_url == DEFAULT_LLAMACPP_BASE_URL


def test_create_provider_llamacpp_requires_model(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"llamacpp\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="default_model"):
        create_provider(config)


@pytest.mark.asyncio
async def test_discover_llamacpp_models_lists_swap_config() -> None:
    client = FakeClient(
        FakeResponse(
            {
                "object": "list",
                "data": [
                    {"id": "qwen3-30b", "object": "model"},
                    {"id": "models/gemma-3-27b.gguf", "object": "model"},
                    {"id": "qwen3-30b", "object": "model"},
                ],
            }
        )
    )

    models = await discover_llamacpp_models("http://localhost:9292/", client=client)

    assert client.requested_url == "http://localhost:9292/v1/models"
    assert client.headers == {}
    assert models == (
        LlamaCppModel(model="models/gemma-3-27b.gguf", label="gemma-3-27b"),
        LlamaCppModel(model="qwen3-30b", label="qwen3-30b"),
    )


@pytest.mark.asyncio
async def test_discover_llamacpp_models_sends_bearer_when_configured() -> None:
    client = FakeClient(FakeResponse({"data": []}))

    models = await discover_llamacpp_models("http://localhost:8080", api_key="secret", client=client)

    assert models == ()
    assert client.headers == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_discover_llamacpp_models_wraps_http_errors() -> None:
    client = FakeClient(FakeResponse({}, status_code=503))

    with pytest.raises(LlamaCppDiscoveryError, match="Could not list llama.cpp models"):
        await discover_llamacpp_models("http://localhost:8080", client=client)


@pytest.mark.asyncio
async def test_discover_llamacpp_models_rejects_invalid_json() -> None:
    client = FakeClient(FakeResponse(json.JSONDecodeError("bad", "", 0)))

    with pytest.raises(LlamaCppDiscoveryError, match="invalid JSON"):
        await discover_llamacpp_models("http://localhost:8080", client=client)


@pytest.mark.asyncio
async def test_daemon_lists_llamacpp_models(monkeypatch, tmp_path: Path) -> None:
    from libre_claw.core.runs import RunStore
    from libre_claw.core.tools import ToolRegistry
    from libre_claw.daemon import DaemonServer

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def fake_discover(base_url: str, **_kwargs: Any) -> tuple[LlamaCppModel, ...]:
        assert base_url == DEFAULT_LLAMACPP_BASE_URL
        return (LlamaCppModel(model="qwen3-30b", label="qwen3-30b"),)

    monkeypatch.setattr("libre_claw.daemon.discover_llamacpp_models", fake_discover)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: None,  # type: ignore[arg-type,return-value]
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    class _Request:
        query: dict[str, str] = {}

    response = await server.list_llamacpp_models(_Request())  # type: ignore[arg-type]

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["base_url"] == DEFAULT_LLAMACPP_BASE_URL
    assert payload["models"] == [{"model": "qwen3-30b", "label": "qwen3-30b"}]


def test_normalize_llamacpp_base_url_strips_v1() -> None:
    from libre_claw.providers.llamacpp import normalize_llamacpp_base_url

    assert normalize_llamacpp_base_url("http://stargate.local:8080/v1") == "http://stargate.local:8080"
    assert normalize_llamacpp_base_url("http://stargate.local:8080/v1/") == "http://stargate.local:8080"
    assert normalize_llamacpp_base_url("http://localhost:8080/") == "http://localhost:8080"
    assert normalize_llamacpp_base_url(" https://swap.example/v1 ") == "https://swap.example"


@pytest.mark.asyncio
async def test_daemon_updates_llamacpp_endpoint(monkeypatch, tmp_path: Path) -> None:
    from libre_claw.core.runs import RunStore
    from libre_claw.core.tools import ToolRegistry
    from libre_claw.daemon import DaemonServer

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: None,  # type: ignore[arg-type,return-value]
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    class _Request:
        query: dict[str, str] = {}

        async def json(self) -> dict[str, Any]:
            return {"base_url": "http://stargate.local:8080/v1", "persist_global": True}

    response = await server.update_llamacpp_config(_Request())  # type: ignore[arg-type]

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["base_url"] == "http://stargate.local:8080"
    config_path = tmp_path / ".libre-claw" / "config.toml"
    assert payload["persisted_path"] == str(config_path)
    saved = config_path.read_text(encoding="utf-8")
    assert 'base_url = "http://stargate.local:8080"' in saved
    assert "[providers.llamacpp]" in saved
    assert server.config.providers["llamacpp"]["base_url"] == "http://stargate.local:8080"

    current = await server.current_llamacpp_config(_Request())  # type: ignore[arg-type]
    assert json.loads(current.body.decode("utf-8"))["base_url"] == "http://stargate.local:8080"


@pytest.mark.asyncio
async def test_daemon_discovery_accepts_base_url_override(monkeypatch, tmp_path: Path) -> None:
    from libre_claw.core.runs import RunStore
    from libre_claw.core.tools import ToolRegistry
    from libre_claw.daemon import DaemonServer

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    probed: list[str] = []

    async def fake_discover(base_url: str, **_kwargs: Any) -> tuple[LlamaCppModel, ...]:
        probed.append(base_url)
        return ()

    monkeypatch.setattr("libre_claw.daemon.discover_llamacpp_models", fake_discover)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: None,  # type: ignore[arg-type,return-value]
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    class _Request:
        query = {"base_url": "http://stargate.local:8080/v1"}

    response = await server.list_llamacpp_models(_Request())  # type: ignore[arg-type]

    assert response.status == 200
    assert probed == ["http://stargate.local:8080"]
