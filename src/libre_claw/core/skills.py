# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, ParamSpec, TypeVar
from uuid import uuid4

SkillScope = Literal["bundled", "external", "user", "project"]
MAX_SKILL_CHARS = 6000
SCOPE_ORDER: dict[SkillScope, int] = {"bundled": 0, "external": 1, "user": 2, "project": 3}
MUTABLE_SCOPES: set[SkillScope] = {"user", "project"}
EXTERNAL_CACHE_SENTINEL = ".libre-claw-synced-at"
IGNORED_SKILL_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv"}
SKILL_AUTHORING_GUIDANCE = (
    "Skill authoring guidance: when asked to create or improve a skill, use an "
    "AgentSkills-compatible SKILL.md structure with YAML frontmatter (`name`, "
    "`description`) and concise sections: When to Use, Prerequisites, Procedure, "
    "Pitfalls, Verification. Keep secrets out, keep descriptions short, reference "
    "Libre Claw tool names, and prefer deterministic scripts for fragile repeated work. "
    "If external skill discovery is enabled and a task needs specialized knowledge not "
    "covered by local skills, use `skills_search` to look for an existing open agent skill."
)
P = ParamSpec("P")
T = TypeVar("T")


class SkillError(RuntimeError):
    """Raised when a skill cannot be loaded or changed."""


@dataclass(frozen=True)
class Skill:
    name: str
    scope: SkillScope
    path: Path
    title: str
    description: str
    content: str

    @property
    def prompt_text(self) -> str:
        body = self.content.strip()
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS].rstrip() + "\n\n[Skill truncated by Libre Claw]"
        return "\n".join(
            [
                f"Skill: {self.title or self.name}",
                f"Scope: {self.scope}",
                f"Path: {self.path}",
                "",
                body,
            ]
        )


