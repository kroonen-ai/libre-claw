# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, cast


MessageRole: TypeAlias = Literal["user", "assistant"]
ContentBlock: TypeAlias = dict[str, Any]
DEFAULT_COMPACT_SUMMARY_MAX_CHARS = 12_000
COMPACT_MESSAGE_MAX_CHARS = 800
COMPACT_TOOL_ARGUMENT_MAX_CHARS = 180
COMPACT_TOOL_RESULT_MAX_CHARS = 360


@dataclass(frozen=True)
class UserAttachment:
    """A user-supplied attachment that can be represented in provider messages."""

    media_type: str
    data: str
    filename: str = ""
    path: str = ""

    def as_payload(self) -> dict[str, str]:
        payload = {"media_type": self.media_type, "data": self.data}
        if self.filename:
            payload["filename"] = self.filename
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    content: list[ContentBlock]

    def as_provider_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class Session:
    """In-memory conversation state with Anthropic-compatible content blocks."""

    messages: list[ChatMessage] = field(default_factory=list)
    summary: str | None = None
    archived_messages: list[ChatMessage] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    mode: Literal["default", "plan"] = "default"
    plan_steps: list[dict[str, str]] = field(default_factory=list)
    pending_steering: list[str] = field(default_factory=list)

    def add_user_message(self, content: str, attachments: Sequence[UserAttachment] = ()) -> None:
        if content.strip() and not self.checkpoint.get("objective"):
            self.checkpoint["objective"] = content.strip()[:6000]
        blocks: list[ContentBlock] = []
        if content.strip() or not attachments:
            blocks.append(text_block(content))
        blocks.extend(image_block(attachment) for attachment in attachments)
        self.messages.append(ChatMessage(role="user", content=blocks))

    def add_assistant_message(self, content: str) -> None:
        self.messages.append(ChatMessage(role="assistant", content=[text_block(content)]))

    def add_assistant_blocks(self, blocks: list[ContentBlock]) -> None:
        if blocks:
            self.messages.append(ChatMessage(role="assistant", content=blocks))

    def add_tool_result_blocks(self, blocks: list[ContentBlock]) -> None:
        if blocks:
            self.messages.append(ChatMessage(role="user", content=blocks))

    def clear(self) -> None:
        self.messages.clear()
        self.summary = None
        self.archived_messages.clear()
        self.checkpoint.clear()
        self.plan_steps.clear()
        self.pending_steering.clear()

    def queue_steering(self, content: str) -> None:
        if content.strip():
            self.pending_steering.append(content.strip())

    def consume_steering(self) -> list[str]:
        notes = list(self.pending_steering)
        self.pending_steering.clear()
        return notes

    def update_checkpoint(self, updates: dict[str, Any]) -> None:
        for key in ("objective", "requirements", "decisions", "changed_files", "verification", "outstanding", "last_turn_tree", "prior_context"):
            value = updates.get(key)
            if key in {"objective", "last_turn_tree", "prior_context"} and isinstance(value, str):
                self.checkpoint[key] = value.strip()[:12000 if key == "prior_context" else 6000]
            elif key != "objective" and isinstance(value, list):
                self.checkpoint[key] = list(dict.fromkeys(str(item).strip()[:1000] for item in value if str(item).strip()))[:24]

    def control_prompt(self) -> str:
        sections: list[str] = []
        if self.mode == "plan":
            sections.append("PLAN ONLY: inspect and explain. Do not change files, run mutating commands, or delegate write work. Produce an actionable plan for the user to edit or approve.")
        if self.plan_steps:
            sections.append("Task plan:\n" + "\n".join(f"{i}. [{step['status']}] {step['text']}" for i, step in enumerate(self.plan_steps, 1)))
        if self.checkpoint:
            sections.append("Task checkpoint:\n" + _checkpoint_text(self.checkpoint))
        return "\n\n".join(sections)

    def recover_interrupted_tools(self) -> None:
        pending: dict[str, str] = {}
        for message in self.messages:
            for block in message.content:
                if block.get("type") == "tool_use" and block.get("id"):
                    pending[str(block["id"])] = str(block.get("name", "tool"))
                elif block.get("type") == "tool_result":
                    pending.pop(str(block.get("tool_use_id", "")), None)
        if pending:
            self.add_tool_result_blocks([
                tool_result_block(tool_id, f"Interrupted while running {name}. Completion is unknown; inspect current state before retrying side effects.", is_error=True)
                for tool_id, name in pending.items()
            ])

    def compact(
        self,
        keep_last: int = 8,
        max_summary_chars: int = DEFAULT_COMPACT_SUMMARY_MAX_CHARS,
    ) -> str | None:
        if len(self.messages) <= keep_last:
            return self.summary

        cut = max(0, len(self.messages) - max(1, keep_last))
        # A retained tool result must keep its matching assistant request.
        retained_results = {str(block.get("tool_use_id", "")) for message in self.messages[cut:] for block in message.content if block.get("type") == "tool_result"}
        for index, message in enumerate(self.messages[:cut]):
            if any(block.get("type") == "tool_use" and str(block.get("id", "")) in retained_results for block in message.content):
                cut = min(cut, index)
                break
        if not cut:
            return self.summary
        older = self.messages[:cut]
        if not self.archived_messages and self.summary and not self.checkpoint.get("prior_context"):
            self.checkpoint["prior_context"] = self.summary[:12000]
        self.archived_messages.extend(older)
        if not self.checkpoint.get("objective"):
            self.checkpoint["objective"] = next((str(block.get("text", ""))[:6000] for message in self.archived_messages if message.role == "user" for block in message.content if block.get("type") == "text"), "")
        requirements = list(self.checkpoint.get("requirements", []))
        for message in older:
            if message.role == "user":
                for block in message.content:
                    if block.get("type") == "text" and block.get("text"):
                        note = str(block["text"])[:1000]
                        if note not in requirements:
                            requirements.append(note)
        self.checkpoint["requirements"] = requirements[:8] + requirements[8:][-8:]
        structured = _checkpoint_text(self.checkpoint)
        remaining = max(0, max_summary_chars - len(structured) - 30)
        activity = summarize_messages(self.archived_messages[-24:])
        self.summary = (structured + "\n\nRecent activity:\n" + _bounded_compact_summary(activity, max_chars=remaining))[:max_summary_chars]
        self.messages = self.messages[cut:]
        return self.summary


