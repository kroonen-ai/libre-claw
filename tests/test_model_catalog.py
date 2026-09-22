# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from libre_claw.auth.api_keys import ApiKeyLookup
from libre_claw.config import FallbackRouteConfig, load_config
from libre_claw.providers import model_catalog
from libre_claw.providers.model_catalog import cached_models, discover_models
from libre_claw.providers.moonshot_metadata import apply_moonshot_model_limits


class KeyStore:
    def __init__(self, key: str | None = "test-secret") -> None:
        self.key = key
        self.calls: list[tuple[str, str | None, tuple[str, ...]]] = []

    def get_api_key(self, provider, env_var=None, *, aliases=()):
        self.calls.append((provider, env_var, aliases))
        return ApiKeyLookup(self.key, "environment" if self.key else "missing")


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.chdir(tmp_path)
    for name in (
        "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "LIBRE_CLAW_DEFAULT_PROVIDER",
        "LIBRE_CLAW_DEFAULT_MODEL", "KIMI_API_KEY", "MOONSHOT_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    model_catalog._CACHE.clear()
    model_catalog._ACTIVE_KEYS.clear()
    model_catalog._SELECTED_CACHE.clear()
    return load_config()


def provider_config(config, provider, **settings):
    return replace(config, providers={
        **config.providers,
        provider: {**config.providers.get(provider, {}), **settings},
    })


@pytest.mark.parametrize(("provider", "settings", "path", "payload"), [
    ("openrouter", {"base_url": "https://gateway.test/api/v1/"}, "/api/v1/models",
     {"data": [{"id": "future-lab/new-agent", "name": "Future Agent"}]}),
    ("openai", {"base_url": "https://gateway.test/custom/v1"}, "/custom/v1/models",
     {"data": [{"id": "new-custom-model"}]}),
    ("deepseek", {"default_model": "configured-deepseek"}, "/models",
     {"data": [{"id": "future-deepseek-model", "object": "model", "owned_by": "deepseek"}]}),
    ("deepseek", {"default_model": "configured-deepseek", "base_url": "https://api.deepseek.com/v1/"},
     "/v1/models", {"data": [{"id": "future-deepseek-model"}]}),
    ("deepseek", {"default_model": "configured-deepseek", "base_url": "https://gateway.test/custom/v1/"},
     "/custom/v1/models", {"data": [{"id": "future-custom-model"}]}),
    ("moonshot", {}, "/coding/v1/models", {"data": [{"id": "future-kimi"}]}),
    ("moonshot", {"service": "platform", "base_url": "https://api.moonshot.cn/v1"},
     "/v1/models", {"data": [{"id": "future-platform-model"}]}),
    ("ollama", {}, "/api/tags", {"models": [{"name": "future-model:latest"}]}),
    ("ollama", {"base_url": "http://local.test/api"}, "/api/tags",
     {"models": [{"model": "another-model:latest", "name": "Another model"}]}),
    ("ollama", {"base_url": "http://local.test/v1", "api_format": "openai"},
     "/v1/models", {"data": [{"id": "arbitrary-local-model"}]}),
    ("llamacpp", {"base_url": "http://local.test:8080"}, "/v1/models",
     {"data": [{"id": "models/custom-agent.gguf"}]}),
    ("llamacpp", {"base_url": "http://local.test:8080/ui/"}, "/v1/models",
     {"data": [{"id": "future-agent.gguf"}]}),
    ("llamacpp", {"base_url": "http://local.test:8080/proxy/ui/index.html?model=future#chat"},
     "/proxy/v1/models", {"data": [{"id": "future-agent.gguf"}]}),
    ("llamacpp", {"base_url": "http://local.test:8080/proxy/v1/"}, "/proxy/v1/models",
     {"data": [{"id": "future-agent.gguf"}]}),
])
async def test_discovers_unknown_models_from_configured_provider(config, provider, settings, path, payload):
    config = provider_config(config, provider, **settings)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, provider, api_key_store=KeyStore(), client=client)
        assert not client.is_closed

    assert result.source == "live"
    assert not result.error
    assert requests[0].url.path == path
    if provider == "llamacpp":
        assert not requests[0].url.query
        assert not requests[0].url.fragment
    assert requests[0].headers["authorization"] == "Bearer test-secret"
    model = next(iter(payload.values()))[0]
    model_id = model.get("id") or model.get("model") or model["name"]
    assert model_id in {item.model for item in result.models}
    if config.providers[provider]["default_model"]:
        assert config.providers[provider]["default_model"] in {item.model for item in result.models}


