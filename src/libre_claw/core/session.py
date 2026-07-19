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

    def add_user_message(self, content: str, attachments: Sequence[UserAttachment] = ()) -> None:
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

    def compact(
        self,
        keep_last: int = 8,
        max_summary_chars: int = DEFAULT_COMPACT_SUMMARY_MAX_CHARS,
    ) -> str | None:
        if len(self.messages) <= keep_last:
            return self.summary

        older = self.messages[:-keep_last]
        compacted = summarize_messages(older)
        if self.summary:
            compacted = self.summary + "\n" + compacted
        self.summary = _bounded_compact_summary(compacted, max_chars=max_summary_chars)
        self.messages = self.messages[-keep_last:]
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
    for message in messages:
        text_parts: list[str] = []
        tool_parts: list[str] = []
        for block in message.content:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(str(block.get("text", "")))
            elif block_type == "tool_use":
                tool_parts.append(f"called {block.get('name', 'tool')}")
            elif block_type == "tool_result":
                tool_parts.append(f"tool result {block.get('tool_use_id', '')}")
            elif block_type == "image":
                tool_parts.append(f"attached image {block.get('filename') or block.get('media_type', '')}")

        content = " ".join(part for part in text_parts + tool_parts if part).strip()
        if content:
            lines.append(f"{message.role}: {content[:500]}")
    return "\n".join(lines)


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
        "messages": [message.as_provider_dict() for message in session.messages],
        "summary": session.summary,
    }


def session_from_payload(value: object) -> Session:
    session = Session()
    if not isinstance(value, dict):
        return session
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
