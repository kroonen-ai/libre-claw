# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnthropicModelPreset:
    model: str
    label: str
    description: str


# Direct Claude API model IDs from Anthropic's current model overview.
# Provider-prefixed forms such as anthropic/claude-opus-4.8 are for gateways
# like OpenRouter/Hermes, not Anthropic's first-party Messages API.
ANTHROPIC_MODEL_PRESETS: tuple[AnthropicModelPreset, ...] = (
    AnthropicModelPreset(
        "claude-opus-4-8",
        "Claude Opus 4.8",
        "Anthropic's most capable model for complex reasoning, long-horizon agentic coding, and high-autonomy work.",
    ),
    AnthropicModelPreset(
        "claude-sonnet-4-6",
        "Claude Sonnet 4.6",
        "Best blend of speed and intelligence for everyday agent work.",
    ),
    AnthropicModelPreset(
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5",
        "Fastest Claude model with near-frontier intelligence.",
    ),
)
