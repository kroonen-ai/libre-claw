# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from libre_claw.core.runs import RunStore
from libre_claw.core.usage import (
    OPENROUTER_ANALYTICS_URL,
    load_usage_records,
    openrouter_attribution_text,
    openrouter_model_presets_text,
    usage_report_text,
    usage_summary_payload,
)


async def test_usage_records_roll_up_openrouter_by_model_surface_and_run(tmp_path) -> None:
    store = RunStore(tmp_path / "runs")
    run = await store.create_run(
        "ship p7",
        kind="chat",
        provider="openrouter",
        model="qwen/qwen3.7-max",
    )
    await store.append_event(run.run_id, "run_started", {"surface": "tui:chat"})
    await store.append_event(
        run.run_id,
        "usage",
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_tokens": 2,
            "reasoning_tokens": 1,
            "cost": 0.00012,
        },
    )

    records = await load_usage_records(store, provider="openrouter")
    text = usage_report_text(records, provider="openrouter")
    payload = usage_summary_payload(records)

    assert len(records) == 1
    assert records[0].surface == "tui:chat"
    assert records[0].model == "qwen/qwen3.7-max"
    assert payload["total_tokens"] == 15
    assert payload["by_model"][0]["name"] == "qwen/qwen3.7-max"
    assert payload["by_surface"][0]["name"] == "tui:chat"
    assert "OpenRouter usage" in text
    assert OPENROUTER_ANALYTICS_URL in text


async def test_usage_records_detect_automation_surface(tmp_path) -> None:
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("scheduled", kind="chat", provider="openrouter", model="openrouter/auto")
    await store.append_event(run.run_id, "automation_triggered", {"route": "telegram"})
    await store.append_event(run.run_id, "usage", {"input_tokens": 1, "output_tokens": 2})

    records = await load_usage_records(store, provider="openrouter")

    assert records[0].surface == "automation:telegram"


def test_openrouter_attribution_and_presets_text_are_actionable() -> None:
    attribution = openrouter_attribution_text()
    presets = openrouter_model_presets_text()

    assert "HTTP-Referer: https://libreclaw.sh" in attribution
    assert "X-OpenRouter-Categories: cli-agent,personal-agent" in attribution
    assert "Ranking targets: Productivity, Coding Agents, Personal Agents, CLI Agents" in attribution
    assert "Docs: https://libreclaw.sh/docs/" in attribution
    assert "OpenRouter app profile" in attribution
    assert "/model openrouter:deepseek/deepseek-v4-flash --global" in presets
    assert "/model openrouter:qwen/qwen3.7-max --global" in presets
    assert "/model openrouter:minimax/minimax-m3 --global" in presets
    assert "/model openrouter:nvidia/nemotron-3-ultra-550b-a55b:free --global" in presets
    assert "/model openrouter:anthropic/claude-opus-4.8 --global" in presets
    assert "/usage openrouter" in presets
