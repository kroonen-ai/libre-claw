# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodexModelPreset:
    model: str
    label: str
    description: str


# Source: https://developers.openai.com/codex/models
# Keep deprecated models and hidden internal helpers out of the picker.
CODEX_MODEL_PRESETS: tuple[CodexModelPreset, ...] = (
    CodexModelPreset(
        "gpt-5.6-sol",
        "GPT-5.6 Sol",
        "Flagship model for complex coding, computer use, research, and cybersecurity.",
    ),
    CodexModelPreset(
        "gpt-5.6-terra",
        "GPT-5.6 Terra",
        "Balanced everyday model with strong reasoning and tool use at a lower cost.",
    ),
    CodexModelPreset(
        "gpt-5.6-luna",
        "GPT-5.6 Luna",
        "Fast, affordable model for clear, repeatable, high-volume tasks.",
    ),
    CodexModelPreset(
        "gpt-5.5",
        "GPT-5.5",
        "Previous-generation frontier model for complex coding and knowledge work.",
    ),
    CodexModelPreset(
        "gpt-5.3-codex-spark",
        "GPT-5.3 Codex Spark",
        "Text-only, near-instant coding research preview for ChatGPT Pro users.",
    ),
    CodexModelPreset(
        "gpt-5.4",
        "GPT-5.4",
        "Frontier model for professional work, coding, reasoning, and tool use.",
    ),
    CodexModelPreset(
        "gpt-5.4-mini",
        "GPT-5.4 Mini",
        "Fast, efficient mini model for responsive coding tasks and subagents.",
    ),
)
