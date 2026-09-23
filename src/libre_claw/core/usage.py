# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_claw.core.runs import RunEvent, RunRecord, RunStore
from libre_claw.providers.base import Usage
from libre_claw.providers.openrouter import (
    OPENROUTER_APP_TITLE,
    OPENROUTER_CATEGORIES,
    OPENROUTER_DOCS_URL,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_RANKING_TARGETS,
)


OPENROUTER_ANALYTICS_URL = f"https://openrouter.ai/apps?url={OPENROUTER_HTTP_REFERER}"


@dataclass(frozen=True)
class UsageRecord:
    run_id: str
    title: str
    state: str
    provider: str
    model: str
    surface: str
    timestamp: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float | None = None
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def usage(self) -> Usage:
        return Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost=self.cost,
            cache_write_tokens=self.cache_write_tokens,
        )


@dataclass(frozen=True)
class UsageGroup:
    name: str
    requests: int
    runs: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost: float | None
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


EventFingerprint = tuple[int, int, int, int, int] | None


def _event_fingerprint(path: Path) -> EventFingerprint:
    try:
        stat = (path / "events.jsonl").stat()
    except FileNotFoundError:
        return None
    # Include identity and ctime, so replacing/truncating a file cannot reuse a
    # cache entry merely because its length or restored mtime happens to match.
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class UsageHistoryCache:
    """Read-only reporting cache shared by a daemon's dashboard clients.

    Only normalized usage records are retained, never prompts or tool output.
    Filesystem fingerprints detect changes from other RunStore instances,
    including the CLI and TUI. A lock coalesces simultaneous cold requests;
    unchanged histories are not read or parsed again.
    """

    def __init__(self, run_store: RunStore, *, max_entries: int = 1000) -> None:
        if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries < 1:
            raise ValueError("Usage cache capacity must be a positive integer.")
        self.run_store = run_store
        self.max_entries = max_entries
        self._lock = asyncio.Lock()
        self._entries: OrderedDict[str, tuple[RunRecord, EventFingerprint, tuple[UsageRecord, ...]]] = OrderedDict()

    async def load(self, *, provider: str | None = None, limit: int = 250) -> list[UsageRecord]:
        async with self._lock:
            runs = await self.run_store.list_runs(limit=max(1, limit))
            provider_filter = provider.lower() if provider else None
            selected = [run for run in runs if not provider_filter or run.provider.lower() == provider_filter]
            fingerprints = await asyncio.to_thread(
                lambda: {run.run_id: _event_fingerprint(run.path) for run in selected},
            )
            records: list[UsageRecord] = []
            for run in selected:
                fingerprint = fingerprints[run.run_id]
                cached = self._entries.get(run.run_id)
                if cached is not None and cached[:2] == (run, fingerprint):
                    self._entries.move_to_end(run.run_id)
                    records.extend(cached[2])
                    continue
                self._entries.pop(run.run_id, None)
                try:
                    events = await self.run_store.load_events(run.run_id)
                except ValueError:
                    # A run can disappear between directory listing and read.
                    continue
                loaded = tuple(usage_records_from_run(run, events))
                records.extend(loaded)
                if await asyncio.to_thread(_event_fingerprint, run.path) == fingerprint:
                    self._entries[run.run_id] = (run, fingerprint, loaded)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            records.sort(key=lambda record: record.timestamp, reverse=True)
            return records


async def load_usage_records(
    run_store: RunStore,
    *,
    provider: str | None = None,
    limit: int = 250,
) -> list[UsageRecord]:
    runs = await run_store.list_runs(limit=max(1, limit))
    records: list[UsageRecord] = []
    provider_filter = provider.lower() if provider else None
    for run in runs:
        if provider_filter and run.provider.lower() != provider_filter:
            continue
        try:
            events = await run_store.load_events(run.run_id)
        except ValueError:
            continue
        records.extend(usage_records_from_run(run, events))
    records.sort(key=lambda record: record.timestamp, reverse=True)
    return records


