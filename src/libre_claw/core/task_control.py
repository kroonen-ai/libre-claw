# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

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
