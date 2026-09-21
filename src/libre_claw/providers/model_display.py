# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from libre_claw.providers.model_catalog import ModelInfo


def model_capability_summary(info: ModelInfo) -> str:
    def flag(value: bool | None) -> str:
        return "unknown" if value is None else "yes" if value else "no"

    values = [f"tools: {flag(info.supports_tools)}", f"images: {flag(info.supports_vision)}", f"reasoning: {flag(info.supports_reasoning)}"]
    values.append(f"context: {info.context_window_tokens:,}" if info.context_window_tokens else "context: unknown")
    if info.supported_reasoning_efforts:
        values.append("effort: " + "/".join(info.supported_reasoning_efforts))
    if info.input_cost_per_token is None or info.output_cost_per_token is None:
        values.append("price: unknown")
    else:
        values.append(f"USD/M tokens in/out: {info.input_cost_per_token * 1_000_000:g}/{info.output_cost_per_token * 1_000_000:g}")
    return " | ".join(values)
