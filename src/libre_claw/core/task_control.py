# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from libre_claw.core.session import Session


def update_plan(session: Session, argument: str) -> str:
    """Apply the same editable plan commands on every user surface."""
    parts = argument.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else "show"
    value = parts[1].strip() if len(parts) > 1 else ""
    previous_steps = {step["text"] for step in session.plan_steps}
    if action in {"on", "off"}:
        session.mode = "plan" if action == "on" else "default"
    elif action == "set":
        steps = [step.strip() for step in value.replace("\n", ";").split(";") if step.strip()]
        if not steps or len(steps) > 100:
            raise ValueError("Use /plan set first step; second step (up to 100 steps).")
        session.plan_steps = [{"text": step[:2000], "status": "pending"} for step in steps]
    elif action == "add":
        if not value or len(session.plan_steps) >= 100:
            raise ValueError("Use /plan add <step>; plans contain at most 100 steps.")
        session.plan_steps.append({"text": value[:2000], "status": "pending"})
    elif action in {"edit", "done", "pending", "running", "remove"}:
        selected = value.split(maxsplit=1)
        if not selected or not selected[0].isascii() or not selected[0].isdigit():
            raise ValueError(f"Use /plan {action} <step-number>" + (" <text>." if action == "edit" else "."))
        index = int(selected[0]) - 1
        if not 0 <= index < len(session.plan_steps):
            raise ValueError("Unknown plan step.")
        if action == "remove":
            session.plan_steps.pop(index)
        elif action == "edit":
            if len(selected) < 2:
                raise ValueError("Use /plan edit <step-number> <text>.")
            session.plan_steps[index]["text"] = selected[1][:2000]
        else:
            session.plan_steps[index]["status"] = action
    elif action == "clear":
        session.plan_steps.clear()
    elif action != "show":
        raise ValueError("Use /plan on|off|show|set|add|edit|running|done|pending|remove|clear.")
    if action not in {"show", "on", "off"}:
        outstanding = [item for item in session.checkpoint.get("outstanding", []) if item not in previous_steps]
        outstanding.extend(step["text"] for step in session.plan_steps if step["status"] != "done")
        session.update_checkpoint({"outstanding": outstanding})
    return plan_text(session)


def plan_text(session: Session) -> str:
    lines = ["Plan-only mode: " + ("on (read-only)" if session.mode == "plan" else "off")]
    lines.extend(f"{index}. [{step['status']}] {step['text']}" for index, step in enumerate(session.plan_steps, 1))
    if not session.plan_steps:
        lines.append("No plan steps. Use /plan set first step; second step.")
    return "\n".join(lines)


def request_subagent_resume(session: Session, argument: str) -> tuple[str, str]:
    """Persist an explicit worker continuation for the parent's next safe boundary."""
    agent_id, _, guidance = argument.strip().partition(" ")
    if not agent_id or agent_id not in session.subagents:
        raise ValueError("Use /agents resume <saved-worker-id> [guidance].")
    state = session.subagents[agent_id]
    if state.get("status") == "done":
        raise ValueError("This worker has already completed; create a new worker for new work.")
    if session.mode == "plan" and state.get("read_only") is False:
        raise ValueError("Plan mode cannot resume a writing worker. Switch to Build or use /plan off first.")
    if isinstance(state.get("tool_calls"), int) and isinstance(state.get("max_tool_calls"), int) and state["tool_calls"] >= state["max_tool_calls"]:
        raise ValueError("The saved worker has exhausted its tool-call budget.")
    if isinstance(state.get("elapsed_seconds"), (int, float)) and isinstance(state.get("max_seconds"), (int, float)) and state["elapsed_seconds"] >= state["max_seconds"]:
        raise ValueError("The saved worker has exhausted its time budget.")
    for item in session.pending_subagent_resumes:
        if item["id"] == agent_id:
            if guidance.strip():
                item["guidance"] = guidance.strip()
            return agent_id, item.get("guidance", "")
    session.pending_subagent_resumes.append({"id": agent_id, "guidance": guidance.strip()})
    return agent_id, guidance.strip()


def saved_subagent_snapshots(session: Session) -> list[dict[str, Any]]:
    """Render saved worker status without exposing raw child sessions or reasoning."""
    fields = {"id", "task", "scope", "read_only", "write_paths", "provider", "model", "status", "output", "error", "tool_calls", "max_tool_calls", "max_seconds", "created_at", "usage", "elapsed_seconds", "resume_count"}
    snapshots = []
    pending = {item["id"] for item in session.pending_subagent_resumes}
    for agent_id, value in session.subagents.items():
        item = {key: value[key] for key in fields if key in value}
        item.setdefault("id", agent_id)
        item.setdefault("task", "Saved worker")
        if item.get("status") in {"running", "blocked"}:
            item["status"] = "interrupted"
        item["resume_pending"] = agent_id in pending
        snapshots.append(item)
    return snapshots