async def test_llamacpp_catalog_reports_invalid_endpoint_without_network(config):
    config = provider_config(config, "llamacpp", base_url="file:///tmp/models", default_model="future-model")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "llamacpp", api_key_store=KeyStore(None), client=client)
    assert requests == []
    assert result.source == "configured"
    assert "HTTP or HTTPS server URL" in result.error
    assert "future-model" in {item.model for item in result.models}


async def test_anthropic_paginates_and_uses_versioned_auth(config):
    config = provider_config(config, "anthropic", base_url="https://anthropic.test/proxy")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/proxy/v1/models"
        assert request.headers["x-api-key"] == "test-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        if request.url.params.get("after_id"):
            return httpx.Response(200, json={"data": [{"id": "claude-future-b"}], "has_more": False})
        return httpx.Response(200, json={
            "data": [{"id": "claude-future-a", "display_name": "Future Claude",
                      "max_input_tokens": 400000, "max_tokens": 96000}],
            "has_more": True, "last_id": "claude-future-a",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "anthropic", api_key_store=KeyStore(), client=client)
    assert len(requests) == 2
    assert requests[1].url.params["after_id"] == "claude-future-a"
    selected = next(item for item in result.models if item.model == "claude-future-a")
    assert selected.label == "Future Claude"
    assert selected.context_window_tokens == 400000
    assert selected.max_completion_tokens == 96000


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_discovery_uses_sdk_base_url_environment(config, monkeypatch, provider):
    base_url = "https://sdk-gateway.test/v1/" if provider == "openai" else "https://sdk-gateway.test/"
    monkeypatch.setenv(f"{provider.upper()}_BASE_URL", base_url)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "future-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await discover_models(config, provider, api_key_store=KeyStore(), client=client)
    assert seen[0].startswith("https://sdk-gateway.test/v1/models")


async def test_openrouter_normalizes_metadata_and_ignores_malformed_rows(config):
    payload = {"data": [None, False, {"id": 123}, {"id": ""},
        {"id": "vendor/z-new", "name": "Z model", "context_length": 300000,
         "top_provider": {"max_completion_tokens": 64000}},
        {"id": "vendor/a-new", "name": None, "context_length": True,
         "top_provider": "invalid", "max_output_tokens": "invalid"},
        {"id": "vendor/image-only", "architecture": {"output_modalities": ["image"]}},
        {"id": "vendor/a-new", "name": "A model"},
    ]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        result = await discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
    assert [item.model for item in result.models] == ["vendor/a-new", "openrouter/auto", "vendor/z-new"]
    assert result.models[0].context_window_tokens is None
    assert result.models[-1].context_window_tokens == 300000
    assert result.models[-1].max_completion_tokens == 64000


@pytest.mark.parametrize("api_key_env", ["DEEPSEEK_API_KEY", "CUSTOM_DEEPSEEK_KEY"])
async def test_deepseek_catalog_keeps_unpublished_capabilities_unknown(config, api_key_env):
    config = provider_config(config, "deepseek", api_key_env=api_key_env)
    store = KeyStore()

    def respond(request):
        assert str(request.url) == "https://api.deepseek.com/models"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "deepseek-next-generation", "object": "model", "owned_by": "deepseek"},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "deepseek", api_key_store=store, client=client)
    info = next(item for item in result.models if item.model == "deepseek-next-generation")
    assert result.source == "live" and not result.error
    assert store.calls == [("deepseek", api_key_env, ())]
    assert info.label == info.model
    assert info.capability_source == "unknown"
    assert all(getattr(info, field) is None for field in (
        "context_window_tokens", "max_completion_tokens", "supports_tools", "supports_vision",
        "supports_reasoning", "supported_reasoning_efforts", "input_cost_per_token",
        "output_cost_per_token", "supports_temperature",
    ))


async def test_deepseek_manual_models_and_capabilities_survive_discovery_failure(config):
    config = replace(
        provider_config(config, "deepseek", default_model="private-deepseek", model_capabilities={
            "private-deepseek": {"context_window_tokens": 131072, "supports_tools": True},
        }),
        general=replace(config.general, default_provider="deepseek", default_model="selected-deepseek"),
        fallback=replace(config.fallback, routes=(FallbackRouteConfig("deepseek", "fallback-deepseek", ""),)),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "sensitive test-secret"}})
    )) as client:
        result = await discover_models(config, "deepseek", api_key_store=KeyStore(), client=client)

    assert result.source == "configured"
    assert result.error == "Model discovery failed (HTTP 401)."
    assert {item.model for item in result.models} == {
        "private-deepseek", "selected-deepseek", "fallback-deepseek",
    }
    info = next(item for item in result.models if item.model == "private-deepseek")
    assert info.context_window_tokens == 131072 and info.supports_tools is True
    assert info.supports_vision is None and info.capability_source == "configured"


