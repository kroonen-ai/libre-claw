# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from libre_claw.auth.api_keys import ApiKeyStore
from libre_claw.config import LibreClawConfig
from libre_claw.kimi import (
    KIMI_CODE_BASE_URL,
    KIMI_CODE_MODEL_IDS,
    is_kimi_code_model,
    moonshot_service,
    normalize_moonshot_selection,
)
from libre_claw.providers.anthropic import AnthropicProvider
from libre_claw.providers.base import LLMProvider, ProviderConfigurationError
from libre_claw.providers.codex import CodexProvider
from libre_claw.providers.local import OllamaThink
from libre_claw.providers.moonshot import (
    MoonshotProvider,
    MoonshotReasoningEffort,
    MoonshotThinking,
)
from libre_claw.providers.ollama import OllamaProvider
from libre_claw.providers.openai import OpenAIProvider
from libre_claw.providers.openrouter import OpenRouterProvider


@dataclass(frozen=True)
class ProviderFallback:
    label: str
    provider: LLMProvider


def create_provider(
    config: LibreClawConfig,
    api_key_store: ApiKeyStore | None = None,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
) -> LLMProvider:
    """Create the configured provider."""
    resolved_provider_name = _canonical_provider_name(provider_name or config.general.default_provider)
    raw_provider_config = config.providers.get(resolved_provider_name)
    provider_config = dict(raw_provider_config) if isinstance(raw_provider_config, Mapping) else None
    if api_key_env:
        provider_config = provider_config or {}
        provider_config["api_key_env"] = api_key_env
    if model:
        provider_config = provider_config or {}
        provider_config["default_model"] = model
    if resolved_provider_name not in {
        "anthropic",
        "openai",
        "openrouter",
        "moonshot",
        "ollama",
        "codex",
    }:
        msg = (
            f"Provider '{resolved_provider_name}' is not supported. "
            "Use 'anthropic', 'openai', 'openrouter', 'moonshot', 'ollama', or 'codex'."
        )
        raise ProviderConfigurationError(msg)
    if provider_config is None:
        raise ProviderConfigurationError(f"Missing [providers.{resolved_provider_name}] configuration.")

    if resolved_provider_name == "codex":
        return _create_codex_provider(config, provider_config)

    if resolved_provider_name == "ollama":
        return _create_ollama_provider(config, provider_config, api_key_store)

    resolved_model = model or _resolve_model(config, resolved_provider_name, provider_config)
    if resolved_provider_name == "moonshot":
        resolved_model, provider_config = normalize_moonshot_selection(
            provider_config,
            resolved_model,
        )
        if moonshot_service(provider_config) == "kimi_code" and not is_kimi_code_model(
            resolved_model
        ):
            models = ", ".join(KIMI_CODE_MODEL_IDS)
            raise ProviderConfigurationError(
                f"Unknown Kimi Code model '{resolved_model}'. Use {models}, or set "
                "[providers.moonshot].service = 'platform' for a Moonshot Platform model."
            )

    resolved_api_key_env = _str_provider_value(
        provider_config,
        "api_key_env",
        _default_api_key_env(resolved_provider_name),
    )
    store = api_key_store or ApiKeyStore.from_config(config.auth)
    if resolved_provider_name == "moonshot":
        api_key_lookup = store.get_api_key(
            resolved_provider_name,
            resolved_api_key_env,
            aliases=("kimi",),
        )
        alternate_env = (
            "MOONSHOT_API_KEY"
            if resolved_api_key_env != "MOONSHOT_API_KEY"
            else "KIMI_API_KEY"
        )
        if not api_key_lookup.value:
            api_key_lookup = store.get_api_key(
                resolved_provider_name,
                alternate_env,
                aliases=("kimi",),
            )
    else:
        api_key_lookup = store.get_api_key(resolved_provider_name, resolved_api_key_env)
    if not api_key_lookup.value:
        if resolved_provider_name == "moonshot":
            msg = (
                "Missing Kimi/Moonshot API key. Set KIMI_API_KEY or MOONSHOT_API_KEY, "
                "or run `libre-claw auth set-key moonshot` before sending a message."
            )
        else:
            provider_label = _provider_label(resolved_provider_name)
            msg = (
                f"Missing {provider_label} API key. Set {resolved_api_key_env} or run "
                f"`libre-claw auth set-key {resolved_provider_name}` before sending a message."
            )
        raise ProviderConfigurationError(msg)

    max_tokens = _provider_max_tokens(provider_config)
    try:
        if resolved_provider_name == "anthropic":
            return AnthropicProvider(api_key=api_key_lookup.value, model=resolved_model, max_tokens=max_tokens)
        if resolved_provider_name == "openrouter":
            return OpenRouterProvider(
                api_key=api_key_lookup.value,
                model=resolved_model,
                max_tokens=max_tokens,
                base_url=_str_provider_value(provider_config, "base_url", "https://openrouter.ai/api/v1"),
            )
        if resolved_provider_name == "moonshot":
            thinking = _moonshot_thinking_value(provider_config)
            if thinking == "disabled" and (
                resolved_model.lower() == "k3"
                or resolved_model.lower().startswith("kimi-k3")
                or resolved_model.lower().startswith("kimi-for-coding")
                or resolved_model.lower().startswith("kimi-k2.7")
            ):
                raise ProviderConfigurationError(
                    f"{resolved_model} requires thinking; set [providers.moonshot].thinking = 'auto'."
                )
            return MoonshotProvider(
                api_key=api_key_lookup.value,
                model=resolved_model,
                max_tokens=max_tokens,
                base_url=_str_provider_value(
                    provider_config,
                    "base_url",
                    KIMI_CODE_BASE_URL,
                ),
                service=moonshot_service(provider_config),
                reasoning_effort=_moonshot_reasoning_effort(provider_config),
                thinking=thinking,
            )
        return OpenAIProvider(api_key=api_key_lookup.value, model=resolved_model, max_tokens=max_tokens)
    except RuntimeError as exc:
        raise ProviderConfigurationError(str(exc)) from exc


