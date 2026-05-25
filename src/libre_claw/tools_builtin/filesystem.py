# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import difflib
import os
import stat
import tempfile
from pathlib import Path

from libre_claw.core.tools import BaseTool, ToolResult, register_tool


MAX_READ_LINES = 2000
MAX_LIST_DEPTH = 8
MAX_LIST_ENTRIES = 1000


@register_tool
class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a precise line range from a text file. Use offset and limit to inspect large files in chunks."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "offset": {"type": "integer", "description": "Start line, 0-based", "default": 0},
        "limit": {
            "type": "integer",
            "description": f"Maximum number of lines to read, capped at {MAX_READ_LINES}",
            "default": 500,
        },
        "show_line_numbers": {
            "type": "boolean",
            "description": "Prefix each returned line with its 0-based line number",
            "default": True,
        },
    }
    required = ("path",)
    permission_level = "allow"

    async def execute(
        self,
        path: str,
        offset: int = 0,
        limit: int = 500,
        show_line_numbers: bool = True,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(self._read, path, offset, limit, show_line_numbers)
        except Exception as exc:
            return ToolResult(error=str(exc))

    def _read(self, path: str, offset: int, limit: int, show_line_numbers: bool) -> ToolResult:
        if offset < 0:
            return ToolResult(error="offset must be >= 0")
        if limit < 1:
            return ToolResult(error="limit must be >= 1")
        if limit > MAX_READ_LINES:
            return ToolResult(error=f"limit must be <= {MAX_READ_LINES}; read large files in chunks")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"File does not exist: {resolved}")
        if not resolved.is_file():
            return ToolResult(error=f"Path is not a file: {resolved}")

        selected, truncated = _read_line_range(resolved, offset, limit)
        if show_line_numbers:
            content = "\n".join(f"{line_number}: {line}" for line_number, line in selected)
        else:
            content = "\n".join(line for _, line in selected)
        return ToolResult(
            content=content,
            metadata={
                "path": str(resolved),
                "offset": offset,
                "limit": limit,
                "returned_lines": len(selected),
                "truncated": truncated,
                "size_bytes": resolved.stat().st_size,
            },
        )


@register_tool
class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Atomically create or overwrite a UTF-8 text file."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "content": {"type": "string", "description": "Complete file content to write"},
        "overwrite": {"type": "boolean", "description": "Allow replacing an existing file", "default": True},
    }
    required = ("path", "content")
    permission_level = "ask"

    async def execute(self, path: str, content: str, overwrite: bool = True) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._write, path, content, overwrite)
        except Exception as exc:
            return ToolResult(error=str(exc))
        await _log_edit(self, "write_file", result)
        return result

    def _write(self, path: str, content: str, overwrite: bool) -> ToolResult:
        resolved = self.resolve_path(path)
        if resolved.exists() and resolved.is_dir():
            return ToolResult(error=f"Path is a directory: {resolved}")
        if resolved.exists() and not overwrite:
            return ToolResult(error=f"File already exists and overwrite is false: {resolved}")

        exists = resolved.exists()
        before = resolved.read_text(encoding="utf-8", errors="replace") if exists else ""
        if exists and before == content:
            return ToolResult(
                content=f"No changes; file already matches requested content: {resolved}",
                metadata={
                    "path": str(resolved),
                    "before": before,
                    "after": content,
                    "changed": False,
                    "created": False,
                    "bytes_written": 0,
                },
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(resolved, content)
        action = "Created" if not exists else "Updated"
        return ToolResult(
            content=f"{action} {resolved} ({len(content)} characters, {len(content.encode('utf-8'))} bytes)",
            metadata={
                "path": str(resolved),
                "before": before,
                "after": content,
                "changed": True,
                "created": not exists,
                "bytes_written": len(content.encode("utf-8")),
            },
        )


@register_tool
class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Atomically replace exact text in a UTF-8 file, with optional occurrence targeting."
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "old_text": {"type": "string", "description": "Exact text to replace"},
        "new_text": {"type": "string", "description": "Replacement text"},
        "occurrence": {
            "type": "integer",
            "description": "1-based occurrence to replace. Omit to require old_text to match exactly once.",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace every occurrence of old_text",
            "default": False,
        },
    }
    required = ("path", "old_text", "new_text")
    permission_level = "ask"

    async def execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        occurrence: int | None = None,
        replace_all: bool = False,
    ) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._edit, path, old_text, new_text, occurrence, replace_all)
        except Exception as exc:
            return ToolResult(error=str(exc))
        await _log_edit(self, "edit_file", result)
        return result

    def _edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        occurrence: int | None,
        replace_all: bool,
    ) -> ToolResult:
        if old_text == "":
            return ToolResult(error="old_text must not be empty")
        if occurrence is not None and occurrence < 1:
            return ToolResult(error="occurrence must be >= 1")
        if occurrence is not None and replace_all:
            return ToolResult(error="occurrence and replace_all cannot both be set")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"File does not exist: {resolved}")
        if not resolved.is_file():
            return ToolResult(error=f"Path is not a file: {resolved}")

        before = resolved.read_text(encoding="utf-8", errors="replace")
        matches = before.count(old_text)
        if matches == 0:
            return ToolResult(error="old_text was not found")
        if not replace_all and occurrence is None and matches > 1:
            return ToolResult(error=f"old_text matched {matches} times; set occurrence or replace_all")
        if occurrence is not None and occurrence > matches:
            return ToolResult(error=f"occurrence {occurrence} was requested, but old_text matched {matches} times")

        if replace_all:
            after = before.replace(old_text, new_text)
            replacements = matches
        else:
            after = _replace_occurrence(before, old_text, new_text, occurrence or 1)
            replacements = 1

        if before == after:
            return ToolResult(
                content=f"No changes; replacement text is identical in {resolved}",
                metadata={
                    "path": str(resolved),
                    "before": before,
                    "after": after,
                    "changed": False,
                    "replacements": 0,
                    "matches": matches,
                },
            )

        _write_text_atomic(resolved, after)
        diff = _unified_diff(before, after, resolved)
        return ToolResult(
            content=f"Replaced {replacements} occurrence(s) in {resolved}\n\n{diff}",
            metadata={
                "path": str(resolved),
                "before": before,
                "after": after,
                "changed": True,
                "matches": matches,
                "replacements": replacements,
                "diff": diff,
            },
        )


