# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from libre_claw.config import load_config
from libre_claw.core.session import ChatMessage, text_block
from libre_claw.providers import ProviderConfigurationError, create_provider
from libre_claw.providers.base import Done, TextDelta
from libre_claw.providers.factory import _canonical_provider_name
from libre_claw.providers.llamacpp import (
    DEFAULT_LLAMACPP_BASE_URL,
    LlamaCppDiscoveryError,
    LlamaCppModel,
    LlamaCppProvider,
    discover_llamacpp_models,
    normalize_llamacpp_base_url,
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


@pytest.mark.parametrize("error", [httpx.ConnectTimeout(""), httpx.ReadTimeout("")])
async def test_discovery_explains_timeouts_with_empty_exception_messages(error) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise error

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(LlamaCppDiscoveryError, match="Timed out.*local.test/v1/models.*reachable"):
            await discover_llamacpp_models("http://local.test/ui/", client=client)


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


@pytest.mark.parametrize(("url", "expected"), [
    ("http://stargate.local:8080/v1", "http://stargate.local:8080"),
    ("http://stargate.local:8080/v1/", "http://stargate.local:8080"),
    ("http://localhost:8080/", "http://localhost:8080"),
    (" https://swap.example/v1 ", "https://swap.example"),
    ("http://192.168.1.188:8080/ui/", "http://192.168.1.188:8080"),
    ("http://localhost:8080/ui/index.html?model=future#chat", "http://localhost:8080"),
    ("https://swap.example/llama/ui/?theme=dark#models", "https://swap.example/llama"),
    ("https://swap.example/llama/v1/", "https://swap.example/llama"),
    ("http://[::1]:8080/ui/", "http://[::1]:8080"),
    ("https://swap.example/ui/proxy/", "https://swap.example/ui/proxy"),
    ("https://swap.example/my-ui/", "https://swap.example/my-ui"),
    ("https://swap.example/proxy?model=ignored#ignored", "https://swap.example/proxy"),
])
def test_normalize_llamacpp_base_url(url: str, expected: str) -> None:
    assert normalize_llamacpp_base_url(url) == expected


@pytest.mark.parametrize("url", [
    "", "localhost:8080", "file:///tmp/models", "ftp://local.test/", "http:///ui/",
    "http://[::1", "http://localhost:invalid", "http://localhost:99999",
    "http://local host:8080", "http://local\nhost:8080",
])
def test_normalize_llamacpp_base_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS server URL"):
        normalize_llamacpp_base_url(url)
    with pytest.raises(ProviderConfigurationError, match="HTTP or HTTPS server URL"):
        LlamaCppProvider(url, model="future-model", max_tokens=4096)


@pytest.mark.parametrize("suffix", ["/ui/", "/ui/index.html?model=future#chat", "/v1/"])
async def test_discovery_uses_api_from_copied_ui_url(suffix: str) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "future-local-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        models = await discover_llamacpp_models("http://local.test/proxy" + suffix, client=client)
    assert [model.model for model in models] == ["future-local-model"]
    assert [str(request.url) for request in requests] == ["http://local.test/proxy/v1/models"]


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, []])
async def test_discovery_rejects_invalid_model_lists(payload: Any) -> None:
    client = FakeClient(FakeResponse(payload))
    with pytest.raises(LlamaCppDiscoveryError, match="invalid model list"):
        await discover_llamacpp_models("http://local.test/ui/", client=client)


async def test_discovery_rejects_invalid_url_without_request() -> None:
    client = FakeClient(FakeResponse({"data": []}))
    with pytest.raises(LlamaCppDiscoveryError, match="HTTP or HTTPS server URL"):
        await discover_llamacpp_models("file:///tmp/models", client=client)
    assert client.requested_url == ""


@pytest.mark.parametrize("suffix", ["/ui/", "/ui/index.html?model=future#chat", "/v1/"])
async def test_factory_inference_normalizes_saved_browser_url(monkeypatch, tmp_path: Path, suffix: str) -> None:
    from openai import AsyncOpenAI

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[general]\ndefault_provider = "llamacpp"\n'
        '[providers.llamacpp]\ndefault_model = "future-model"\n'
        f'base_url = "http://local.test/proxy{suffix}"\n',
        encoding="utf-8",
    )
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        chunk = {"id": "local-test", "object": "chat.completion.chunk", "created": 0,
                 "model": "future-model", "choices": [{"index": 0,
                 "delta": {"content": "Connected"}, "finish_reason": "stop"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr("libre_claw.providers.openai.AsyncOpenAI",
                            lambda **kwargs: AsyncOpenAI(http_client=client, **kwargs))
        provider = create_provider(load_config(config_path=config_path))
        events = [event async for event in provider.complete(
            [ChatMessage(role="user", content=[text_block("Hello")])]
        )]
    assert provider.base_url == "http://local.test/proxy"
    assert [str(request.url) for request in requests] == ["http://local.test/proxy/v1/chat/completions"]
    assert json.loads(requests[0].content)["model"] == "future-model"
    assert events[0] == TextDelta("Connected")
    assert isinstance(events[-1], Done)


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
