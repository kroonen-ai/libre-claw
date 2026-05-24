# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from libre_claw.config import load_config
from libre_claw.providers import ProviderConfigurationError, create_provider
from libre_claw.providers.local import LocalProvider
from libre_claw.providers.openai import OpenAIProvider


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
    assert provider.model == "gpt-4o"


def test_create_provider_supports_local_without_api_key(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ndefault_provider = \"local\"\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config(config_path=config_path)

    provider = create_provider(config)

    assert isinstance(provider, LocalProvider)
    assert provider.model == "qwen3:32b"
