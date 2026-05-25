# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_SOUL_TEMPLATE = """# Libre Claw Soul

Use this file to personalize how Libre Claw feels when it works with you.
These notes shape voice, taste, collaboration style, and recurring context.
They do not override safety rules, tool permissions, sandbox settings, or direct user instructions.

## Persona
- Be direct, warm, and practical.
- Prefer concise progress updates.
- Keep Kroonen AI Inc. visible as the maker when identity comes up.

## Working Style
- Read before editing.
- Explain tradeoffs when there are multiple reasonable paths.
- Verify changes before calling work done.
"""


class SoulError(Exception):
    """Raised when a soul file cannot be managed safely."""


@dataclass(frozen=True)
class SoulFragment:
    scope: str
    path: Path
    content: str

    @property
    def prompt_text(self) -> str:
        return f"Soul scope: {self.scope}\nPath: {self.path}\n\n{self.content}"


class SoulStore:
    """Load user and project persona files for system-prompt injection."""

    def __init__(self, working_directory: Path, *, home: Path | None = None) -> None:
        self.working_directory = working_directory.expanduser().resolve()
        self.home = (home or Path.home()).expanduser().resolve()

    @property
    def user_path(self) -> Path:
        return self.home / ".libre-claw" / "soul.md"

    @property
    def project_path(self) -> Path:
        return self.working_directory / ".libre-claw" / "soul.md"

    @property
    def project_root_path(self) -> Path:
        return self.working_directory / "soul.md"

    def paths(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("user", self.user_path),
            ("project", self.project_path),
            ("project-root", self.project_root_path),
        )

    def load(self) -> list[SoulFragment]:
        fragments: list[SoulFragment] = []
        seen: set[Path] = set()
        for scope, path in self.paths():
            resolved = path.expanduser().resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            content = resolved.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                fragments.append(SoulFragment(scope=scope, path=resolved, content=content))
        return fragments

    def soul_texts(self) -> list[str]:
        return [fragment.prompt_text for fragment in self.load()]

    def status_text(self) -> str:
        loaded = {fragment.path for fragment in self.load()}
        lines = ["Libre Claw soul files:"]
        for scope, path in self.paths():
            resolved = path.expanduser().resolve()
            state = "loaded" if resolved in loaded else "missing"
            lines.append(f"- {scope}: {resolved} ({state})")
        lines.append("")
        lines.append("Use `/soul init --user` or `/soul init --project` to create a template.")
        return "\n".join(lines)

    def combined_text(self) -> str:
        fragments = self.load()
        if not fragments:
            return self.status_text()
        return "\n\n---\n\n".join(fragment.prompt_text for fragment in fragments)

    def ensure_template(self, scope: str) -> Path:
        normalized = scope.strip().lower()
        if normalized in {"", "user", "--user"}:
            path = self.user_path
        elif normalized in {"project", "--project"}:
            path = self.project_path
        elif normalized in {"root", "--root", "project-root"}:
            path = self.project_root_path
        else:
            raise SoulError("Usage: /soul init --user|--project|--root")

        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_SOUL_TEMPLATE, encoding="utf-8")
        return path