async def test_deepseek_accepts_advertised_metadata_from_custom_endpoint(config):
    config = provider_config(config, "deepseek", base_url="https://gateway.test/deepseek")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{
            "id": "gateway-model", "context_window_tokens": 100000, "max_output_tokens": 4096,
            "supports_tools": True, "supports_vision": False,
        }]})
    )) as client:
        result = await discover_models(config, "deepseek", api_key_store=KeyStore(), client=client)

    info = next(item for item in result.models if item.model == "gateway-model")
    assert info.context_window_tokens == 100000 and info.max_completion_tokens == 4096
    assert info.supports_tools is True and info.supports_vision is False
    assert info.supports_reasoning is None and info.capability_source == "provider"


@pytest.mark.parametrize("provider", ["openrouter", "deepseek"])
async def test_cache_ttl_refresh_and_stale_failure(config, monkeypatch, provider):
    now = [1000.0]
    monkeypatch.setattr(model_catalog.time, "monotonic", lambda: now[0])
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 3:
            return httpx.Response(503, text="failure with sensitive provider details")
        return httpx.Response(200, json={"data": [{"id": f"vendor/model-{len(requests)}"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        kwargs = {"api_key_store": KeyStore(), "client": client}
        first = await discover_models(config, provider, **kwargs)
        cached = await discover_models(config, provider, **kwargs)
        assert first.models == cached.models and cached.source == "cache"
        assert len(requests) == 1
        refreshed = await discover_models(config, provider, refresh=True, **kwargs)
        assert "vendor/model-2" in {item.model for item in refreshed.models}
        now[0] += model_catalog._CACHE_TTL + 1
        unavailable = await discover_models(config, provider, **kwargs)
        assert unavailable.models == refreshed.models
        assert unavailable.source == "cache" and unavailable.error.endswith("(HTTP 503).")
        assert "sensitive" not in unavailable.error
        await discover_models(config, provider, **kwargs)
        assert len(requests) == 3
        now[0] += model_catalog._ERROR_TTL + 1
        recovered = await discover_models(config, provider, **kwargs)
        assert recovered.source == "live" and not recovered.error
        assert len(requests) == 4


@pytest.mark.parametrize("provider", ["openrouter", "deepseek"])
async def test_cache_isolated_by_endpoint_and_resolved_credentials(config, provider):
    store = KeyStore("account-one")
    requests = []

    def respond(request):
        requests.append(request)
        model = f"{request.url.host}/{request.headers['authorization'].split()[-1]}"
        return httpx.Response(200, json={"data": [{"id": model}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        one = await discover_models(config, provider, api_key_store=store, client=client)
        store.key = "account-two"
        two = await discover_models(config, provider, api_key_store=store, client=client)
        assert one.models != two.models
        assert not any("account-one" in model.model for model in cached_models(config, provider))
        second_endpoint = provider_config(config, provider, base_url="https://second.test/v1")
        three = await discover_models(second_endpoint, provider, api_key_store=store, client=client)
        assert three.models != two.models
        assert len(requests) == 3


async def test_catalog_cache_does_not_retain_environment_or_resolved_credentials(config, monkeypatch):
    environment_key = "private-environment-credential"
    resolved_key = "private-keyring-credential"
    monkeypatch.setenv("CUSTOM_CATALOG_KEY", environment_key)
    config = provider_config(config, "openrouter", api_key_env="CUSTOM_CATALOG_KEY")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "vendor/model"}]})
    )) as client:
        result = await discover_models(config, "openrouter", api_key_store=KeyStore(resolved_key), client=client)
    assert result.source == "live"
    assert model_catalog._CACHE and model_catalog._ACTIVE_KEYS
    retained = repr((model_catalog._CACHE, model_catalog._ACTIVE_KEYS, model_catalog._SELECTED_CACHE))
    assert environment_key not in retained
    assert resolved_key not in retained


async def test_concurrent_first_loads_remain_available_to_offline_readers(config):
    requests = []
    both_started = asyncio.Event()

    async def respond(request):
        requests.append(request)
        if len(requests) == 2:
            both_started.set()
        await both_started.wait()
        return httpx.Response(200, json={"data": [{"id": f"{request.url.host}/new-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await asyncio.gather(*[
            discover_models(config, provider, api_key_store=KeyStore(), client=client)
            for provider in ("openai", "openrouter")
        ])
    assert any(item.model == "api.openai.com/new-model" for item in cached_models(config, "openai"))
    assert any(item.model == "openrouter.ai/new-model" for item in cached_models(config, "openrouter"))


async def test_concurrent_loads_for_same_account_share_one_request(config):
    requests = []

    async def respond(request):
        requests.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"data": [{"id": "vendor/new-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        results = await asyncio.gather(*[
            discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
            for _ in range(5)
        ])
    assert len(requests) == 1
    assert all(item.models == results[0].models for item in results)
    assert not model_catalog._LOCKS


def test_cached_suggestions_include_all_configured_ids_without_io(config, monkeypatch):
    config = replace(
        provider_config(config, "openrouter", default_model="provider/custom"),
        general=replace(config.general, default_provider="openrouter", default_model="general/custom"),
        telegram=replace(config.telegram, default_provider="openrouter", default_model="telegram/custom"),
        fallback=replace(config.fallback, routes=(FallbackRouteConfig("openrouter", "fallback/custom", ""),)),
    )

    def fail(*args, **kwargs):
        raise AssertionError("Offline suggestions must not perform I/O")

    monkeypatch.setattr(model_catalog.ApiKeyStore, "from_config", fail)
    monkeypatch.setattr(model_catalog.httpx.AsyncClient, "get", fail)
    assert {item.model for item in cached_models(config, "openrouter")} == {
        "provider/custom", "general/custom", "telegram/custom", "fallback/custom",
    }


async def test_configured_models_are_merged_again_when_using_cache(config):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "provider/discovered"}]})
    )) as client:
        await discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
        updated = provider_config(config, "openrouter", default_model="just/configured")
        result = await discover_models(updated, "openrouter", api_key_store=KeyStore(), client=client)
    assert result.source == "cache"
    assert {item.model for item in result.models} == {"just/configured", "provider/discovered"}


