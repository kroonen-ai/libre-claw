# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from libre_claw.kimi import canonical_kimi_code_model


@dataclass(frozen=True)
class MoonshotModelPreset:
    model: str
    label: str
    context_window_tokens: int
    max_output_tokens: int | None = None
    vision: bool = False


MOONSHOT_MODEL_PRESETS: tuple[MoonshotModelPreset, ...] = (
    MoonshotModelPreset(
        model="k3",
        label="Kimi K3",
        context_window_tokens=1_048_576,
        vision=True,
    ),
    MoonshotModelPreset(
        model="kimi-for-coding",
        label="Kimi K2.7 Code",
        context_window_tokens=262_144,
        vision=True,
    ),
    MoonshotModelPreset(
        model="kimi-for-coding-highspeed",
        label="Kimi K2.7 Code HighSpeed",
        context_window_tokens=262_144,
        vision=True,
    ),
)


def moonshot_model_preset(model: str) -> MoonshotModelPreset | None:
    normalized = canonical_kimi_code_model(model).lower()
    for preset in MOONSHOT_MODEL_PRESETS:
        if normalized == preset.model:
            return preset
    for preset in sorted(MOONSHOT_MODEL_PRESETS, key=lambda item: len(item.model), reverse=True):
        if normalized.startswith(f"{preset.model}-"):
            return preset
    return None
