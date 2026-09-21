# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Published model capabilities and explicit request controls; absent means unknown."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from libre_claw.providers.base import ProviderConfigurationError

if TYPE_CHECKING:
    from libre_claw.auth.api_keys import ApiKeyStore
    from libre_claw.config import LibreClawConfig
    from libre_claw.core.session import ChatMessage
    from libre_claw.providers.base import LLMProvider, ToolSchema
    from libre_claw.providers.model_catalog import ModelInfo


_BOOLEAN_FIELDS = ("supports_tools", "supports_vision", "supports_reasoning", "supports_temperature")
_PRICE_FIELDS = ("input_cost_per_token", "output_cost_per_token")
_LIMIT_FIELDS = ("context_window_tokens", "max_completion_tokens")


def _supported(value: Any) -> bool | None:
    if isinstance(value, Mapping):
        value = value.get("supported")
    return value if isinstance(value, bool) else None


def _price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _strings(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        return None
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def parse_capabilities(provider: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Parse advertised fields without deriving support from a model name."""
    result: dict[str, Any] = {}
    for field in _BOOLEAN_FIELDS:
        if isinstance(row.get(field), bool):
            result[field] = row[field]
    parameters = _strings(row.get("supported_parameters"))
    if parameters is not None:
        result.update(
            supports_tools="tools" in parameters,
            supports_reasoning=bool({"reasoning", "reasoning_effort"}.intersection(parameters)),
            supports_temperature="temperature" in parameters,
        )
    architecture = row.get("architecture")
    architecture = architecture if isinstance(architecture, Mapping) else {}
    inputs = _strings(architecture.get("input_modalities", row.get("inputModalities")))
    if inputs is not None:
        result["supports_vision"] = "image" in inputs
    capabilities = row.get("capabilities")
    if isinstance(capabilities, Mapping):
        for field, names in {
            "supports_tools": ("tools", "tool_use"),
            "supports_vision": ("image_input", "vision"),
            "supports_reasoning": ("thinking", "reasoning"),
            "supports_temperature": ("temperature",),
        }.items():
            for name in names:
                supported = _supported(capabilities.get(name))
                if supported is not None:
                    result[field] = supported
                    break
        effort = capabilities.get("effort")
        if isinstance(effort, Mapping):
            if _supported(effort) is False:
                result["supported_reasoning_efforts"] = ()
            else:
                levels = tuple(name for name, value in effort.items() if name != "supported" and _supported(value) is True)
                if levels:
                    result["supported_reasoning_efforts"] = levels
    elif provider == "ollama" and (advertised := _strings(capabilities)) is not None:
        result.update(supports_tools="tools" in advertised, supports_vision="vision" in advertised,
                      supports_reasoning="thinking" in advertised)
    efforts = row.get("supportedReasoningEfforts", row.get("supported_reasoning_efforts"))
    if isinstance(efforts, list) and all(isinstance(item, Mapping) for item in efforts):
        efforts = [item.get("reasoningEffort") for item in efforts]
    if (levels := _strings(efforts)) is not None:
        result["supported_reasoning_efforts"] = levels
        result.setdefault("supports_reasoning", bool(levels))
    pricing = row.get("pricing")
    if isinstance(pricing, Mapping):
        for field, key in zip(_PRICE_FIELDS, ("prompt", "completion")):
            if (price := _price(pricing.get(key))) is not None:
                result[field] = price
    for field in _PRICE_FIELDS:
        if (price := _price(row.get(field))) is not None:
            result[field] = price
    if result:
        result["capability_source"] = "provider"
    return result


def apply_model_overrides(info: ModelInfo, settings: Mapping[str, Any]) -> ModelInfo:
    overrides = settings.get("model_capabilities")
    if not isinstance(overrides, Mapping):
        return info
    raw = overrides.get(info.model)
    if not isinstance(raw, Mapping):
        return info
    values: dict[str, Any] = {}
    for field in (*_BOOLEAN_FIELDS, *_PRICE_FIELDS, *_LIMIT_FIELDS, "supported_reasoning_efforts"):
        if field not in raw:
            continue
        value = raw[field]
        if value == "unknown":
            values[field] = None
        elif field in _BOOLEAN_FIELDS and isinstance(value, bool):
            values[field] = value
        elif field in _PRICE_FIELDS and (price := _price(value)) is not None:
            values[field] = price
        elif field in _LIMIT_FIELDS and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            values[field] = value
        elif field == "supported_reasoning_efforts" and (levels := _strings(value)) is not None:
            values[field] = levels
        else:
            raise ProviderConfigurationError(f"Invalid capability override for {info.provider}:{info.model}: {field}.")
    return replace(info, **values, capability_source="configured") if values else info


def bind_model_capabilities(
    provider: LLMProvider, config: LibreClawConfig, name: str, *, api_key_store: ApiKeyStore | None = None,
) -> LLMProvider:
    provider._capability_config = config
    provider._capability_provider = name
    provider.auto_context_window = config.providers.get(name, {}).get("auto_context_window") is not False
    async def ensure_model_info() -> ModelInfo:
        from libre_claw.providers.model_catalog import discover_model
        return await discover_model(config, name, getattr(provider, "model", ""), api_key_store=api_key_store)
    provider.ensure_model_info = ensure_model_info
    return provider


def has_images(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("type") in {"image", "image_url", "input_image"} or any(has_images(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_images(item) for item in value)
    return False


def prepare_request(
    provider: LLMProvider, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema] | None,
    max_tokens: int | None,
) -> tuple[Sequence[ToolSchema] | None, int | None]:
    info = provider.model_info
    if info is None:
        return tools, max_tokens
    if info.supports_vision is False and any(has_images(message.content) for message in messages):
        raise ProviderConfigurationError(f"{info.provider}:{info.model} does not support image input. Choose a vision model.")
    if info.supports_tools is False:
        tools = None
    limit = max_tokens or getattr(provider, "max_tokens", None)
    context = info.context_window_tokens if getattr(provider, "auto_context_window", True) else None
    ceilings = [value for value in (info.max_completion_tokens, context) if value is not None]
    if ceilings:
        limit = min([*ceilings, *([limit] if limit is not None else [])])
    return tools, limit


def configured_reasoning_effort(provider: LLMProvider) -> str | None:
    info = provider.model_info
    config = getattr(provider, "_capability_config", None)
    name = getattr(provider, "_capability_provider", "")
    settings = config.providers.get(name, {}) if config is not None else {}
    effort = settings.get("reasoning_effort", getattr(provider, "reasoning_effort", None))
    if not isinstance(effort, str) or not effort.strip() or info is None:
        return None
    effort = effort.strip()
    if info.supports_reasoning is False:
        return None
    choices = info.supported_reasoning_efforts
    if choices is not None and effort not in choices:
        raise ProviderConfigurationError(f"{info.provider}:{info.model} does not support reasoning effort '{effort}'. Supported: {', '.join(choices) or 'none'}.")
    return effort if info.supports_reasoning is True or choices else None


def apply_reasoning_request(provider: LLMProvider, request: dict[str, Any], *, anthropic: bool = False) -> None:
    info = provider.model_info
    if info is None:
        return
    if info.supports_reasoning is False:
        request.pop("reasoning_effort", None)
        for field in ("thinking", "reasoning"):
            request.pop(field, None)
            if isinstance(request.get("extra_body"), dict):
                request["extra_body"].pop(field, None)
        return
    effort = configured_reasoning_effort(provider)
    if effort is None:
        return
    if anthropic:
        request.setdefault("output_config", {})["effort"] = effort
    elif info.provider == "openrouter":
        request.setdefault("extra_body", {}).setdefault("reasoning", {})["effort"] = effort
    else:
        request["reasoning_effort"] = effort