def text_block(text: str) -> ContentBlock:
    return {"type": "text", "text": text}


def image_block(attachment: UserAttachment) -> ContentBlock:
    block: ContentBlock = {
        "type": "image",
        "media_type": attachment.media_type,
        "data": attachment.data,
    }
    if attachment.filename:
        block["filename"] = attachment.filename
    if attachment.path:
        block["path"] = attachment.path
    return block


def provider_reasoning_block(text: str, provider: str) -> ContentBlock:
    """Store opaque provider reasoning without presenting it as assistant text."""
    return {
        "type": "provider_reasoning",
        "provider": provider,
        "text": text,
    }


def tool_use_block(tool_use_id: str, name: str, input_data: dict[str, Any]) -> ContentBlock:
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": input_data,
    }


def tool_result_block(tool_use_id: str, content: str, is_error: bool = False) -> ContentBlock:
    block: ContentBlock = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


def summarize_messages(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    tool_names: dict[str, str] = {}
    for message in messages:
        text_parts: list[str] = []
        tool_parts: list[str] = []
        for block in message.content:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(_compact_summary_fragment(str(block.get("text", "")), 500))
            elif block_type == "tool_use":
                tool_name = str(block.get("name", "tool"))
                tool_use_id = str(block.get("id", ""))
                if tool_use_id:
                    tool_names[tool_use_id] = tool_name
                arguments = _compact_summary_fragment(
                    json.dumps(block.get("input", {}), sort_keys=True, default=str),
                    COMPACT_TOOL_ARGUMENT_MAX_CHARS,
                )
                tool_parts.append(f"called {tool_name} {arguments}".rstrip())
            elif block_type == "tool_result":
                tool_use_id = str(block.get("tool_use_id", ""))
                tool_name = tool_names.get(tool_use_id, f"tool {tool_use_id}".rstrip())
                status = " error" if block.get("is_error") else " result"
                result = _compact_summary_fragment(
                    str(block.get("content", "")),
                    COMPACT_TOOL_RESULT_MAX_CHARS,
                )
                tool_parts.append(f"{tool_name}{status}: {result}".rstrip())
            elif block_type == "image":
                tool_parts.append(f"attached image {block.get('filename') or block.get('media_type', '')}")

        content = " ".join(part for part in text_parts + tool_parts if part).strip()
        if content:
            lines.append(
                f"{message.role}: "
                f"{_compact_summary_fragment(content, COMPACT_MESSAGE_MAX_CHARS)}"
            )
    return "\n".join(lines)


def _compact_summary_fragment(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    omitted = len(compact) - limit
    for _ in range(3):
        marker = f" ... [{omitted} chars omitted] ... "
        retained_chars = max(0, limit - len(marker))
        next_omitted = len(compact) - retained_chars
        if next_omitted == omitted:
            break
        omitted = next_omitted
    marker = f" ... [{omitted} chars omitted] ... "
    retained_chars = max(0, limit - len(marker))
    head_chars = (retained_chars + 1) // 2
    tail_chars = retained_chars - head_chars
    tail = compact[-tail_chars:] if tail_chars else ""
    return compact[:head_chars] + marker + tail


def _bounded_compact_summary(summary: str, *, max_chars: int) -> str:
    """Retain recent compacted context without letting repeated compaction grow forever."""
    limit = max(1, max_chars)
    if len(summary) <= limit:
        return summary
    marker = "[Earlier compacted context omitted]\n"
    if limit <= len(marker):
        return summary[-limit:]
    return marker + summary[-(limit - len(marker)) :]


def session_to_payload(session: Session) -> dict[str, Any]:
    return {
        "version": 2,
        "messages": [message.as_provider_dict() for message in session.messages],
        "summary": session.summary,
        "archived_messages": [message.as_provider_dict() for message in session.archived_messages],
        "checkpoint": session.checkpoint,
        "mode": session.mode,
        "plan_steps": session.plan_steps,
        "pending_steering": session.pending_steering,
    }


def session_from_payload(value: object) -> Session:
    session = Session()
    if not isinstance(value, dict):
        return session
    session.mode = "plan" if value.get("mode") == "plan" else "default"
    if isinstance(value.get("checkpoint"), dict):
        session.update_checkpoint(value["checkpoint"])
    if isinstance(value.get("plan_steps"), list):
        session.plan_steps = [{"text": str(step["text"])[:2000], "status": str(step.get("status", "pending"))} for step in value["plan_steps"] if isinstance(step, dict) and step.get("text") and step.get("status", "pending") in {"pending", "running", "done"}][:100]
    if isinstance(value.get("pending_steering"), list):
        session.pending_steering = [item for item in value["pending_steering"] if isinstance(item, str) and item.strip()]
    if isinstance(value.get("archived_messages"), list):
        session.archived_messages = session_from_payload({"messages": value["archived_messages"]}).messages
    summary = value.get("summary")
    if isinstance(summary, str) and summary.strip():
        session.summary = summary
    messages = value.get("messages")
    if not isinstance(messages, list):
        return session
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        blocks = [dict(block) for block in content if isinstance(block, dict)]
        if blocks:
            session.messages.append(ChatMessage(role=cast(MessageRole, role), content=blocks))
    return session


def _checkpoint_text(checkpoint: dict[str, Any]) -> str:
    sections = []
    for key, title, budget in (("objective", "Objective", 4000), ("prior_context", "Previously saved context", 2600), ("requirements", "Requirements", 2600), ("decisions", "Decisions", 1600), ("changed_files", "Changed files", 900), ("verification", "Verification", 1000), ("outstanding", "Outstanding work", 1600)):
        value = checkpoint.get(key)
        if value:
            body = "\n".join(f"- {item}" for item in value) if isinstance(value, list) else str(value)
            sections.append(f"{title}:\n{body[:budget]}")
    return "\n\n".join(sections)


def estimate_context_tokens(
    messages: list[ChatMessage],
    summary: str | None = None,
    extra_texts: tuple[str, ...] = (),
) -> int:
    """Estimate context size cheaply when provider tokenizers are unavailable."""
    character_count = sum(len(text) for text in extra_texts if text)
    if summary:
        character_count += len(summary)

    for message in messages:
        character_count += 16
        for block in message.content:
            block_type = block.get("type")
            if block_type == "text":
                character_count += len(str(block.get("text", "")))
            elif block_type == "provider_reasoning":
                character_count += len(str(block.get("text", "")))
            elif block_type == "tool_use":
                character_count += len(str(block.get("name", "")))
                character_count += len(json.dumps(block.get("input", {}), sort_keys=True, default=str))
            elif block_type == "tool_result":
                character_count += len(str(block.get("content", "")))
            elif block_type == "image":
                character_count += len(str(block.get("filename", "")))
                character_count += len(str(block.get("media_type", "")))
                character_count += len(str(block.get("data", ""))) // 4
            else:
                character_count += len(json.dumps(block, sort_keys=True, default=str))

    return max(0, math.ceil(character_count / 4))
