# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.providers.moonshot_catalog import moonshot_model_preset


def apply_moonshot_model_limits(
    config: LibreClawConfig,
    *,
    model: str | None = None,
) -> LibreClawConfig:
    """Apply Moonshot's published model limits without a network lookup."""
    provider_config = config.providers.get("moonshot", {})
    if isinstance(provider_config, Mapping) and provider_config.get("auto_context_window") is False:
        return config

    selected_model = (model or _effective_moonshot_model(config)).strip()
    preset = moonshot_model_preset(selected_model)
    if preset is None:
        return config

    providers: dict[str, Mapping[str, Any]] = {}
    for name, value in config.providers.items():
        providers[name] = dict(value) if isinstance(value, Mapping) else value
    moonshot_config = dict(providers.get("moonshot", {}))
    moonshot_config.update(
        {
            "detected_context_window_tokens": preset.context_window_tokens,
            "detected_max_completion_tokens": preset.max_output_tokens,
            "detected_context_source": "moonshot-docs",
            "detected_context_model": selected_model,
        }
    )
    providers["moonshot"] = moonshot_config
    return replace(
        config,
        agent=replace(config.agent, context_window_tokens=preset.context_window_tokens),
        providers=providers,
    )


def _effective_moonshot_model(config: LibreClawConfig) -> str:
    provider_config = config.providers.get("moonshot", {})
    provider_default = "kimi-k3"
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
