# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from libre_claw.auth.api_keys import ApiKeyStore
from libre_claw.config import LibreClawConfig
from libre_claw.providers.anthropic import AnthropicProvider
from libre_claw.providers.base import LLMProvider, ProviderConfigurationError
from libre_claw.providers.local import LocalProvider
from libre_claw.providers.openai import OpenAIProvider


def create_provider(config: LibreClawConfig, api_key_store: ApiKeyStore | None = None) -> LLMProvider:
    """Create the configured provider."""
    provider_name = config.general.default_provider.lower()
    provider_config = config.providers.get(provider_name)
    if provider_name not in {"anthropic", "openai", "local"}:
        msg = f"Provider '{provider_name}' is not supported. Use 'anthropic', 'openai', or 'local'."
        raise ProviderConfigurationError(msg)
    if provider_config is None:
        raise ProviderConfigurationError(f"Missing [providers.{provider_name}] configuration.")

    if provider_name == "local":
        return _create_local_provider(config, provider_config, api_key_store)

    api_key_env = _str_provider_value(provider_config, "api_key_env", _default_api_key_env(provider_name))
    store = api_key_store or ApiKeyStore.from_config(config.auth)
    api_key_lookup = store.get_api_key(provider_name, api_key_env)
    if not api_key_lookup.value:
        provider_label = "Anthropic" if provider_name == "anthropic" else "OpenAI"
        msg = (
            f"Missing {provider_label} API key. Set {api_key_env} or run "
            f"`libre-claw auth set-key {provider_name}` before sending a message."
        )
        raise ProviderConfigurationError(msg)

    model = _resolve_model(config, provider_name, provider_config)
    max_tokens = _int_provider_value(provider_config, "max_tokens", 16384)
    try:
        if provider_name == "anthropic":
            return AnthropicProvider(api_key=api_key_lookup.value, model=model, max_tokens=max_tokens)
        return OpenAIProvider(api_key=api_key_lookup.value, model=model, max_tokens=max_tokens)
    except RuntimeError as exc:
        raise ProviderConfigurationError(str(exc)) from exc


def _create_local_provider(
    config: LibreClawConfig,
    provider_config: Mapping[str, Any],
    api_key_store: ApiKeyStore | None,
) -> LocalProvider:
    api_key_env = _str_provider_value(provider_config, "api_key_env", "")
    store = api_key_store or ApiKeyStore.from_config(config.auth)
    api_key_lookup = store.get_api_key("local", api_key_env or None)
    api_key = api_key_lookup.value or "ollama"
    api_format = _str_provider_value(provider_config, "api_format", "ollama").lower()
    if api_format not in {"ollama", "openai"}:
        raise ProviderConfigurationError("[providers.local].api_format must be 'ollama' or 'openai'.")
    tool_mode = _str_provider_value(provider_config, "tool_mode", "auto").lower()
    if tool_mode not in {"auto", "native", "xml"}:
        raise ProviderConfigurationError("[providers.local].tool_mode must be 'auto', 'native', or 'xml'.")

    return LocalProvider(
        base_url=_str_provider_value(provider_config, "base_url", "http://localhost:11434"),
        model=_resolve_model(config, "local", provider_config),
        max_tokens=_int_provider_value(provider_config, "max_tokens", 16384),
        api_format=api_format,  # type: ignore[arg-type]
        api_key=api_key,
        supports_tools=_bool_provider_value(provider_config, "supports_tools", True),
        tool_mode=tool_mode,  # type: ignore[arg-type]
    )


def _str_provider_value(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if isinstance(value, str):
        return value
    return default


def _int_provider_value(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _bool_provider_value(config: Mapping[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    return default


def _default_api_key_env(provider_name: str) -> str:
    if provider_name == "openai":
        return "OPENAI_API_KEY"
    return "ANTHROPIC_API_KEY"


def _resolve_model(
    config: LibreClawConfig,
    provider_name: str,
    provider_config: Mapping[str, Any],
) -> str:
    provider_default = _str_provider_value(provider_config, "default_model", _fallback_model(provider_name))
    general_model = config.general.default_model
    other_provider_defaults = {
        str(other_config.get("default_model"))
        for name, other_config in config.providers.items()
        if name != provider_name and isinstance(other_config, Mapping) and other_config.get("default_model")
    }
    if not general_model or general_model in other_provider_defaults:
        return provider_default
    return general_model


def _fallback_model(provider_name: str) -> str:
    if provider_name == "openai":
        return "gpt-4o"
    if provider_name == "local":
        return "qwen3:32b"
    return "claude-sonnet-4-6"
