# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.core.review import pending_approvals, run_changes_text, run_plan_text
from libre_claw.core.runs import RunEvent, RunRecord


def test_run_plan_text_extracts_assistant_text_before_first_tool() -> None:
    events = [
        RunEvent(1, "t1", "user_message", {"content": "fix it"}),
        RunEvent(2, "t2", "assistant_delta", {"text": "I will inspect the failing test"}),
        RunEvent(3, "t3", "tool_call", {"id": "toolu_1", "name": "read_file"}),
        RunEvent(4, "t4", "assistant_delta", {"text": "later answer"}),
    ]

    assert run_plan_text(events) == "I will inspect the failing test\n"


def test_pending_approvals_ignores_resolved_prompts(tmp_path: Path) -> None:
    run = RunRecord("run-1", "blocked", "title", "chat", "openai", "gpt", "", "t1", "t2", tmp_path)
    events = [
        RunEvent(1, "t1", "permission_request", {"tool_call_id": "a", "name": "bash", "arguments": {"command": "pwd"}}),
        RunEvent(2, "t2", "permission_request", {"tool_call_id": "b", "name": "bash", "arguments": {"command": "date"}}),
        RunEvent(3, "t3", "permission_response", {"tool_call_id": "a", "resolution": "deny"}),
    ]

    approvals = pending_approvals(run, events)

    assert len(approvals) == 1
    assert approvals[0].tool_call_id == "b"
    assert approvals[0].arguments == {"command": "date"}


def test_run_changes_text_summarizes_new_events(tmp_path: Path) -> None:
    run = RunRecord("run-1", "done", "title", "chat", "openai", "gpt", "", "t1", "t2", tmp_path)
    events = [
        RunEvent(1, "t1", "user_message", {"content": "hi"}),
        RunEvent(2, "t2", "tool_result", {"name": "bash", "is_error": False}),
    ]

    text = run_changes_text(run, events, after_event_id=1)

    assert "New events: 1" in text
    assert "tool result: bash ok" in text
