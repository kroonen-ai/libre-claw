# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any


@dataclass(frozen=True)
class ProjectInstruction:
    path: Path
    scope: Path | None
    text: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def render(self) -> str:
        scope = str(self.scope) if self.scope else "all workspaces"
        return f"Source: {self.path}\nScope: {scope}\n{self.text}"


class InstructionLoader:
    """Discover bounded instructions, from global to the most specific scope."""

    def __init__(
        self,
        working_directory: Path,
        *,
        global_path: Path | None = None,
        max_file_bytes: int = 16_384,
        max_total_bytes: int = 49_152,
        max_directories: int = 64,
    ) -> None:
        self.working_directory = working_directory.expanduser().resolve()
        self.global_path = global_path or Path.home() / ".libre-claw" / "AGENTS.md"
        self.max_file_bytes = max(1, max_file_bytes)
        self.max_total_bytes = max(1, max_total_bytes)
        self.max_directories = max(1, max_directories)
        self.root = self._find_root()

    def _find_root(self) -> Path:
        for directory in (self.working_directory, *self.working_directory.parents):
            if (directory / ".git").exists():
                return directory
        return self.working_directory

    def load(self, paths: Sequence[Path] = ()) -> list[ProjectInstruction]:
        directories: set[Path] = {self.root}
        for path in (self.working_directory, *paths[:32]):
            resolved = path.expanduser().resolve()
            if not resolved.is_relative_to(self.root):
                continue
            directory = resolved if resolved.is_dir() else resolved.parent
            while directory.is_relative_to(self.root):
                if directory not in directories and len(directories) >= self.max_directories:
                    break
                directories.add(directory)
                if directory == self.root or len(directories) >= self.max_directories:
                    break
                directory = directory.parent
            if len(directories) >= self.max_directories:
                break
        candidates: list[tuple[Path, Path | None]] = [(self.global_path, None)]
        for directory in sorted(directories, key=lambda value: (len(value.parts), str(value))):
            # AGENTS.md is canonical; the older repository spelling is a fallback.
            canonical = directory / "AGENTS.md"
            candidate = canonical if canonical.exists() else directory / "AGENT.md"
            candidates.append((candidate, directory))
        remaining = self.max_total_bytes
        instructions: list[ProjectInstruction] = []
        for path, scope in candidates:
            if remaining <= 0:
                break
            text = self._read(path, min(self.max_file_bytes, remaining))
            if not text:
                continue
            remaining -= len(text.encode())
            instructions.append(ProjectInstruction(path=path, scope=scope, text=text))
        return instructions

    @staticmethod
    def _read(path: Path, limit: int) -> str:
        try:
            # Do not follow symlinks or block on pipes/devices masquerading as instructions.
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return ""
                data = handle.read(limit + 1)
            text = data[:limit].decode("utf-8", errors="replace").strip()
            if len(data) > limit:
                text += "\n[Instruction file truncated at the configured size limit.]"
            return text
        except (OSError, ValueError):
            return ""


def tool_paths(arguments: Mapping[str, Any], working_directory: Path) -> list[Path]:
    """Inspect only explicit path fields, including atomic patch edit entries."""
    values: list[str] = []
    for name in ("path", "directory", "cwd", "working_directory", "output_path"):
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            values.append(value)
    for item in list(arguments.get("edits", []))[:50] if isinstance(arguments.get("edits"), list) else ():
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            values.append(item["path"])
    paths = []
    for value in values[:64]:
        path = Path(value).expanduser()
        paths.append((working_directory / path).resolve() if not path.is_absolute() else path.resolve())
    return paths


def render_instructions(instructions: Sequence[ProjectInstruction]) -> str:
    if not instructions:
        return ""
    return (
        "Project instructions. Global instructions apply first; deeper directory instructions "
        "override broader instructions only within their stated scope. AGENTS.md takes precedence "
        "over AGENT.md in the same directory. Direct user instructions, tool permissions and "
        "sandbox boundaries still take precedence. Follow scoped instructions only for matching paths.\n\n"
        + "\n\n---\n\n".join(item.render() for item in instructions)
    )
