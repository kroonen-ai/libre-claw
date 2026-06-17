# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaModelPreset:
    model: str
    label: str


# Ollama Cloud names are intentionally kept as first-class presets because the
# direct https://ollama.com API is a remote Ollama host, not a local llama.cpp
# endpoint. Users can still type any exact name returned by /api/tags.
OLLAMA_CLOUD_MODEL_PRESETS: tuple[OllamaModelPreset, ...] = (
    OllamaModelPreset("kimi-k2.6:cloud", "Kimi K2.6 Cloud"),
    OllamaModelPreset("qwen3.5:cloud", "Qwen3.5 Cloud"),
    OllamaModelPreset("qwen3.5:397b-cloud", "Qwen3.5 397B Cloud"),
    OllamaModelPreset("gemma4:31b-cloud", "Gemma 4 31B Cloud"),
    OllamaModelPreset("glm-5.1:cloud", "GLM 5.1 Cloud"),
    OllamaModelPreset("glm-5.2:cloud", "GLM 5.2 Cloud"),
    OllamaModelPreset("minimax-m3:cloud", "MiniMax M3 Cloud"),
    OllamaModelPreset("minimax-m2.7:cloud", "MiniMax M2.7 Cloud"),
    OllamaModelPreset("nemotron-3-super:cloud", "Nemotron 3 Super Cloud"),
    OllamaModelPreset("glm-5:cloud", "GLM 5 Cloud"),
    OllamaModelPreset("minimax-m2.5:cloud", "MiniMax M2.5 Cloud"),
    OllamaModelPreset("glm-4.7:cloud", "GLM 4.7 Cloud"),
    OllamaModelPreset("gemini-3-flash-preview:cloud", "Gemini 3 Flash Preview Cloud"),
    OllamaModelPreset("minimax-m2.1:cloud", "MiniMax M2.1 Cloud"),
    OllamaModelPreset("qwen3-coder-next:cloud", "Qwen3 Coder Next Cloud"),
    OllamaModelPreset("deepseek-v3.2:cloud", "DeepSeek V3.2 Cloud"),
    OllamaModelPreset("ministral-3:cloud", "Ministral 3 Cloud"),
    OllamaModelPreset("devstral-small-2:cloud", "Devstral Small 2 Cloud"),
    OllamaModelPreset("deepseek-v4-flash:cloud", "DeepSeek V4 Flash Cloud"),
    OllamaModelPreset("deepseek-v4-pro:cloud", "DeepSeek V4 Pro Cloud"),
    OllamaModelPreset("qwen3-next:cloud", "Qwen3 Next Cloud"),
    OllamaModelPreset("nemotron-3-nano:cloud", "Nemotron 3 Nano Cloud"),
    OllamaModelPreset("rnj-1:cloud", "RNJ 1 Cloud"),
    OllamaModelPreset("kimi-k2.5:cloud", "Kimi K2.5 Cloud"),
    OllamaModelPreset("devstral-2:cloud", "Devstral 2 Cloud"),
    OllamaModelPreset("mistral-large-3:cloud", "Mistral Large 3 Cloud"),
    OllamaModelPreset("gpt-oss:120b", "GPT OSS 120B Direct API"),
    OllamaModelPreset("gpt-oss:20b", "GPT OSS 20B Direct API"),
    OllamaModelPreset("gpt-oss:120b-cloud", "GPT OSS 120B Cloud"),
    OllamaModelPreset("gpt-oss:20b-cloud", "GPT OSS 20B Cloud"),
    OllamaModelPreset("qwen3-vl:cloud", "Qwen3 VL Cloud"),
    OllamaModelPreset("qwen3-coder:cloud", "Qwen3 Coder Cloud"),
    OllamaModelPreset("kimi-k2-thinking:cloud", "Kimi K2 Thinking Cloud"),
    OllamaModelPreset("minimax-m2:cloud", "MiniMax M2 Cloud"),
    OllamaModelPreset("glm-4.6:cloud", "GLM 4.6 Cloud"),
    OllamaModelPreset("deepseek-v3.1:cloud", "DeepSeek V3.1 Cloud"),
    OllamaModelPreset("cogito-2.1:cloud", "Cogito 2.1 Cloud"),
    OllamaModelPreset("kimi-k2:cloud", "Kimi K2 Cloud"),
    OllamaModelPreset("gemma3:27b-cloud", "Gemma 3 27B Cloud"),
)

OLLAMA_LOCAL_MODEL_PRESETS: tuple[OllamaModelPreset, ...] = (
    OllamaModelPreset("qwen3.6:27b", "Qwen3.6 local daemon"),
    OllamaModelPreset("qwen3:32b", "Qwen3 local daemon"),
)

OLLAMA_MODEL_PRESETS: tuple[OllamaModelPreset, ...] = (
    OLLAMA_CLOUD_MODEL_PRESETS + OLLAMA_LOCAL_MODEL_PRESETS
)
