# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from libre_claw.core.atif import RecordingSession, write_atif_trajectory
from libre_claw.core.session import text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import Usage


def test_atif_export_preserves_tool_history_after_compaction(tmp_path: Path) -> None:
    session = RecordingSession()
    session.add_user_message("Inspect the repository")
    session.add_assistant_blocks(
        [
            text_block("I will inspect it."),
            tool_use_block("call-1", "read_file", {"path": "README.md"}),
        ]
    )
    session.add_tool_result_blocks(
        [tool_result_block("call-1", "Libre Claw\n", is_error=False)]
    )
    session.add_assistant_message("Inspection complete.")
    session.compact(keep_last=1)

    path = tmp_path / "agent" / "trajectory.json"
    write_atif_trajectory(
        path,
        session=session,
        system_prompt="System prompt",
        agent_version="abc123",
        model_name="glm-5.2:cloud",
        tool_schemas=[
            {
                "name": "read_file",
                "description": "Read a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        usage=Usage(input_tokens=120, output_tokens=30, cached_tokens=10, cost=0.02),
        error=None,
        reasoning_effort="auto",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ATIF-v1.7"
    assert payload["agent"]["name"] == "libre-claw"
    assert payload["agent"]["version"] == "abc123"
    assert payload["agent"]["tool_definitions"][0]["function"]["name"] == "read_file"
    assert [step["source"] for step in payload["steps"]] == [
        "system",
        "user",
        "agent",
        "agent",
    ]
    tool_step = payload["steps"][2]
    assert tool_step["tool_calls"] == [
        {
            "tool_call_id": "call-1",
            "function_name": "read_file",
            "arguments": {"path": "README.md"},
        }
    ]
    assert tool_step["observation"]["results"][0] == {
        "source_call_id": "call-1",
        "content": "Libre Claw\n",
        "extra": {"is_error": False},
    }
    assert payload["final_metrics"] == {
        "total_steps": 4,
        "total_prompt_tokens": 120,
        "total_completion_tokens": 30,
        "total_cached_tokens": 10,
        "total_cost_usd": 0.02,
    }


def test_atif_export_records_errors_without_dropping_trace(tmp_path: Path) -> None:
    session = RecordingSession()
    session.add_user_message("Do the task")
    path = tmp_path / "trajectory.json"

    write_atif_trajectory(
        path,
        session=session,
        system_prompt="System prompt",
        agent_version="0.1.0",
        model_name="test-model",
        tool_schemas=[],
        usage=None,
        error="provider unavailable",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["extra"] == {
        "completed": False,
        "error": "provider unavailable",
    }
    assert "provider unavailable" in payload["notes"]
    assert payload["final_metrics"] == {"total_steps": 2}