def create_fallback_providers(
    config: LibreClawConfig,
    api_key_store: ApiKeyStore | None = None,
) -> tuple[ProviderFallback, ...]:
    """Create configured fallback provider candidates without breaking primary setup."""
    if not config.fallback.enabled:
        return ()

    fallbacks: list[ProviderFallback] = []
    for route in config.fallback.routes:
        try:
            provider = create_provider(
                config,
                api_key_store=api_key_store,
                provider_name=route.provider,
                model=route.model or None,
                api_key_env=route.api_key_env or None,
            )
        except ProviderConfigurationError:
            continue
        model = route.model or _resolve_model(
            config,
            _canonical_provider_name(route.provider),
            config.providers.get(_canonical_provider_name(route.provider), {}),
        )
        label = f"{_canonical_provider_name(route.provider)}:{model}"
        if route.api_key_env:
            label += f" via {route.api_key_env}"
        fallbacks.append(ProviderFallback(label=label, provider=provider))
    return tuple(fallbacks)


def _create_codex_provider(config: LibreClawConfig, provider_config: Mapping[str, Any]) -> CodexProvider:
    sandbox = _str_provider_value(provider_config, "sandbox", "workspace-write")
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ProviderConfigurationError(
            "[providers.codex].sandbox must be 'read-only', 'workspace-write', or 'danger-full-access'."
        )
    approval_policy = _str_provider_value(provider_config, "approval_policy", "never")
    if approval_policy not in {"untrusted", "on-failure", "on-request", "never"}:
        raise ProviderConfigurationError(
            "[providers.codex].approval_policy must be 'untrusted', 'on-failure', 'on-request', or 'never'."
        )
    return CodexProvider(
        model=_resolve_model(config, "codex", provider_config),
        working_directory=config.general.working_directory,
        executable=_str_provider_value(provider_config, "executable", "codex"),
        sandbox=sandbox,
        approval_policy=approval_policy,
        timeout=_int_provider_value(provider_config, "timeout", 900),
    )


