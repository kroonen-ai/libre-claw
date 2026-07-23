# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoonshotModelPreset:
    model: str
    label: str
    context_window_tokens: int
    max_output_tokens: int
    vision: bool = False


MOONSHOT_MODEL_PRESETS: tuple[MoonshotModelPreset, ...] = (
    MoonshotModelPreset(
        model="kimi-k3",
        label="Kimi K3",
        context_window_tokens=1_048_576,
        max_output_tokens=1_048_576,
        vision=True,
    ),
    MoonshotModelPreset(
        model="kimi-k2.7-code",
        label="Kimi K2.7 Code",
        context_window_tokens=262_144,
        max_output_tokens=32_768,
        vision=True,
    ),
    MoonshotModelPreset(
        model="kimi-k2.7-code-highspeed",
        label="Kimi K2.7 Code Highspeed",
        context_window_tokens=262_144,
        max_output_tokens=32_768,
        vision=True,
    ),
    MoonshotModelPreset(
        model="kimi-k2.6",
        label="Kimi K2.6",
        context_window_tokens=262_144,
        max_output_tokens=32_768,
        vision=True,
    ),
)


def moonshot_model_preset(model: str) -> MoonshotModelPreset | None:
    normalized = model.strip().lower()
    for preset in MOONSHOT_MODEL_PRESETS:
        if normalized == preset.model:
            return preset
    for preset in sorted(MOONSHOT_MODEL_PRESETS, key=lambda item: len(item.model), reverse=True):
        if normalized.startswith(f"{preset.model}-"):
            return preset
    return None
