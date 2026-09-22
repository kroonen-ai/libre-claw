# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from libre_claw.config import _build_config, _load_default_config
from libre_claw.core.session import ChatMessage
from libre_claw.providers.base import ProviderConfigurationError, ProviderError
from libre_claw.providers.capabilities import apply_model_overrides, bind_model_capabilities, parse_capabilities
from libre_claw.providers import codex as codex_provider, model_catalog
from libre_claw.providers.openrouter import OpenRouterProvider
from libre_claw.providers.anthropic import AnthropicProvider
from libre_claw.providers.local import LocalProvider


class OpenAIClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request):
        self.calls.append(request)
        async def chunks():
            if False:
                yield None
        return chunks()


def bind(provider, name, info, **settings):
    config = _build_config(_load_default_config(), ())
    overrides = {key: value for key, value in vars(info).items()
                 if key not in {"provider", "model", "label", "capability_source"} and value is not None}
    config = replace(config, providers={**config.providers, name: {
        **config.providers[name], **settings, "model_capabilities": {info.model: overrides},
    }})
    return bind_model_capabilities(provider, config, name)


def test_published_capabilities_are_independent_of_model_names():
    fields = parse_capabilities("openrouter", {
        "id": "unreleased/anything", "supported_parameters": ["tools", "reasoning", "temperature"],
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0", "completion": "0.000003"},
    })
    assert fields == {
        "supports_tools": True, "supports_reasoning": True, "supports_temperature": True,
        "supports_vision": True, "input_cost_per_token": 0.0,
        "output_cost_per_token": 0.000003, "capability_source": "provider",
    }
    assert parse_capabilities("openai", {"id": "vision-reasoning-tool-free"}) == {}
    assert parse_capabilities("openrouter", {"pricing": {"prompt": "NaN", "completion": "-1"}}) == {}
    assert parse_capabilities("openrouter", {"pricing": {"prompt": "Infinity"}}) == {}


def test_anthropic_and_codex_capability_shapes_preserve_unknown_tools():
    fields = parse_capabilities("anthropic", {"capabilities": {
        "image_input": {"supported": False}, "thinking": {"supported": True},
        "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": False}},
    }})
    assert fields["supports_vision"] is False
    assert fields["supported_reasoning_efforts"] == ("low",)
    assert "supports_tools" not in fields
    fields = parse_capabilities("codex", {
        "inputModalities": ["text", "image"],
        "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": "Standard"}],
    })
    assert fields["supported_reasoning_efforts"] == ("medium",)
    assert fields["supports_reasoning"] is True
    assert fields["supports_vision"] is True


def test_model_override_precedence_and_explicit_unknown():
    original = model_catalog.ModelInfo("openrouter", "future/model", "Future", supports_tools=True, input_cost_per_token=0.1)
    info = apply_model_overrides(original, {"model_capabilities": {"future/model": {
        "supports_tools": False, "input_cost_per_token": "unknown", "max_completion_tokens": 512,
    }}})
    assert info.supports_tools is False
    assert info.input_cost_per_token is None
    assert info.max_completion_tokens == 512
    assert info.capability_source == "configured"
    with pytest.raises(ProviderConfigurationError, match="supports_tools"):
        apply_model_overrides(original, {"model_capabilities": {"future/model": {"supports_tools": "yes"}}})


@pytest.mark.parametrize("known", [True, False])
async def test_openrouter_omits_known_unsupported_parameters_but_preserves_unknown(known):
    client = OpenAIClient()
    provider = OpenRouterProvider("key", "future/model", 1000, client=client)
    provider.model_info = model_catalog.ModelInfo("openrouter", "future/model", "Future", max_completion_tokens=123 if known else None,
                                  supports_tools=False if known else None,
                                  supports_temperature=False if known else None)
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[{"type":"text", "text":"Hi"}])],
              tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}])]
    assert not any(isinstance(event, ProviderError) for event in events)
    assert ("tools" in client.calls[0]) is (not known)
    assert ("temperature" in client.calls[0]) is (not known)
    assert client.calls[0]["max_tokens"] == (123 if known else 1000)


async def test_known_nonvision_model_rejects_images_before_request():
    client = OpenAIClient()
    provider = OpenRouterProvider("key", "future/model", 1000, client=client)
    provider.model_info = model_catalog.ModelInfo("openrouter", "future/model", "Future", supports_vision=False)
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[
        {"type": "image", "media_type": "image/png", "data": "encoded"},
    ])])]
    assert len(events) == 1 and isinstance(events[0], ProviderError)
    assert "does not support image input" in events[0].message
    assert not client.calls


async def test_reasoning_effort_validated_and_sent_for_new_openrouter_model():
    client = OpenAIClient()
    provider = OpenRouterProvider("key", "future/model", 1000, client=client)
    info = model_catalog.ModelInfo("openrouter", "future/model", "Future", supports_reasoning=True,
                     supported_reasoning_efforts=("low", "medium"))
    bind(provider, "openrouter", info, reasoning_effort="medium")
    [event async for event in provider.complete([ChatMessage(role="user", content=[{"type":"text", "text":"Hi"}])])]
    assert client.calls[0]["extra_body"]["reasoning"]["effort"] == "medium"
    bind(provider, "openrouter", info, reasoning_effort="ultra")
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[{"type":"text", "text":"Hi"}])])]
    assert isinstance(events[0], ProviderError) and "ultra" in events[0].message
    assert len(client.calls) == 1


