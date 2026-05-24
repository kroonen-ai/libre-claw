# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


class SandboxViolation(PermissionError):
    """Raised when a tool request violates the configured sandbox."""


_REMOTE_INSTALL_RE = re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:sh|bash)\b", re.IGNORECASE)
_SUDO_RE = re.compile(r"(^|[;&|]\s*)sudo(\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class SandboxPolicy:
    working_directory: Path
    restrict_to_working_dir: bool = True
    command_timeout: int = 120
    allow_sudo: bool = False
    blocked_patterns: tuple[str, ...] = ()

    def resolve_path(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.working_directory / candidate
        resolved = candidate.resolve()

        if self.restrict_to_working_dir:
            root = self.working_directory.resolve()
            if not _is_relative_to(resolved, root):
                raise SandboxViolation(f"Path is outside the working directory: {resolved}")

        return resolved

    def validate_command(self, command: str) -> None:
        normalized = " ".join(command.strip().split())
        lower_normalized = normalized.lower()
        for pattern in self.blocked_patterns:
            if pattern and pattern.lower() in lower_normalized:
                raise SandboxViolation(f"Command blocked by sandbox pattern: {pattern}")

        if not self.allow_sudo and _SUDO_RE.search(command):
            raise SandboxViolation("Command blocked by sandbox: sudo is disabled")

        if _REMOTE_INSTALL_RE.search(command):
            raise SandboxViolation("Command blocked by sandbox: remote install pipe is disabled")

        parts = _split_command(command)
        if _is_recursive_root_rm(parts):
            raise SandboxViolation("Command blocked by sandbox: recursive removal of root is disabled")


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _is_recursive_root_rm(parts: list[str]) -> bool:
    if not parts or parts[0] != "rm":
        return False

    flags = [part for part in parts[1:] if part.startswith("-") and part != "--"]
    targets = [part for part in parts[1:] if not part.startswith("-") and part != "--"]
    recursive_force = any("r" in flag and "f" in flag for flag in flags)
    return recursive_force and any(target in {"/", "/*"} for target in targets)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
