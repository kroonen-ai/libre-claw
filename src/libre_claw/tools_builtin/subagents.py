# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from libre_claw.core.session import ChatMessage
from libre_claw.core.subagents import SubagentManager
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


class SubagentTool(BaseTool):
    @property
    def manager(self) -> SubagentManager:
        manager = self.context.shared_state.get("subagent_manager")
        if not isinstance(manager, SubagentManager):
            raise ValueError("Subagents are not attached to an active agent run.")
        return manager

    @staticmethod
    def result(value: object) -> ToolResult:
        return ToolResult(content=json.dumps(value, ensure_ascii=True), metadata={"subagents": value})


@register_tool
class SubagentSpawnTool(SubagentTool):
    name = "subagent_spawn"
    description = (
        "Delegate one bounded independent task with a separate conversation. Workers default to read only. "
        "Writing workers must declare nonoverlapping write_paths relative to scope; their edits retain parent permissions. "
        "Wait for results before finishing; unfinished workers are cancelled when the parent turn ends."
    )
    parameters = {
        "task": {"type": "string"},
        "scope": {"type": "string", "description": "Existing workspace directory; use . for the current directory."},
        "read_only": {"type": "boolean", "default": True},
        "write_paths": {"type": "array", "items": {"type": "string"}},
        "provider": {"type": "string"}, "model": {"type": "string"},
        "max_tool_calls": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        "max_seconds": {"type": "number", "minimum": 1, "maximum": 900, "default": 180},
    }
    required = ("task", "scope")
    permission_level = "allow"

    def is_read_only(self, arguments: Mapping[str, Any]) -> bool:
        return arguments.get("read_only", True) is True

    async def execute(self, **kwargs: Any) -> ToolResult:
        return self.result(await self.manager.spawn(**kwargs))


@register_tool
class SubagentListTool(SubagentTool):
    name = "subagent_list"
    description = "Inspect bounded subagent status, scope, tool usage, results and errors."
    parameters = {}
    permission_level = "allow"

    async def execute(self) -> ToolResult:
        return self.result(self.manager.snapshots())


@register_tool
class SubagentWaitTool(SubagentTool):
    name = "subagent_wait"
    description = "Wait for the first selected worker to finish, then return current results for all selected workers."
    parameters = {
        "ids": {"type": "array", "items": {"type": "string"}},
        "timeout": {"type": "number", "minimum": 0, "maximum": 60, "default": 30},
    }
    permission_level = "allow"

    async def execute(self, ids: list[str] | None = None, timeout: float = 30) -> ToolResult:
        return self.result(await self.manager.wait(ids, timeout))


@register_tool
class SubagentCancelTool(SubagentTool):
    name = "subagent_cancel"
    description = "Cancel an active worker and release its declared file ownership."
    parameters = {"id": {"type": "string"}}
    required = ("id",)
    permission_level = "allow"
    read_only = True

    async def execute(self, id: str) -> ToolResult:
        return self.result(await self.manager.cancel(id))


@register_tool
class TaskCheckpointTool(BaseTool):
    name = "task_checkpoint"
    description = (
        "Maintain durable task state before compaction or after meaningful progress. "
        "Record requirements, decisions, changed_files, verification and outstanding work; "
        "each supplied list replaces that checkpoint field. Preserve unresolved requirements."
    )
    parameters = {
        "objective": {"type": "string"},
        **{key: {"type": "array", "items": {"type": "string"}} for key in (
            "requirements", "decisions", "changed_files", "verification", "outstanding",
        )},
    }
    permission_level = "allow"
    read_only = True

    async def execute(self, **kwargs: Any) -> ToolResult:
        from libre_claw.core.session import Session
        session = self.context.shared_state.get("agent_session")
        if not isinstance(session, Session):
            return ToolResult(error="No active task session is attached.")
        session.update_checkpoint(kwargs)
        return ToolResult(content=json.dumps(session.checkpoint), metadata={"checkpoint": dict(session.checkpoint)})


@register_tool
class TaskHistoryTool(BaseTool):
    name = "task_history"
    description = (
        "Search or page original archived and current task messages after compaction. "
        "Returns exact text excerpts with stable zero-based message indexes and continuation offsets. "
        "Use message_index and offset to read more of one message. Images return metadata only."
    )
    parameters = {
        "query": {"type": "string", "description": "Optional literal case-insensitive search, up to 256 characters."},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        "message_index": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "max_chars": {"type": "integer", "minimum": 100, "maximum": 12000, "default": 6000},
    }
    permission_level = "allow"
    read_only = True

    async def execute(
        self, query: str = "", page: int = 1, page_size: int = 5,
        message_index: int | None = None, offset: int = 0, max_chars: int = 6000,
    ) -> ToolResult:
        import asyncio
        from libre_claw.core.session import Session
        session = self.context.shared_state.get("agent_session")
        if not isinstance(session, Session):
            return ToolResult(error="No active task session is attached.")
        if not isinstance(query, str) or len(query) > 256:
            return ToolResult(error="query must contain at most 256 characters")
        if page < 1 or not 1 <= page_size <= 20 or offset < 0 or not 100 <= max_chars <= 12000:
            return ToolResult(error="Invalid page, page_size, offset or max_chars")
        messages = [*session.archived_messages, *session.messages]
        if message_index is not None and not 0 <= message_index < len(messages):
            return ToolResult(error="message_index is outside this task's history")
        result = await asyncio.to_thread(
            _history_page, messages, len(session.archived_messages), query,
            page, page_size, message_index, offset, max_chars,
        )
        return ToolResult(content=json.dumps(result, ensure_ascii=True), metadata={"history": result})


def _history_page(
    messages: Sequence[ChatMessage], archived_count: int, query: str, page: int,
    page_size: int, message_index: int | None, offset: int, max_chars: int,
) -> dict[str, Any]:
    import re
    matches = []
    pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
    for index, message in enumerate(messages):
        if message_index is not None and index != message_index:
            continue
        chunks = []
        for block in message.content:
            if block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            elif block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    chunks.extend(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text")
            elif block.get("type") == "tool_use":
                chunks.append("Tool call: " + str(block.get("name", "tool")))
            elif block.get("type") == "image":
                chunks.append(f"[Image: {block.get('filename', 'attachment')} ({block.get('media_type', 'unknown')})]")
        text = "\n".join(chunks)
        match = pattern.search(text) if pattern is not None else None
        if pattern is None or match is not None:
            matches.append((index, message.role, text, match.start() if match is not None else 0))
    selected = matches[(page - 1) * page_size:page * page_size]
    per_message = max(1, max_chars // max(1, len(selected)))
    results = []
    for index, role, text, match_start in selected:
        start = offset
        if query and offset == 0 and message_index is None:
            start = max(0, match_start - min(200, per_message // 4))
        excerpt = text[start:start + per_message]
        results.append({
            "message_index": index, "role": role, "archived": index < archived_count,
            "text": excerpt, "offset": start, "characters": len(text),
            "next_offset": start + len(excerpt) if start + len(excerpt) < len(text) else None,
        })
    return {
        "total_messages": len(messages), "total_matches": len(matches), "page": page,
        "page_size": page_size, "next_page": page + 1 if page * page_size < len(matches) else None,
        "messages": results,
    }
