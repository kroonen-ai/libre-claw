# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


MessageRole: TypeAlias = Literal["user", "assistant"]
ContentBlock: TypeAlias = dict[str, Any]


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

    def add_user_message(self, content: str) -> None:
        self.messages.append(ChatMessage(role="user", content=[text_block(content)]))

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

    def compact(self, keep_last: int = 8) -> str | None:
        if len(self.messages) <= keep_last:
            return self.summary

        older = self.messages[:-keep_last]
        compacted = summarize_messages(older)
        if self.summary:
            compacted = self.summary + "\n" + compacted
        self.summary = compacted
        self.messages = self.messages[-keep_last:]
        return self.summary


def text_block(text: str) -> ContentBlock:
    return {"type": "text", "text": text}


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

        content = " ".join(part for part in text_parts + tool_parts if part).strip()
        if content:
            lines.append(f"{message.role}: {content[:500]}")
    return "\n".join(lines)