@register_tool
class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "List files and directories with bounded recursive depth and entry count."
    parameters = {
        "path": {"type": "string", "description": "Directory path", "default": "."},
        "depth": {
            "type": "integer",
            "description": f"Maximum recursive depth, capped at {MAX_LIST_DEPTH}",
            "default": 2,
        },
        "max_entries": {
            "type": "integer",
            "description": f"Maximum entries to return, capped at {MAX_LIST_ENTRIES}",
            "default": 500,
        },
        "include_hidden": {
            "type": "boolean",
            "description": "Include dotfiles and hidden directories",
            "default": True,
        },
    }
    permission_level = "allow"

    async def execute(
        self,
        path: str = ".",
        depth: int = 2,
        max_entries: int = 500,
        include_hidden: bool = True,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(self._list, path, depth, max_entries, include_hidden)
        except Exception as exc:
            return ToolResult(error=str(exc))

    def _list(self, path: str, depth: int, max_entries: int, include_hidden: bool) -> ToolResult:
        if depth < 0:
            return ToolResult(error="depth must be >= 0")
        if depth > MAX_LIST_DEPTH:
            return ToolResult(error=f"depth must be <= {MAX_LIST_DEPTH}")
        if max_entries < 1:
            return ToolResult(error="max_entries must be >= 1")
        if max_entries > MAX_LIST_ENTRIES:
            return ToolResult(error=f"max_entries must be <= {MAX_LIST_ENTRIES}")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"Directory does not exist: {resolved}")
        if not resolved.is_dir():
            return ToolResult(error=f"Path is not a directory: {resolved}")

        entries, truncated = _list_entries(resolved, depth, max_entries, include_hidden)
        content = "\n".join(entries)
        if truncated:
            content += f"\n... truncated after {len(entries)} entries; narrow path/depth or raise max_entries"
        return ToolResult(
            content=content,
            metadata={
                "path": str(resolved),
                "depth": depth,
                "entry_count": len(entries),
                "truncated": truncated,
                "max_entries": max_entries,
            },
        )


def _read_line_range(path: Path, offset: int, limit: int) -> tuple[list[tuple[int, str]], bool]:
    selected: list[tuple[int, str]] = []
    truncated = False

    with path.open("r", encoding="utf-8", errors="replace", newline=None) as handle:
        for line_number, line in enumerate(handle):
            if line_number < offset:
                continue
            if len(selected) >= limit:
                truncated = True
                break
            selected.append((line_number, line.rstrip("\n")))

    return selected, truncated


def _write_text_atomic(path: Path, content: str) -> None:
    existing_mode: int | None = None
    if path.exists():
        existing_mode = stat.S_IMODE(path.stat().st_mode)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        if existing_mode is not None:
            tmp_path.chmod(existing_mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _replace_occurrence(text: str, old_text: str, new_text: str, occurrence: int) -> str:
    start = -1
    search_from = 0
    for _ in range(occurrence):
        start = text.find(old_text, search_from)
        if start == -1:
            return text
        search_from = start + len(old_text)
    end = start + len(old_text)
    return text[:start] + new_text + text[end:]


def _unified_diff(before: str, after: str, path: Path) -> str:
    diff_lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        lineterm="",
    )
    return "\n".join(diff_lines).rstrip()


def _list_entries(root: Path, depth: int, max_entries: int, include_hidden: bool) -> tuple[list[str], bool]:
    entries: list[str] = []
    truncated = False

    def walk(directory: Path, remaining_depth: int, prefix: str = "") -> None:
        nonlocal truncated
        if truncated:
            return
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if not include_hidden and child.name.startswith("."):
                continue
            if len(entries) >= max_entries:
                truncated = True
                return
            marker = _entry_marker(child)
            entries.append(f"{prefix}{child.name}{marker}")
            if child.is_dir() and remaining_depth > 0:
                walk(child, remaining_depth - 1, prefix + "  ")

    walk(root, depth)
    return entries, truncated


def _entry_marker(path: Path) -> str:
    if path.is_symlink():
        return "@"
    if path.is_dir():
        return "/"
    return ""


async def _log_edit(tool: BaseTool, tool_name: str, result: ToolResult) -> None:
    memory_store = tool.context.memory_store
    if result.is_error or memory_store is None:
        return
    path = result.metadata.get("path")
    before = result.metadata.get("before")
    after = result.metadata.get("after")
    if isinstance(path, str) and isinstance(before, str) and isinstance(after, str) and before != after:
        await memory_store.log_file_edit(path=path, tool_name=tool_name, before=before, after=after)
