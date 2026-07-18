# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libre_claw.core.session import ChatMessage, ContentBlock, Session, UserAttachment
from libre_claw.providers.base import ToolSchema, Usage


ATIF_SCHEMA_VERSION = "ATIF-v1.7"


@dataclass(frozen=True)
class RecordedMessage:
    """A session message retained independently of context compaction."""

    message: ChatMessage
    timestamp: str


class RecordingSession(Session):
    """Session variant that preserves the complete message history for audit traces."""

    def __init__(self) -> None:
        super().__init__()
        self.started_at = _timestamp()
        self.recorded_messages: list[RecordedMessage] = []

    def add_user_message(self, content: str, attachments: Sequence[UserAttachment] = ()) -> None:
        super().add_user_message(content, attachments=attachments)
        self._record_last_message()

    def add_assistant_message(self, content: str) -> None:
        super().add_assistant_message(content)
        self._record_last_message()

    def add_assistant_blocks(self, blocks: list[ContentBlock]) -> None:
        previous_count = len(self.messages)
        super().add_assistant_blocks(blocks)
        if len(self.messages) > previous_count:
            self._record_last_message()

    def add_tool_result_blocks(self, blocks: list[ContentBlock]) -> None:
        previous_count = len(self.messages)
        super().add_tool_result_blocks(blocks)
        if len(self.messages) > previous_count:
            self._record_last_message()

    def _record_last_message(self) -> None:
        self.recorded_messages.append(
            RecordedMessage(message=deepcopy(self.messages[-1]), timestamp=_timestamp())
        )


def write_atif_trajectory(
    path: Path,
    *,
    session: RecordingSession,
    system_prompt: str,
    agent_version: str,
    model_name: str,
    tool_schemas: list[ToolSchema],
    usage: Usage | None,
    error: str | None,
    reasoning_effort: str | None = None,
) -> None:
    """Write one complete Libre Claw run in ATIF v1.7 format."""
    trajectory_id = f"libre-claw-{uuid.uuid4().hex}"
    steps = _trajectory_steps(
        session,
        system_prompt=system_prompt,
        model_name=model_name,
        reasoning_effort=reasoning_effort,
    )
    final_metrics: dict[str, Any] = {"total_steps": len(steps)}
    if usage is not None:
        final_metrics.update(
            {
                "total_prompt_tokens": usage.input_tokens,
                "total_completion_tokens": usage.output_tokens,
                "total_cached_tokens": usage.cached_tokens,
            }
        )
        if usage.cost is not None:
            final_metrics["total_cost_usd"] = usage.cost

    payload: dict[str, Any] = {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": trajectory_id,
        "trajectory_id": trajectory_id,
        "agent": {
            "name": "libre-claw",
            "version": agent_version,
            "model_name": model_name,
            "tool_definitions": [_openai_tool_schema(schema) for schema in tool_schemas],
            "extra": {"producer": "Libre Claw native ATIF exporter"},
        },
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": {"completed": error is None},
    }
    if error:
        payload["notes"] = f"Libre Claw ended with an error: {error}"
        payload["extra"]["error"] = error

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _trajectory_steps(
    session: RecordingSession,
    *,
    system_prompt: str,
    model_name: str,
    reasoning_effort: str | None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "timestamp": session.started_at,
            "source": "system",
            "message": system_prompt,
        }
    ]
    index = 0
    records = session.recorded_messages
    while index < len(records):
        record = records[index]
        message = record.message
        if message.role == "assistant":
            step = _assistant_step(
                message,
                timestamp=record.timestamp,
                model_name=model_name,
                reasoning_effort=reasoning_effort,
            )
            if _has_tool_calls(step) and index + 1 < len(records):
                next_record = records[index + 1]
                if _is_tool_result_message(next_record.message):
                    step["observation"] = {
                        "results": [
                            {
                                "source_call_id": str(block.get("tool_use_id", "")),
                                "content": str(block.get("content", "")),
                                "extra": {"is_error": bool(block.get("is_error", False))},
                            }
                            for block in next_record.message.content
                            if block.get("type") == "tool_result"
                        ]
                    }
                    index += 1
            steps.append(step)
        elif not _is_tool_result_message(message):
            steps.append(
                {
                    "timestamp": record.timestamp,
                    "source": "user",
                    "message": _message_text(message.content),
                }
            )
        index += 1

    for step_id, step in enumerate(steps, start=1):
        step["step_id"] = step_id
    return steps


def _assistant_step(
    message: ChatMessage,
    *,
    timestamp: str,
    model_name: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    tool_calls = [
        {
            "tool_call_id": str(block.get("id", "")),
            "function_name": str(block.get("name", "")),
            "arguments": dict(block.get("input", {}))
            if isinstance(block.get("input"), dict)
            else {},
        }
        for block in message.content
        if block.get("type") == "tool_use"
    ]
    step: dict[str, Any] = {
        "timestamp": timestamp,
        "source": "agent",
        "model_name": model_name,
        "message": _message_text(message.content),
        "llm_call_count": 1,
    }
    if reasoning_effort:
        step["reasoning_effort"] = reasoning_effort
    if tool_calls:
        step["tool_calls"] = tool_calls
    return step


def _message_text(blocks: list[ContentBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "image":
            label = block.get("filename") or block.get("media_type") or "image"
            parts.append(f"[Attached image: {label}]")
    return "\n".join(part for part in parts if part).strip()


def _has_tool_calls(step: dict[str, Any]) -> bool:
    return bool(step.get("tool_calls"))


def _is_tool_result_message(message: ChatMessage) -> bool:
    return bool(message.content) and all(
        block.get("type") == "tool_result" for block in message.content
    )


def _openai_tool_schema(schema: ToolSchema) -> dict[str, Any]:
    parameters = schema.get("input_schema", {})
    return {
        "type": "function",
        "function": {
            "name": str(schema.get("name", "")),
            "description": str(schema.get("description", "")),
            "parameters": dict(parameters) if isinstance(parameters, dict) else {},
        },
    }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
