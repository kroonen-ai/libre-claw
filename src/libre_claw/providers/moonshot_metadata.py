# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.kimi import canonical_kimi_code_model, moonshot_service
from libre_claw.providers.model_catalog import cached_models
from libre_claw.providers.moonshot_catalog import moonshot_model_preset


def apply_moonshot_model_limits(
    config: LibreClawConfig,
    *,
    model: str | None = None,
) -> LibreClawConfig:
    """Apply discovered limits, or historical Kimi limits, without network I/O."""
    provider_config = config.providers.get("moonshot", {})
    if isinstance(provider_config, Mapping) and provider_config.get("auto_context_window") is False:
        return config

    selected_model = model or _effective_moonshot_model(config)
    is_kimi_code = moonshot_service(provider_config) == "kimi_code"
    if is_kimi_code:
        selected_model = canonical_kimi_code_model(selected_model)
    discovered = next(
        (item for item in cached_models(config, "moonshot") if item.model == selected_model), None
    )
    if discovered is not None and discovered.context_window_tokens is not None:
        context_window = discovered.context_window_tokens
        max_output = discovered.max_completion_tokens
        source = "moonshot-models"
    else:
        preset = moonshot_model_preset(selected_model) if is_kimi_code else None
        if preset is None:
            return config
        context_window = preset.context_window_tokens
        max_output = preset.max_output_tokens
        source = "kimi-code-docs"

    providers: dict[str, Mapping[str, Any]] = {}
    for name, value in config.providers.items():
        providers[name] = dict(value) if isinstance(value, Mapping) else value
    moonshot_config = dict(providers.get("moonshot", {}))
    moonshot_config.update(
        {
            "detected_context_window_tokens": context_window,
            "detected_context_source": source,
            "detected_context_model": selected_model,
        }
    )
    moonshot_config.pop("detected_max_completion_tokens", None)
    if max_output is not None:
        moonshot_config["detected_max_completion_tokens"] = max_output
    providers["moonshot"] = moonshot_config
    return replace(
        config,
        agent=replace(config.agent, context_window_tokens=context_window),
        providers=providers,
    )


def _effective_moonshot_model(config: LibreClawConfig) -> str:
    provider_config = config.providers.get("moonshot", {})
    provider_default = "k3"
    if isinstance(provider_config, Mapping):
        configured_default = provider_config.get("default_model")
        if isinstance(configured_default, str) and configured_default.strip():
            provider_default = configured_default.strip()

    general_model = config.general.default_model.strip()
    other_provider_defaults = {
        str(other_config.get("default_model")).strip()
        for name, other_config in config.providers.items()
        if name != "moonshot"
        and isinstance(other_config, Mapping)
        and other_config.get("default_model")
    }
    if not general_model or general_model in other_provider_defaults:
        return provider_default
    return general_model
