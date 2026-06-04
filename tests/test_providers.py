# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from libre_claw.auth.api_keys import ApiKeyLookup
from libre_claw.config import load_config
from libre_claw.providers import ProviderConfigurationError, create_fallback_providers, create_provider
from libre_claw.providers.factory import _fallback_model
from libre_claw.providers.anthropic_catalog import ANTHROPIC_MODEL_PRESETS
from libre_claw.providers.codex_catalog import CODEX_MODEL_PRESETS
from libre_claw.providers.codex import CodexProvider
from libre_claw.providers.ollama_catalog import (
    OLLAMA_CLOUD_MODEL_PRESETS,
    OLLAMA_MODEL_PRESETS,
)
from libre_claw.providers.ollama import OllamaProvider
from libre_claw.providers.openai import OpenAIProvider
from libre_claw.providers.openrouter import OpenRouterProvider
from libre_claw.providers.openrouter_catalog import OPENROUTER_MODEL_PRESETS


class FakeApiKeyStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get_api_key(self, provider_name: str, env_var: str | None = None) -> ApiKeyLookup:
        del provider_name, env_var
        if self.value is None:
            return ApiKeyLookup(value=None, source="missing")
        return ApiKeyLookup(value=self.value, source="environment")


def test_ollama_cloud_presets_include_current_library_names() -> None:
    preset_names = {preset.model for preset in OLLAMA_MODEL_PRESETS}
    cloud_names = {preset.model for preset in OLLAMA_CLOUD_MODEL_PRESETS}
    expected_cloud_names = {
        "kimi-k2.6:cloud",
        "qwen3.5:cloud",
        "qwen3.5:397b-cloud",
        "gemma4:31b-cloud",
        "glm-5.1:cloud",
        "minimax-m3:cloud",
        "minimax-m2.7:cloud",
        "nemotron-3-super:cloud",
        "glm-5:cloud",
        "minimax-m2.5:cloud",
        "glm-4.7:cloud",
        "gemini-3-flash-preview:cloud",
        "minimax-m2.1:cloud",
        "qwen3-coder-next:cloud",
        "deepseek-v3.2:cloud",
        "ministral-3:cloud",
        "devstral-small-2:cloud",
        "deepseek-v4-flash:cloud",
        "deepseek-v4-pro:cloud",
        "qwen3-next:cloud",
        "nemotron-3-nano:cloud",
        "rnj-1:cloud",
        "kimi-k2.5:cloud",
        "devstral-2:cloud",
        "mistral-large-3:cloud",
        "gpt-oss:120b",
        "gpt-oss:20b",
        "gpt-oss:120b-cloud",
        "gpt-oss:20b-cloud",
        "qwen3-vl:cloud",
        "qwen3-coder:cloud",
        "kimi-k2-thinking:cloud",
        "minimax-m2:cloud",
        "glm-4.6:cloud",
        "deepseek-v3.1:cloud",
        "cogito-2.1:cloud",
        "kimi-k2:cloud",
        "gemma3:27b-cloud",
    }

    assert expected_cloud_names <= preset_names
    assert expected_cloud_names <= cloud_names
    assert "qwen3.6:27b" not in cloud_names
    assert len(preset_names) == len(OLLAMA_MODEL_PRESETS)


def test_codex_oauth_presets_include_current_cli_model_names() -> None:
    preset_names = {preset.model for preset in CODEX_MODEL_PRESETS}

    assert "gpt-5.5" in preset_names
    assert "gpt-5.4" in preset_names
    assert "gpt-5.4-mini" in preset_names
    assert "gpt-5.3-codex" in preset_names
    assert "gpt-5.3-codex-spark" in preset_names
    assert "gpt-5.2" in preset_names
    assert "codex-auto-review" not in preset_names


def test_anthropic_presets_include_current_api_model_names() -> None:
    preset_names = {preset.model for preset in ANTHROPIC_MODEL_PRESETS}

    assert "claude-opus-4-8" in preset_names
    assert "claude-sonnet-4-6" in preset_names
    assert "claude-haiku-4-5-20251001" in preset_names
    assert "anthropic/claude-opus-4.8" not in preset_names


def test_openrouter_presets_include_recommended_models() -> None:
    preset_names = {preset.model for preset in OPENROUTER_MODEL_PRESETS}
    expected_models = {
        "deepseek/deepseek-v4-flash",
        "tencent/hy3-preview",
        "qwen/qwen3.7-max",
        "qwen/qwen3.7-plus",
        "deepseek/deepseek-v4-pro",
        "moonshotai/kimi-k2.6",
        "minimax/minimax-m2.7",
        "z-ai/glm-5.1",
        "xiaomi/mimo-v2.5-pro",
        "qwen/qwen3.6-plus",
        "anthropic/claude-opus-4.8",
        "anthropic/claude-sonnet-4.6",
        "minimax/minimax-m3",
        "google/gemini-3.5-flash",
        "openai/gpt-5.5",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "stepfun/step-3.5-flash",
        "openai/gpt-4o-mini",
        "openrouter/auto",
    }

    assert expected_models <= preset_names
    assert len(preset_names) == len(OPENROUTER_MODEL_PRESETS)


def test_provider_factory_fallback_models_match_public_defaults() -> None:
    assert _fallback_model("anthropic") == "claude-opus-4-8"
    assert _fallback_model("openrouter") == "openrouter/auto"
    assert _fallback_model("codex") == "gpt-5.5"
    assert _fallback_model("ollama") == "qwen3.6:27b"


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