def _create_ollama_provider(
    config: LibreClawConfig,
    provider_config: Mapping[str, Any],
    api_key_store: ApiKeyStore | None,
) -> OllamaProvider:
    api_key_env = _str_provider_value(provider_config, "api_key_env", "")
    store = api_key_store or ApiKeyStore.from_config(config.auth)
    api_key_lookup = store.get_api_key(
        "ollama",
        api_key_env or None,
        aliases=("local",),
    )
    base_url = _str_provider_value(provider_config, "base_url", "http://localhost:11434")
    if _is_ollama_cloud_url(base_url) and not api_key_lookup.value:
        msg = (
            "Missing Ollama Cloud API key. Set OLLAMA_API_KEY or run "
            "`libre-claw auth set-key ollama` before using https://ollama.com."
        )
        raise ProviderConfigurationError(msg)
    api_key = api_key_lookup.value or "ollama"
    api_format = _str_provider_value(provider_config, "api_format", "ollama").lower()
    if api_format not in {"ollama", "openai"}:
        raise ProviderConfigurationError("[providers.ollama].api_format must be 'ollama' or 'openai'.")
    tool_mode = _str_provider_value(provider_config, "tool_mode", "auto").lower()
    if tool_mode not in {"auto", "native", "xml"}:
        raise ProviderConfigurationError("[providers.ollama].tool_mode must be 'auto', 'native', or 'xml'.")

    resolved_model = _resolve_model(config, "ollama", provider_config)
    return OllamaProvider(
        base_url=base_url,
        model=resolved_model,
        max_tokens=_int_provider_value(provider_config, "max_tokens", 16384),
        api_format=api_format,  # type: ignore[arg-type]
        api_key=api_key,
        supports_tools=_bool_provider_value(provider_config, "supports_tools", True),
        tool_mode=tool_mode,  # type: ignore[arg-type]
        think=_ollama_think_value(provider_config, resolved_model),
    )


def _canonical_provider_name(provider_name: str) -> str:
    normalized = provider_name.lower()
    if normalized == "local":
        return "ollama"
    return normalized


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


def _ollama_think_value(config: Mapping[str, Any], model: str) -> OllamaThink:
    value = config.get("think", "auto")
    if value == "auto":
        model_name = model.rsplit("/", maxsplit=1)[-1].lower()
        return "low" if model_name.startswith("gpt-oss") else False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"low", "medium", "high"}:
        return value.lower()  # type: ignore[return-value]
    raise ProviderConfigurationError(
        "[providers.ollama].think must be 'auto', true, false, 'low', 'medium', or 'high'."
    )


def _provider_max_tokens(config: Mapping[str, Any]) -> int:
    configured = _int_provider_value(config, "max_tokens", 16384)
    detected = _int_provider_value(config, "detected_max_completion_tokens", 0)
    if detected > 0:
        return min(configured, detected)
    return configured


def _moonshot_reasoning_effort(
    config: Mapping[str, Any],
) -> MoonshotReasoningEffort:
    value = _str_provider_value(config, "reasoning_effort", "high").lower()
    if value not in {"low", "high", "max"}:
        raise ProviderConfigurationError(
            "[providers.moonshot].reasoning_effort must be 'low', 'high', or 'max'."
        )
    return value  # type: ignore[return-value]


def _moonshot_thinking_value(config: Mapping[str, Any]) -> MoonshotThinking:
    value = _str_provider_value(config, "thinking", "auto").lower()
    if value not in {"auto", "enabled", "disabled"}:
        raise ProviderConfigurationError(
            "[providers.moonshot].thinking must be 'auto', 'enabled', or 'disabled'."
        )
    return value  # type: ignore[return-value]


def _default_api_key_env(provider_name: str) -> str:
    if provider_name == "openrouter":
        return "OPENROUTER_API_KEY"
    if provider_name == "openai":
        return "OPENAI_API_KEY"
    if provider_name == "moonshot":
        return "KIMI_API_KEY"
    return "ANTHROPIC_API_KEY"


def _provider_label(provider_name: str) -> str:
    labels = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "openrouter": "OpenRouter",
        "moonshot": "Moonshot AI",
        "codex": "Codex",
    }
    return labels.get(provider_name, provider_name)


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
    if provider_name == "openrouter":
        return "openrouter/auto"
    if provider_name == "moonshot":
        return "k3"
    if provider_name == "codex":
        return "gpt-5.5"
    if provider_name == "ollama":
        return "qwen3.6:27b"
    return "claude-opus-4-8"


def _is_ollama_cloud_url(base_url: str) -> bool:
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    return parsed.hostname == "ollama.com"
