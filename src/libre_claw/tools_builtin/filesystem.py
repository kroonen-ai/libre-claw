# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from libre_claw.core.tools import BaseTool, ToolResult, register_tool


@register_tool
class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file at the given path."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "offset": {"type": "integer", "description": "Start line, 0-based", "default": 0},
        "limit": {"type": "integer", "description": "Maximum number of lines to read", "default": 500},
    }
    required = ("path",)
    permission_level = "allow"

    async def execute(self, path: str, offset: int = 0, limit: int = 500) -> ToolResult:
        try:
            return await asyncio.to_thread(self._read, path, offset, limit)
        except Exception as exc:
            return ToolResult(error=str(exc))

    def _read(self, path: str, offset: int, limit: int) -> ToolResult:
        if offset < 0:
            return ToolResult(error="offset must be >= 0")
        if limit < 1:
            return ToolResult(error="limit must be >= 1")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"File does not exist: {resolved}")
        if not resolved.is_file():
            return ToolResult(error=f"Path is not a file: {resolved}")

        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[offset : offset + limit]
        content = "\n".join(f"{line_number}: {line}" for line_number, line in enumerate(selected, start=offset))
        return ToolResult(
            content=content,
            metadata={"path": str(resolved), "offset": offset, "limit": limit, "total_lines": len(lines)},
        )


@register_tool
class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Create or overwrite a file."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "content": {"type": "string", "description": "Complete file content to write"},
    }
    required = ("path", "content")
    permission_level = "ask"

    async def execute(self, path: str, content: str) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._write, path, content)
        except Exception as exc:
            return ToolResult(error=str(exc))
        await _log_edit(self, "write_file", result)
        return result

    def _write(self, path: str, content: str) -> ToolResult:
        resolved = self.resolve_path(path)
        before = resolved.read_text(encoding="utf-8", errors="replace") if resolved.exists() else ""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return ToolResult(
            content=f"Wrote {len(content)} characters to {resolved}",
            metadata={"path": str(resolved), "before": before, "after": content},
        )


@register_tool
class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace one exact string in a file with another string."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "old_text": {"type": "string", "description": "Exact text to replace"},
        "new_text": {"type": "string", "description": "Replacement text"},
    }
    required = ("path", "old_text", "new_text")
    permission_level = "ask"

    async def execute(self, path: str, old_text: str, new_text: str) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._edit, path, old_text, new_text)
        except Exception as exc:
            return ToolResult(error=str(exc))
        await _log_edit(self, "edit_file", result)
        return result

    def _edit(self, path: str, old_text: str, new_text: str) -> ToolResult:
        if old_text == "":
            return ToolResult(error="old_text must not be empty")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"File does not exist: {resolved}")
        if not resolved.is_file():
            return ToolResult(error=f"Path is not a file: {resolved}")

        before = resolved.read_text(encoding="utf-8", errors="replace")
        matches = before.count(old_text)
        if matches == 0:
            return ToolResult(error="old_text was not found")
        if matches > 1:
            return ToolResult(error=f"old_text matched {matches} times; provide a more specific replacement")

        after = before.replace(old_text, new_text, 1)
        resolved.write_text(after, encoding="utf-8")
        return ToolResult(
            content=f"Replaced 1 occurrence in {resolved}",
            metadata={"path": str(resolved), "before": before, "after": after},
        )

@register_tool
class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "List files and directories with bounded recursive depth."
    parameters = {
        "path": {"type": "string", "description": "Directory path", "default": "."},
        "depth": {"type": "integer", "description": "Maximum recursive depth", "default": 2},
    }
    permission_level = "allow"

    async def execute(self, path: str = ".", depth: int = 2) -> ToolResult:
        try:
            return await asyncio.to_thread(self._list, path, depth)
        except Exception as exc:
            return ToolResult(error=str(exc))

    def _list(self, path: str, depth: int) -> ToolResult:
        if depth < 0:
            return ToolResult(error="depth must be >= 0")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"Directory does not exist: {resolved}")
        if not resolved.is_dir():
            return ToolResult(error=f"Path is not a directory: {resolved}")

        entries = _list_entries(resolved, depth)
        return ToolResult(content="\n".join(entries), metadata={"path": str(resolved), "depth": depth})


def _list_entries(root: Path, depth: int) -> list[str]:
    entries: list[str] = []

    def walk(directory: Path, remaining_depth: int, prefix: str = "") -> None:
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            marker = "/" if child.is_dir() else ""
            entries.append(f"{prefix}{child.name}{marker}")
            if child.is_dir() and remaining_depth > 0:
                walk(child, remaining_depth - 1, prefix + "  ")

    walk(root, depth)
    return entries


async def _log_edit(tool: BaseTool, tool_name: str, result: ToolResult) -> None:
    memory_store = tool.context.memory_store
    if result.is_error or memory_store is None:
        return
    path = result.metadata.get("path")
    before = result.metadata.get("before")
    after = result.metadata.get("after")
    if isinstance(path, str) and isinstance(before, str) and isinstance(after, str):
        await memory_store.log_file_edit(path=path, tool_name=tool_name, before=before, after=after)
