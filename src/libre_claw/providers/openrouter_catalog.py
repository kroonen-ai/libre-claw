# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenRouterModelPreset:
    model: str
    label: str
    description: str


OPENROUTER_MODEL_PRESETS: tuple[OpenRouterModelPreset, ...] = (
    OpenRouterModelPreset(
        "deepseek/deepseek-v4-flash",
        "DeepSeek V4 Flash",
        "Fast DeepSeek V4 coding and agent preset.",
    ),
    OpenRouterModelPreset(
        "tencent/hy3-preview",
        "Tencent Hunyuan 3 Preview",
        "Tencent preview model for broad agent tasks.",
    ),
    OpenRouterModelPreset(
        "qwen/qwen3.7-max",
        "Qwen3.7 Max",
        "Primary high-capacity coding preset.",
    ),
    OpenRouterModelPreset(
        "qwen/qwen3.7-plus",
        "Qwen3.7 Plus",
        "Qwen plus-tier coding and agent preset through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "deepseek/deepseek-v4-pro",
        "DeepSeek V4 Pro",
        "Higher-capacity DeepSeek V4 agent preset.",
    ),
    OpenRouterModelPreset(
        "moonshotai/kimi-k2.6",
        "Kimi K2.6",
        "Moonshot Kimi K2.6 coding and long-horizon agent preset.",
    ),
    OpenRouterModelPreset(
        "minimax/minimax-m2.7",
        "MiniMax M2.7",
        "MiniMax coding, agentic, and productivity preset.",
    ),
    OpenRouterModelPreset(
        "z-ai/glm-5.1",
        "GLM 5.1",
        "Z.ai GLM agentic engineering preset.",
    ),
    OpenRouterModelPreset(
        "xiaomi/mimo-v2.5-pro",
        "MiMo V2.5 Pro",
        "Xiaomi MiMo pro model preset.",
    ),
    OpenRouterModelPreset(
        "qwen/qwen3.6-plus",
        "Qwen3.6 Plus",
        "Qwen plus-tier coding and reasoning preset.",
    ),
    OpenRouterModelPreset(
        "anthropic/claude-opus-4.8",
        "Claude Opus 4.8",
        "Anthropic Opus through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "anthropic/claude-sonnet-4.6",
        "Claude Sonnet 4.6",
        "Anthropic Sonnet through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "minimax/minimax-m3",
        "MiniMax M3",
        "MiniMax M3 coding, agentic, and productivity preset through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "google/gemini-3.5-flash",
        "Gemini 3.5 Flash",
        "Google fast multimodal/general preset through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "openai/gpt-5.5",
        "GPT-5.5",
        "OpenAI flagship preset through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "nvidia/nemotron-3-super-120b-a12b:free",
        "Nemotron 3 Super 120B Free",
        "NVIDIA Nemotron free-tier preset.",
    ),
    OpenRouterModelPreset(
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "Nemotron 3 Ultra 550B Free",
        "NVIDIA Nemotron Ultra free-tier preset.",
    ),
    OpenRouterModelPreset(
        "stepfun/step-3.5-flash",
        "Step 3.5 Flash",
        "StepFun fast model preset.",
    ),
    OpenRouterModelPreset(
        "openai/gpt-4o-mini",
        "GPT-4o Mini",
        "Small OpenAI fallback preset through OpenRouter.",
    ),
    OpenRouterModelPreset(
        "openrouter/auto",
        "OpenRouter Auto",
        "Let OpenRouter route across available providers.",
    ),
)
