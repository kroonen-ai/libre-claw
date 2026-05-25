# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from libre_claw.core.runs import RunEvent, RunRecord


RUN_ARTIFACT_NAMES = ("events.jsonl", "plan.md", "summary.md", "verification.md", "diff.patch")


@dataclass(frozen=True)
class PendingApproval:
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    timestamp: str


def run_plan_text(events: list[RunEvent]) -> str:
    """Extract the initial visible assistant plan from a run timeline."""
    chunks: list[str] = []
    for event in events:
        if event.type in {"tool_call", "permission_request", "tool_result", "goal_judge", "goal_complete"}:
            break
        if event.type == "assistant_delta":
            chunks.append(str(event.data.get("text", "")))
    text = "".join(chunks).strip()
    if text:
        return text[:8000].rstrip() + ("\n" if len(text) <= 8000 else "\n\n[Plan truncated]\n")

    request = _first_request(events)
    if request:
        return f"No separate plan was recorded before tool use.\n\nInitial request:\n{request}\n"
    return "No plan was recorded for this run.\n"


def pending_approvals(run: RunRecord, events: list[RunEvent]) -> list[PendingApproval]:
    requests: dict[str, RunEvent] = {}
    resolved: set[str] = set()
    for event in events:
        if event.type == "permission_request":
            tool_call_id = str(event.data.get("tool_call_id", ""))
            if tool_call_id:
                requests[tool_call_id] = event
        if event.type == "permission_response":
            tool_call_id = str(event.data.get("tool_call_id", ""))
            if tool_call_id:
                resolved.add(tool_call_id)
    return [
        PendingApproval(
            run_id=run.run_id,
            tool_call_id=tool_call_id,
            tool_name=str(event.data.get("name", "tool")),
            arguments=dict(event.data.get("arguments", {})) if isinstance(event.data.get("arguments"), dict) else {},
            timestamp=event.timestamp,
        )
        for tool_call_id, event in requests.items()
        if tool_call_id not in resolved
    ]


def run_changes_text(run: RunRecord, events: list[RunEvent], after_event_id: int) -> str:
    fresh = [event for event in events if event.event_id > after_event_id]
    if not fresh:
        return f"No new events for {run.run_id} since your last review."

    lines = [
        f"Changes for {run.run_id} since event {after_event_id}:",
        f"State now: {run.state}",
        f"New events: {len(fresh)}",
        "",
    ]
    for event in fresh[-20:]:
        lines.append(f"- #{event.event_id} {event.timestamp} {_event_summary(event)}")
    omitted = len(fresh) - 20
    if omitted > 0:
        lines.append(f"- ... {omitted} earlier new event(s) omitted.")
    return "\n".join(lines)


def _first_request(events: list[RunEvent]) -> str:
    for event in events:
        if event.type == "user_message":
            return str(event.data.get("content", "")).strip()
        if event.type == "user_goal":
            return "Goal: " + str(event.data.get("goal", "")).strip()
    return ""


def _event_summary(event: RunEvent) -> str:
    if event.type == "assistant_delta":
        text = " ".join(str(event.data.get("text", "")).split())
        return f"assistant: {_truncate(text, 100)}"
    if event.type == "tool_call":
        return f"tool call: {event.data.get('name', 'tool')} ({event.data.get('id', '')})"
    if event.type == "tool_result":
        status = "error" if event.data.get("is_error") else "ok"
        return f"tool result: {event.data.get('name', 'tool')} {status}"
    if event.type == "permission_request":
        return f"approval needed: {event.data.get('name', 'tool')} ({event.data.get('tool_call_id', '')})"
    if event.type == "permission_response":
        return f"approval response: {event.data.get('resolution', '')}"
    if event.type == "error":
        return "error: " + _truncate(str(event.data.get("message", "")), 120)
    if event.type == "run_finished":
        return f"run finished: {event.data.get('state', '')}"
    if event.type == "usage":
        total = int(event.data.get("input_tokens", 0) or 0) + int(event.data.get("output_tokens", 0) or 0)
        return f"usage: {total} tokens"
    return event.type


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."