async def test_anthropic_request_respects_published_limits_and_effort():
    calls = []
    class Stream:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
    def stream(**request):
        calls.append(request)
        return Stream()
    provider = AnthropicProvider("key", "new-model", 5000, client=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    bind(provider, "anthropic", model_catalog.ModelInfo("anthropic", "new-model", "New", max_completion_tokens=500,
         supports_tools=False, supports_reasoning=True, supported_reasoning_efforts=("high",)), reasoning_effort="high")
    events = [event async for event in provider.complete([ChatMessage(role="user", content=[{"type":"text", "text":"Hi"}])], tools=[{"name":"read"}])]
    assert not any(isinstance(event, ProviderError) for event in events)
    assert calls[0]["max_tokens"] == 500
    assert calls[0]["output_config"] == {"effort": "high"}
    assert "tools" not in calls[0]


async def test_ollama_request_omits_unsupported_thinking_and_tools():
    provider = LocalProvider("http://local.test", "new-model", 900, think="high")
    provider.model_info = model_catalog.ModelInfo("ollama", "new-model", "New", max_completion_tokens=400,
                                   supports_tools=False, supports_reasoning=False, supports_temperature=False)
    calls=[]
    async def stream(request):
        calls.append(request)
        yield {"done": True, "message": {"content": "Hello"}}
    provider._stream_ollama_request = stream
    [event async for event in provider.complete([ChatMessage(role="user", content=[{"type":"text", "text":"Hi"}])], tools=[{"name":"read"}])]
    assert "think" not in calls[0] and "tools" not in calls[0]
    assert calls[0]["options"] == {"num_predict":400}


async def test_codex_forwards_image_and_known_reasoning_then_cleans_temp_files(monkeypatch, tmp_path):
    import base64
    from pathlib import Path
    from libre_claw.auth.codex import CodexCommandResult, CodexStatus
    provider = codex_provider.CodexProvider("future/model", tmp_path, replay_delay=0)
    bind(provider, "codex", model_catalog.ModelInfo("codex", "future/model", "Future", context_window_tokens=65536,
         supports_vision=True, supports_reasoning=True, supported_reasoning_efforts=("medium",)), reasoning_effort="medium")
    captured=[]
    async def status(*args): return CodexStatus(available=True,logged_in=True,detail="ok")
    async def stream(args, input_text=None):
        path=Path(args[args.index("--image")+1]);captured.append(path)
        assert path.read_bytes() == b"image-bytes"
        assert 'model_reasoning_effort="medium"' in args
        assert "model_context_window=65536" in args
        assert "Image attached" in input_text
        yield CodexCommandResult(args=tuple(args),exit_code=0,stdout="ok",stderr="")
    monkeypatch.setattr(codex_provider,"codex_status",status)
    monkeypatch.setattr(codex_provider,"stream_codex_command",stream)
    events=[event async for event in provider.complete([ChatMessage(role="user",content=[
        {"type":"image","data":base64.b64encode(b"image-bytes").decode(),"media_type":"image/png"},
    ])])]
    assert not any(isinstance(event,ProviderError) for event in events)
    assert captured and not captured[0].exists()


async def test_codex_rejects_known_nonvision_model_without_spawning(monkeypatch, tmp_path):
    provider = codex_provider.CodexProvider("future/text",tmp_path)
    provider.model_info=model_catalog.ModelInfo("codex","future/text","Text",supports_vision=False)
    events=[event async for event in provider.complete([ChatMessage(role="user",content=[{"type":"image","data":"a"}])])]
    assert len(events)==1 and isinstance(events[0],ProviderError)
    assert "does not support image input" in events[0].message


async def test_factory_discovery_callback_preserves_fallback_credentials(monkeypatch):
    from libre_claw.providers.factory import create_provider
    from libre_claw.auth.api_keys import ApiKeyLookup
    config = _build_config(_load_default_config(), ())
    class KeyStore:
        def get_api_key(self,*args,**kwargs): return ApiKeyLookup('test-key','environment')
    store = KeyStore()
    calls=[]
    async def discover(config_arg,name,model,**kwargs):
        calls.append((config_arg,name,model,kwargs))
        return model_catalog.ModelInfo(name,model,model)
    monkeypatch.setattr(model_catalog,'discover_model',discover)
    provider = create_provider(config,store,provider_name='openrouter',model='future/fallback',api_key_env='BACKUP_KEY')
    assert not calls
    await provider.ensure_model_info()
    assert calls[0][0].providers['openrouter']['api_key_env'] == 'BACKUP_KEY'
    assert calls[0][1:3] == ('openrouter','future/fallback')
    assert calls[0][3]['api_key_store'] is store
    assert config.providers['openrouter']['api_key_env'] != 'BACKUP_KEY'


@pytest.mark.parametrize('name',['codex','ollama','llamacpp'])
def test_explicit_model_override_wins_for_current_local_or_cli_provider(name):
    from libre_claw.providers.factory import create_provider
    from libre_claw.auth.api_keys import ApiKeyLookup
    config=_build_config(_load_default_config(),())
    config=replace(config,general=replace(config.general,default_provider=name,default_model='current-model'))
    class KeyStore:
        def get_api_key(self,*args,**kwargs): return ApiKeyLookup(None,'missing')
    provider=create_provider(config,KeyStore(),provider_name=name,model='new-arbitrary-model')
    assert provider.model == 'new-arbitrary-model'
    assert provider.model_info.model == 'new-arbitrary-model'