def usage_records_from_run(run: RunRecord, events: list[RunEvent]) -> list[UsageRecord]:
    surface = _surface_for_run(run, events)
    records: list[UsageRecord] = []
    for event in events:
        if event.type != "usage":
            continue
        data = event.data
        records.append(
            UsageRecord(
                run_id=run.run_id,
                title=run.title,
                state=run.state,
                provider=str(data.get("provider") or run.provider),
                model=str(data.get("model") or run.model),
                surface=str(data.get("surface") or surface),
                timestamp=event.timestamp,
                input_tokens=_int_value(data.get("input_tokens")),
                output_tokens=_int_value(data.get("output_tokens")),
                cached_tokens=_int_value(data.get("cached_tokens")),
                reasoning_tokens=_int_value(data.get("reasoning_tokens")),
                cost=_cost_value(data.get("cost")),
                cache_write_tokens=_int_value(data.get("cache_write_tokens")),
            )
        )
    return records


def usage_report_text(records: list[UsageRecord], *, provider: str = "openrouter") -> str:
    label = "OpenRouter" if provider == "openrouter" else provider.capitalize() if provider else "Provider"
    if not records:
        lines = [f"No {label} usage has been recorded yet."]
        if provider == "openrouter":
            lines.extend(["", openrouter_attribution_text(), "", openrouter_model_presets_text()])
        return "\n".join(lines).strip()

    total = _group_records("total", records)
    lines = [
        f"{label} usage",
        f"- Requests: {total.requests}",
        f"- Runs: {total.runs}",
        f"- Tokens: {total.total_tokens} total ({total.input_tokens} input, {total.output_tokens} output)",
    ]
    if total.cached_tokens:
        lines.append(f"- Cached input: {total.cached_tokens}")
        if total.input_tokens:
            lines.append(f"- Reported cache reuse: {_cache_hit_ratio(total.cached_tokens, total.input_tokens):.1%} of input")
    if total.cache_write_tokens:
        lines.append(f"- Cache writes: {total.cache_write_tokens}")
    if total.reasoning_tokens:
        lines.append(f"- Reasoning output: {total.reasoning_tokens}")
    lines.append(f"- Cost: {_format_cost(total.cost)}")
    if provider == "openrouter":
        lines.append(f"- Analytics: {OPENROUTER_ANALYTICS_URL}")

    lines.extend(["", "By model:"])
    lines.extend(_group_lines(group_usage_by(records, "model")))
    lines.extend(["", "By surface:"])
    lines.extend(_group_lines(group_usage_by(records, "surface")))
    lines.extend(["", "Recent runs:"])
    lines.extend(_recent_run_lines(records))
    if provider == "openrouter":
        lines.extend(["", "Attribution:", _openrouter_attribution_line()])
    return "\n".join(lines)


def group_usage_by(records: list[UsageRecord], field: str) -> list[UsageGroup]:
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        key = getattr(record, field)
        grouped[str(key)].append(record)
    groups = [_group_records(name, values) for name, values in grouped.items()]
    groups.sort(key=lambda group: group.total_tokens, reverse=True)
    return groups


def openrouter_attribution_text() -> str:
    return "\n".join(
        [
            "OpenRouter attribution verification:",
            f"- HTTP-Referer: {OPENROUTER_HTTP_REFERER}",
            f"- X-OpenRouter-Title: {OPENROUTER_APP_TITLE}",
            f"- X-OpenRouter-Categories: {OPENROUTER_CATEGORIES}",
            f"- Ranking targets: {', '.join(OPENROUTER_RANKING_TARGETS)}",
            f"- Docs: {OPENROUTER_DOCS_URL}",
            f"- Analytics: {OPENROUTER_ANALYTICS_URL}",
            "- Status: configured for Libre Claw app attribution on every OpenRouter request.",
            (
                "- Note: OpenRouter documents attribution headers for site, title, and categories; "
                "add the docs URL in the OpenRouter app profile if their UI exposes it."
            ),
        ]
    )


def openrouter_model_presets_text() -> str:
    return "\n".join(
        [
            "Use /models openrouter to discover available models.",
            "Add --refresh to reload the provider catalog.",
            "Select any ID with /model openrouter:<model-id> --global.",
            "Use /usage openrouter to view tokens, cost, and runs.",
        ]
    )


def usage_summary_payload(records: list[UsageRecord]) -> dict[str, Any]:
    total = _group_records("total", records)
    return {
        "requests": total.requests,
        "runs": total.runs,
        "input_tokens": total.input_tokens,
        "output_tokens": total.output_tokens,
        "cached_tokens": total.cached_tokens,
        "cache_write_tokens": total.cache_write_tokens,
        "cache_hit_ratio": _cache_hit_ratio(total.cached_tokens, total.input_tokens),
        "reasoning_tokens": total.reasoning_tokens,
        "total_tokens": total.total_tokens,
        "cost": total.cost,
        "by_model": [_group_payload(group) for group in group_usage_by(records, "model")],
        "by_surface": [_group_payload(group) for group in group_usage_by(records, "surface")],
    }