@pytest.mark.parametrize("payload", [None, [], {}, {"data": {}}, {"data": [None, {"id": False}]}])
async def test_bad_catalogs_fall_back_to_configuration(config, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        result = await discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
    assert result.source == "configured" and result.error
    assert [item.model for item in result.models] == ["openrouter/auto"]


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
async def test_missing_remote_key_avoids_request_and_local_server_needs_no_key(config, provider):
    requests = []

    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, provider, api_key_store=KeyStore(None), client=client)
        assert result.source == "configured" and "API key" in result.error
        assert not requests
        local = await discover_models(config, "llamacpp", api_key_store=KeyStore(None), client=client)
        assert local.source == "live" and len(requests) == 1


async def test_moonshot_uses_alternate_key_and_live_context_limits(config):
    class AlternateStore(KeyStore):
        def get_api_key(self, provider, env_var=None, *, aliases=()):
            super().get_api_key(provider, env_var, aliases=aliases)
            return ApiKeyLookup("platform-key" if env_var == "MOONSHOT_API_KEY" else None, "environment")

    config = replace(config, general=replace(config.general, default_provider="moonshot", default_model="k3"))
    store = AlternateStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [
            {"id": "k3", "context_length": 123456, "max_completion_tokens": 45000}
        ]})
    )) as client:
        result = await discover_models(config, "moonshot", api_key_store=store, client=client)
    assert result.source == "live"
    assert store.calls == [("moonshot", "KIMI_API_KEY", ("kimi",)), ("moonshot", "MOONSHOT_API_KEY", ("kimi",))]
    updated = apply_moonshot_model_limits(config)
    assert updated.agent.context_window_tokens == 123456
    assert updated.providers["moonshot"]["detected_max_completion_tokens"] == 45000
    assert updated.providers["moonshot"]["detected_context_source"] == "moonshot-models"


async def test_anthropic_repeated_cursor_is_bounded(config):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "same-model"}], "has_more": True, "last_id": "same"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "anthropic", api_key_store=KeyStore(), client=client)
    assert len(requests) == 2
    assert result.source == "configured" and "pagination" in result.error


