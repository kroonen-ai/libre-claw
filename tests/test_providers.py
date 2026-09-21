# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from libre_claw.auth.api_keys import ApiKeyLookup
from libre_claw.config import load_config
from libre_claw.providers import ProviderConfigurationError, create_fallback_providers, create_provider
from libre_claw.providers.factory import _fallback_model
from libre_claw.providers.codex import CodexProvider
from libre_claw.providers.ollama import OllamaProvider
from libre_claw.providers.moonshot import MoonshotProvider
from libre_claw.providers.openai import OpenAIProvider
from libre_claw.providers.openrouter import OpenRouterProvider


class FakeApiKeyStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get_api_key(
        self,
        provider_name: str,
        env_var: str | None = None,
        *,
        aliases: tuple[str, ...] = (),
    ) -> ApiKeyLookup:
        del provider_name, env_var, aliases
        if self.value is None:
            return ApiKeyLookup(value=None, source="missing")
        return ApiKeyLookup(value=self.value, source="environment")


def test_provider_factory_fallback_models_match_public_defaults() -> None:
    assert _fallback_model("anthropic") == "claude-opus-5"
    assert _fallback_model("openrouter") == "openrouter/auto"
    assert _fallback_model("moonshot") == "k3"
    assert _fallback_model("codex") == "gpt-5.5"
    assert _fallback_model("ollama") == "qwen3.6:27b"


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_factory_inference_honors_custom_base_url(monkeypatch, tmp_path, provider_name):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, providers={
        **config.providers,
        provider_name: {**config.providers[provider_name], "base_url": "https://proxy.test/v1"},
    })
    provider = create_provider(config, provider_name=provider_name, api_key_store=FakeApiKeyStore("key"))
    assert provider.base_url == "https://proxy.test/v1"
    assert str(provider._client.base_url).rstrip("/") == "https://proxy.test/v1"


def test_factory_accepts_new_kimi_code_ids_without_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    provider = create_provider(
        config, provider_name="moonshot", model="kimi-future-code",
        api_key_store=FakeApiKeyStore("key"),
    )
    assert provider.model == "kimi-future-code"
    assert provider.service == "kimi_code"
    assert provider.base_url == "https://api.kimi.com/coding/v1"


def test_create_provider_requires_anthropic_api_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config()

    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY"):
        create_provider(config)


def test_create_provider_rejects_unsupported_provider(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "bogus"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="not supported"):
        create_provider(config)


def test_create_provider_requires_openai_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"openai\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        create_provider(config)


def test_create_provider_supports_openai(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"openai\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-5.5"


def test_create_provider_requires_openrouter_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"openrouter\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="OPENROUTER_API_KEY"):
        create_provider(config)


def test_create_provider_supports_openrouter(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"openrouter\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, OpenRouterProvider)
    assert provider.model == "openrouter/auto"
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.default_headers == {
        "HTTP-Referer": "https://libreclaw.sh",
        "X-OpenRouter-Title": "Libre Claw",
        "X-OpenRouter-Categories": "cli-agent,personal-agent",
    }


def test_create_provider_requires_moonshot_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"moonshot\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="KIMI_API_KEY"):
        create_provider(config)


def test_create_provider_supports_moonshot(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                "",
                "[providers.moonshot]",
                'default_model = "kimi-k3"',
                'base_url = "https://api.moonshot.ai/v1"',
                "max_tokens = 32768",
                'reasoning_effort = "high"',
                'thinking = "auto"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(
        config,
        api_key_store=FakeApiKeyStore("moonshot-key"),  # type: ignore[arg-type]
    )

    assert isinstance(provider, MoonshotProvider)
    assert provider.model == "k3"
    assert provider.base_url == "https://api.kimi.com/coding/v1"
    assert provider.service == "kimi_code"
    assert provider.reasoning_effort == "high"


def test_create_provider_preserves_explicit_moonshot_platform(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                'default_model = "platform-model"',
                "",
                "[providers.moonshot]",
                'service = "platform"',
                'default_model = "platform-model"',
                'base_url = "https://api.moonshot.cn/v1"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(config_path=config_path)

    provider = create_provider(
        config,
        api_key_store=FakeApiKeyStore("platform-key"),  # type: ignore[arg-type]
    )

    assert isinstance(provider, MoonshotProvider)
    assert provider.model == "platform-model"
    assert provider.base_url == "https://api.moonshot.cn/v1"
    assert provider.service == "platform"


def test_create_provider_rejects_disabled_thinking_for_kimi_k3(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                "",
                "[providers.moonshot]",
                'default_model = "kimi-k3"',
                'thinking = "disabled"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="requires thinking"):
        create_provider(
            config,
            api_key_store=FakeApiKeyStore("moonshot-key"),  # type: ignore[arg-type]
        )


def test_create_provider_caps_openrouter_max_tokens_from_detected_metadata(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "openrouter"',
                "",
                "[providers.openrouter]",
                'default_model = "minimax/minimax-m3"',
                "max_tokens = 16384",
                "detected_max_completion_tokens = 4096",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, OpenRouterProvider)
    assert provider.max_tokens == 4096


def test_create_fallback_providers_supports_provider_model_and_key_env(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[fallback]",
                "enabled = true",
                "",
                "[[fallback.routes]]",
                'provider = "openrouter"',
                'model = "deepseek/deepseek-v4-flash"',
                'api_key_env = "OPENROUTER_BACKUP_API_KEY"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_BACKUP_API_KEY", "backup-key")
    config = load_config(config_path=config_path)

    fallbacks = create_fallback_providers(config)

    assert len(fallbacks) == 1
    assert fallbacks[0].label == "openrouter:deepseek/deepseek-v4-flash via OPENROUTER_BACKUP_API_KEY"
    assert isinstance(fallbacks[0].provider, OpenRouterProvider)
    assert fallbacks[0].provider.model == "deepseek/deepseek-v4-flash"


def test_create_provider_supports_ollama_without_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"ollama\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3.6:27b"


def test_create_provider_supports_codex_without_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"codex\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, CodexProvider)
    assert provider.model == "gpt-5.5"


def test_create_provider_requires_ollama_cloud_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "ollama"',
                'default_model = "kimi-k2.6:cloud"',
                "",
                "[providers.ollama]",
                'base_url = "https://ollama.com"',
                'api_format = "ollama"',
                'api_key_env = "OLLAMA_API_KEY"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    with pytest.raises(ProviderConfigurationError, match="Ollama Cloud API key"):
        create_provider(config, api_key_store=FakeApiKeyStore(None))  # type: ignore[arg-type]


def test_create_provider_supports_ollama_cloud_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "ollama"',
                'default_model = "kimi-k2.6:cloud"',
                "",
                "[providers.ollama]",
                'base_url = "https://ollama.com"',
                'api_format = "ollama"',
                'api_key_env = "OLLAMA_API_KEY"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config, api_key_store=FakeApiKeyStore("cloud-key"))  # type: ignore[arg-type]

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "https://ollama.com"
    assert provider.model == "kimi-k2.6:cloud"
    assert provider.api_key == "cloud-key"
    assert provider.think is False


def test_create_provider_uses_low_thinking_for_gpt_oss(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "ollama"',
                'default_model = "gpt-oss:120b"',
                "",
                "[providers.ollama]",
                'base_url = "http://localhost:11434"',
                'think = "auto"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config, api_key_store=FakeApiKeyStore(None))  # type: ignore[arg-type]

    assert isinstance(provider, OllamaProvider)
    assert provider.think == "low"