def usage_record_payload(record: UsageRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "title": record.title,
        "state": record.state,
        "provider": record.provider,
        "model": record.model,
        "surface": record.surface,
        "timestamp": record.timestamp,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cached_tokens": record.cached_tokens,
        "cache_write_tokens": record.cache_write_tokens,
        "cache_hit_ratio": _cache_hit_ratio(record.cached_tokens, record.input_tokens),
        "reasoning_tokens": record.reasoning_tokens,
        "total_tokens": record.total_tokens,
        "cost": record.cost,
    }


def openrouter_attribution_payload() -> dict[str, str]:
    return {
        "http_referer": OPENROUTER_HTTP_REFERER,
        "title": OPENROUTER_APP_TITLE,
        "categories": OPENROUTER_CATEGORIES,
        "ranking_targets": ", ".join(OPENROUTER_RANKING_TARGETS),
        "docs_url": OPENROUTER_DOCS_URL,
        "analytics_url": OPENROUTER_ANALYTICS_URL,
    }


def _surface_for_run(run: RunRecord, events: list[RunEvent]) -> str:
    for event in events:
        if event.type == "automation_triggered":
            route = str(event.data.get("route") or "report")
            return f"automation:{route}"
    for event in events:
        if event.type == "run_started" and event.data.get("surface"):
            return str(event.data["surface"])
    return run.kind or "chat"


def _group_records(name: str, records: list[UsageRecord]) -> UsageGroup:
    costs = [record.cost for record in records if record.cost is not None]
    return UsageGroup(
        name=name,
        requests=len(records),
        runs=len({record.run_id for record in records}),
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
        cached_tokens=sum(record.cached_tokens for record in records),
        cache_write_tokens=sum(record.cache_write_tokens for record in records),
        reasoning_tokens=sum(record.reasoning_tokens for record in records),
        cost=sum(costs) if costs and len(costs) == len(records) else None,
    )


def _group_lines(groups: list[UsageGroup]) -> list[str]:
    if not groups:
        return ["- none"]
    return [
        f"- {group.name}: {group.total_tokens} tokens across {group.runs} run(s), "
        f"{group.requests} request(s), {_format_cost(group.cost)}"
        for group in groups[:10]
    ]


def _recent_run_lines(records: list[UsageRecord]) -> list[str]:
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        grouped[record.run_id].append(record)
    run_groups = [_group_records(run_id, values) for run_id, values in grouped.items()]
    run_groups.sort(key=lambda group: max(record.timestamp for record in grouped[group.name]), reverse=True)
    lines: list[str] = []
    for group in run_groups[:8]:
        sample = grouped[group.name][0]
        lines.append(
            f"- {group.name} [{sample.surface}] {sample.model}: "
            f"{group.total_tokens} tokens, {_format_cost(group.cost)} - {sample.title}"
        )
    return lines or ["- none"]


def _openrouter_attribution_line() -> str:
    return (
        f"`HTTP-Referer={OPENROUTER_HTTP_REFERER}`, "
        f"`X-OpenRouter-Title={OPENROUTER_APP_TITLE}`, "
        f"`X-OpenRouter-Categories={OPENROUTER_CATEGORIES}`"
    )


def _group_payload(group: UsageGroup) -> dict[str, Any]:
    return {
        "name": group.name,
        "requests": group.requests,
        "runs": group.runs,
        "input_tokens": group.input_tokens,
        "output_tokens": group.output_tokens,
        "cached_tokens": group.cached_tokens,
        "cache_write_tokens": group.cache_write_tokens,
        "cache_hit_ratio": _cache_hit_ratio(group.cached_tokens, group.input_tokens),
        "reasoning_tokens": group.reasoning_tokens,
        "total_tokens": group.total_tokens,
        "cost": group.cost,
    }


def _cache_hit_ratio(cached_tokens: int, input_tokens: int) -> float | None:
    return min(1.0, max(0, cached_tokens) / input_tokens) if input_tokens > 0 else None


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _cost_value(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _format_cost(cost: float | None) -> str:
    if cost is None:
        return "unknown"
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.6f}"
    return f"${cost:.2f}"