async def test_timeout_returns_configuration_and_cancellation_propagates(config, monkeypatch):
    monkeypatch.setattr(model_catalog, "_DISCOVERY_TIMEOUT", 0.05)

    async def respond(request):
        await asyncio.sleep(20)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
        assert result.source == "configured" and "timed out" in result.error
        task = asyncio.create_task(discover_models(config, "openrouter", api_key_store=KeyStore(), client=client, refresh=True))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_codex_cli_handshake_pagination_hidden_models_and_account_cache(config, tmp_path):
    executable = tmp_path / "codex-test"
    requests_path = tmp_path / "requests.jsonl"
    executable.write_text(f"#!{Path(sys.executable).resolve()}\n" + '''
import json, sys
assert sys.argv[1:] == ['app-server']
initialized = False
for line in sys.stdin:
    message = json.loads(line)
    with open('requests.jsonl', 'a') as log:
        log.write(line)
    method = message['method']
    if method == 'initialize':
        assert message['params']['clientInfo']['name'] == 'libre_claw'
        result = {'userAgent': 'test'}
    elif method == 'initialized':
        initialized = True
        continue
    elif method == 'model/list':
        assert initialized
        assert message['params']['includeHidden'] is False
        if message['params'].get('cursor') == 'second-page':
            result = {'data': [{'id': 'second', 'model': 'codex-second'}], 'nextCursor': None}
        else:
            result = {'data': [
                {'id': 'first', 'model': 'codex-future', 'displayName': 'Future Codex'},
                {'id': 'helper', 'model': 'hidden-helper', 'hidden': True},
            ], 'nextCursor': 'second-page'}
    else:
        raise AssertionError(method)
    print(json.dumps({'id': message['id'], 'result': result}), flush=True)
''')
    executable.chmod(0o700)
    config = provider_config(config, "codex", executable=str(executable))
    result = await discover_models(config, "codex")
    assert result.source == "live" and not result.error
    assert "codex-future" in {item.model for item in result.models}
    assert "codex-second" in {item.model for item in result.models}
    assert "hidden-helper" not in {item.model for item in result.models}
    assert await discover_models(config, "codex") == replace(result, source="cache")
    messages = [json.loads(line) for line in requests_path.read_text().splitlines()]
    assert [item["method"] for item in messages] == ["initialize", "initialized", "model/list", "model/list"]
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text('{"account": "changed"}')
    assert (await discover_models(config, "codex")).source == "live"


async def test_missing_codex_executable_preserves_custom_model(config, tmp_path):
    config = provider_config(config, "codex", executable=str(tmp_path / "missing-cli"), default_model="custom-codex")
    result = await discover_models(config, "codex")
    assert result.source == "configured"
    assert "executable was not found" in result.error
    assert "custom-codex" in {item.model for item in result.models}


async def test_selected_model_metadata_and_manual_overrides(config):
    from libre_claw.providers.model_catalog import selected_model_info
    config = provider_config(config, "openrouter", model_capabilities={"future/new": {"supports_tools": False}})
    def respond(request):
        return httpx.Response(200, json={"data": [{"id": "future/new", "supported_parameters": ["tools", "reasoning"],
            "architecture": {"input_modalities": ["text", "image"]},
            "pricing": {"prompt": "0.000001", "completion": "0.000003"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await discover_models(config, "openrouter", api_key_store=KeyStore(), client=client)
    info = selected_model_info(config, "openrouter", "future/new")
    assert info.supports_tools is False and info.supports_vision is True
    assert info.input_cost_per_token == 0.000001 and info.output_cost_per_token == 0.000003
    assert selected_model_info(config, "openrouter", "arbitrary/unlisted").supports_tools is None


async def test_ollama_selected_show_is_cached_without_listing_every_model(config):
    from libre_claw.providers.model_catalog import discover_model, selected_model_info
    calls=[]
    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json={"models": [{"name": f"future-{i}"} for i in range(20)]})
        assert json.loads(request.content) == {"model":"future-8"}
        return httpx.Response(200, json={"capabilities":["completion", "tools", "vision"],
            "model_info":{"novel.context_length":65536}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await discover_models(config,"ollama",api_key_store=KeyStore(),client=client)
        assert calls == [("GET", "/api/tags")]
        info=await discover_model(config,"ollama","future-8",api_key_store=KeyStore(),client=client)
        assert info.supports_tools is True and info.supports_vision is True and info.supports_reasoning is False
        assert info.context_window_tokens == 65536
        await discover_model(config,"ollama","future-8",api_key_store=KeyStore(),client=client)
    assert calls == [("GET", "/api/tags"), ("POST", "/api/show")]
    assert selected_model_info(config,"ollama","future-9").supports_tools is None
    assert next(item for item in cached_models(config,"ollama") if item.model == "future-8").supports_tools is True
