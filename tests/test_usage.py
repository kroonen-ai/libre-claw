# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from libre_claw.core.runs import RunStore
from libre_claw.providers.base import Usage, combine_usage
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
    assert "/models openrouter" in presets
    assert "--refresh" in presets
    assert "/model openrouter:<model-id> --global" in presets
    assert "/usage openrouter" in presets


def test_combined_usage_keeps_unreported_cost_unknown() -> None:
    known = Usage(input_tokens=5, output_tokens=2, cost=0.01)
    unknown = Usage(input_tokens=7, output_tokens=3)
    for left, right in ((known, unknown), (unknown, known)):
        combined = combine_usage(left, right)
        assert combined.total_tokens == 17
        assert combined.cost is None
    assert combine_usage(Usage(cost=0.0), known).cost == 0.01
    assert combine_usage(known, known).cost == 0.02
    assert combine_usage(None, known) is known


async def test_cost_rollups_do_not_present_partial_cost_as_total(tmp_path) -> None:
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Mixed accounting", kind="chat", provider="openrouter", model="lab/model")
    await store.append_event(run.run_id, "usage", {"input_tokens": 10, "output_tokens": 2, "cost": 0.02})
    await store.append_event(run.run_id, "usage", {"input_tokens": 20, "output_tokens": 4})
    records = await load_usage_records(store)
    payload = usage_summary_payload(records)
    assert payload["total_tokens"] == 36
    assert payload["cost"] is None
    assert payload["by_model"][0]["cost"] is None
    assert "Cost: unknown" in usage_report_text(records)