class SkillStore:
    """Loads bundled, user, and project skills for prompt injection and slash commands."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        user_root: Path | str | None = None,
        include_bundled: bool = True,
        skills_config: "SkillsConfig | None" = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.user_root = Path(user_root).expanduser() if user_root is not None else default_user_skills_path()
        self.project_skills_root = self.project_root / ".libre-claw" / "skills"
        self.bundled_root = default_bundled_skills_path()
        self.skills_config = skills_config
        self.include_bundled = include_bundled and _config_bool(skills_config, "include_bundled", True)
        self._external_sync_attempted = False

    async def list_skills(self) -> list[Skill]:
        return await _to_thread(self._list_skills_sync)

    async def add_skill(self, name: str, content: str, *, scope: SkillScope = "user") -> Skill:
        return await _to_thread(self._add_skill_sync, name, content, scope)

    async def edit_skill(self, name: str, content: str, *, scope: SkillScope | None = None) -> Skill:
        return await _to_thread(self._edit_skill_sync, name, content, scope)

    async def delete_skill(self, name: str, *, scope: SkillScope | None = None) -> bool:
        return await _to_thread(self._delete_skill_sync, name, scope)

    async def relevant_skills(self, prompt: str, *, limit: int = 5) -> list[Skill]:
        return await _to_thread(self._relevant_skills_sync, prompt, limit)

    def relevant_skill_texts(self, prompt: str, *, limit: int = 5) -> list[str]:
        configured_limit = _config_int(self.skills_config, "max_relevant", limit)
        return [skill.prompt_text for skill in self._relevant_skills_sync(prompt, min(limit, configured_limit))]

    async def sync_external_sources(self, *, force: bool = False) -> list[str]:
        return await _to_thread(self._sync_external_sources_sync, force)

    def _list_skills_sync(self) -> list[Skill]:
        if not _config_bool(self.skills_config, "enabled", True):
            return []
        if _config_bool(self.skills_config, "external_discovery_enabled", False):
            self._maybe_sync_external_sources()
        skills = []
        if self.include_bundled:
            skills.extend(self._load_scope("bundled", self.bundled_root))
        if _config_bool(self.skills_config, "external_discovery_enabled", False):
            for root in self._external_roots():
                skills.extend(self._load_scope("external", root))
        if _config_bool(self.skills_config, "include_user", True):
            skills.extend(self._load_scope("user", self.user_root))
        if _config_bool(self.skills_config, "include_project", True):
            skills.extend(self._load_scope("project", self.project_skills_root))
        skills.sort(key=lambda skill: (SCOPE_ORDER[skill.scope], skill.name))
        return skills

    def _load_scope(self, scope: SkillScope, root: Path) -> list[Skill]:
        if not root.exists():
            return []
        skills: list[Skill] = []
        if scope != "external":
            for path in sorted(root.glob("*.md")):
                skill = _load_skill_file(path, scope, path.stem)
                if skill is not None:
                    skills.append(skill)
        for path in sorted(_skill_markdown_paths(root)):
            skill = _load_skill_file(path, scope, path.parent.name)
            if skill is not None:
                skills.append(skill)
        return skills

    def _add_skill_sync(self, name: str, content: str, scope: SkillScope) -> Skill:
        slug = normalize_skill_name(name)
        root = self._root_for_scope(scope)
        path = root / f"{slug}.md"
        if path.exists() or (root / slug / "SKILL.md").exists():
            raise SkillError(f"Skill already exists: {slug}")
        _write_text(path, _skill_content(slug, content))
        skill = _load_skill_file(path, scope, slug)
        if skill is None:
            raise SkillError(f"Could not load skill after writing: {slug}")
        return skill

    def _edit_skill_sync(self, name: str, content: str, scope: SkillScope | None) -> Skill:
        existing = self._find_skill(name, scope=scope)
        if existing is None:
            raise SkillError(f"Skill not found: {name}")
        if existing.scope not in MUTABLE_SCOPES:
            raise SkillError(f"Bundled skill {existing.name} cannot be edited directly; add a user or project override.")
        _write_text(existing.path, _skill_content(existing.name, content))
        skill = _load_skill_file(existing.path, existing.scope, existing.name)
        if skill is None:
            raise SkillError(f"Could not load skill after editing: {existing.name}")
        return skill

    def _delete_skill_sync(self, name: str, scope: SkillScope | None) -> bool:
        existing = self._find_skill(name, scope=scope)
        if existing is None:
            return False
        if existing.scope not in MUTABLE_SCOPES:
            raise SkillError(f"Bundled skill {existing.name} cannot be deleted; add a user or project override.")
        existing.path.unlink()
        if existing.path.name == "SKILL.md":
            try:
                existing.path.parent.rmdir()
            except OSError:
                # Remove only empty skill directories; retain supporting files and assets.
                pass
        return True

    def _find_skill(self, name: str, *, scope: SkillScope | None = None) -> Skill | None:
        slug = normalize_skill_name(name)
        matches = [skill for skill in self._list_skills_sync() if skill.name == slug and (scope is None or skill.scope == scope)]
        if len(matches) > 1:
            raise SkillError(f"Skill name exists in multiple scopes; specify --user or --project: {slug}")
        return matches[0] if matches else None

    def _relevant_skills_sync(self, prompt: str, limit: int) -> list[Skill]:
        tokens = _tokens(prompt)
        if not tokens:
            return []
        scored: dict[str, tuple[int, Skill]] = {}
        for skill in self._list_skills_sync():
            haystack = _tokens(" ".join([skill.name, skill.title, skill.description, skill.content]))
            score = sum(3 if token in _tokens(skill.title + " " + skill.description) else 1 for token in tokens if token in haystack)
            if score > 0:
                existing = scored.get(skill.name)
                if existing is None or SCOPE_ORDER[skill.scope] > SCOPE_ORDER[existing[1].scope] or (
                    SCOPE_ORDER[skill.scope] == SCOPE_ORDER[existing[1].scope] and score > existing[0]
                ):
                    scored[skill.name] = (score, skill)
        ranked = list(scored.values())
        ranked.sort(key=lambda item: (-item[0], -SCOPE_ORDER[item[1].scope], item[1].name))
        return [skill for _, skill in ranked[: max(1, limit)]]

    def _root_for_scope(self, scope: SkillScope) -> Path:
        if scope in {"bundled", "external"}:
            raise SkillError(f"{scope.title()} skills are read-only.")
        return self.user_root if scope == "user" else self.project_skills_root

    def _maybe_sync_external_sources(self) -> None:
        if self._external_sync_attempted:
            return
        self._external_sync_attempted = True
        if not _config_bool(self.skills_config, "external_auto_refresh", True):
            return
        try:
            self._sync_external_sources_sync(force=False)
        except SkillError:
            return

    def _sync_external_sources_sync(self, force: bool = False) -> list[str]:
        if not _config_bool(self.skills_config, "external_discovery_enabled", False):
            raise SkillError("External skill discovery is disabled by [skills].external_discovery_enabled.")
        statuses: list[str] = []
        if _config_bool(self.skills_config, "vercel_source_enabled", True):
            root = self._vercel_cache_root()
            statuses.append(
                _sync_git_catalog(
                    root=root,
                    url=_config_str(self.skills_config, "vercel_repo_url", "https://github.com/vercel-labs/skills.git"),
                    ref=_config_str(self.skills_config, "vercel_ref", "main"),
                    refresh_seconds=_config_int(self.skills_config, "external_refresh_seconds", 86400),
                    force=force,
                )
            )
        return statuses

    def _external_roots(self) -> list[Path]:
        roots: list[Path] = []
        if _config_bool(self.skills_config, "vercel_source_enabled", True):
            root = self._vercel_cache_root()
            if root.exists():
                roots.append(root)
        return roots

    def _vercel_cache_root(self) -> Path:
        cache_dir = _config_path(self.skills_config, "external_cache_dir", Path.home() / ".libre-claw" / "skills" / "catalogs")
        return cache_dir / "vercel-labs-skills"


def default_user_skills_path() -> Path:
    return Path.home() / ".libre-claw" / "skills"


def default_bundled_skills_path() -> Path:
    return Path(__file__).resolve().parents[1] / "skills"


def _skill_markdown_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("SKILL.md"):
        if any(part in IGNORED_SKILL_DIRS for part in path.parts):
            continue
        paths.append(path)
    return paths


def normalize_skill_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-._")
    if not slug:
        raise SkillError("Skill name cannot be empty.")
    if slug in {".", ".."} or "/" in slug or "\\" in slug:
        raise SkillError(f"Invalid skill name: {name}")
    return slug[:80]


def _load_skill_file(path: Path, scope: SkillScope, name: str) -> Skill | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    title = _first_heading(content) or name.replace("-", " ").title()
    description = _description(content)
    return Skill(
        name=normalize_skill_name(name),
        scope=scope,
        path=path,
        title=title,
        description=description,
        content=content,
    )


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _description(content: str) -> str:
    in_frontmatter = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and stripped.lower().startswith("description:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped != "---":
            return stripped[:240]
    return ""


def _skill_content(name: str, content: str) -> str:
    body = content.strip()
    if not body:
        body = "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: Repeatable {name.replace('-', ' ')} workflow.",
                "---",
                "",
                f"# {name.replace('-', ' ').title()}",
                "",
                "Use this skill when this workflow appears again.",
                "",
                "## When to Use",
                "",
                "- Describe the user requests or task patterns that should trigger this skill.",
                "",
                "## Prerequisites",
                "",
                "- List required tools, credentials, files, or APIs. Do not include secrets.",
                "",
                "## Procedure",
                "",
                "1. Inspect the relevant context first.",
                "2. Use the smallest reliable set of tools.",
                "3. Keep intermediate scratch output out of the final answer.",
                "",
                "## Pitfalls",
                "",
                "- Note common failure modes and what to avoid.",
                "",
                "## Verification",
                "",
                "- State how the agent should know the task is done.",
                "",
            ]
        )
    if not body.endswith("\n"):
        body += "\n"
    return body


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9][a-z0-9._-]{1,}", text.lower()) if token not in _STOPWORDS}


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _sync_git_catalog(*, root: Path, url: str, ref: str, refresh_seconds: int, force: bool) -> str:
    now = time.time()
    sentinel = root / EXTERNAL_CACHE_SENTINEL
    if root.exists() and not force and sentinel.exists():
        try:
            age = now - float(sentinel.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            age = refresh_seconds + 1
        if age < refresh_seconds:
            return f"{root.name}: fresh"

    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists() and (root / ".git").exists():
        command = ["git", "-C", str(root), "fetch", "--depth", "1", "origin", ref]
        _run_git(command)
        _run_git(["git", "-C", str(root), "checkout", "--quiet", "FETCH_HEAD"])
    elif root.exists():
        raise SkillError(f"External skills cache path exists but is not a git checkout: {root}")
    else:
        _run_git(["git", "clone", "--depth", "1", "--branch", ref, url, str(root)])

    try:
        sentinel.write_text(str(now), encoding="utf-8")
    except OSError:
        # The checkout is synced; an unavailable timestamp only forces an earlier refresh.
        pass
    return f"{root.name}: synced"


def _run_git(command: list[str]) -> None:
    try:
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError(f"Could not refresh external skills catalog: {exc}") from exc
    if completed.returncode != 0:
        stderr = " ".join(completed.stderr.split())
        raise SkillError(f"Could not refresh external skills catalog: {stderr or completed.returncode}")


def _config_bool(config: object | None, name: str, default: bool) -> bool:
    return bool(getattr(config, name, default)) if config is not None else default


def _config_int(config: object | None, name: str, default: int) -> int:
    value = getattr(config, name, default) if config is not None else default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _config_str(config: object | None, name: str, default: str) -> str:
    value = getattr(config, name, default) if config is not None else default
    return str(value).strip() or default


def _config_path(config: object | None, name: str, default: Path) -> Path:
    value = getattr(config, name, default) if config is not None else default
    return Path(value).expanduser()


async def _to_thread(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


_STOPWORDS = {
    "and",
    "an",
    "as",
    "at",
    "be",
    "by",
    "do",
    "for",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "no",
    "of",
    "on",
    "or",
    "the",
    "this",
    "that",
    "to",
    "with",
    "from",
    "into",
    "when",
    "then",
    "have",
    "your",
    "you",
    "use",
    "using",
}
