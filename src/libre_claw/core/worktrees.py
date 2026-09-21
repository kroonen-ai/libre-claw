# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from libre_claw.core.git_review import (
    ReviewError,
    ReviewSnapshot,
    atomic_json,
    create_checkpoint,
    git_bytes,
    repository_lock,
    repository_root,
    resolve_commit,
    review_between,
    validate_repo_path,
)
from libre_claw.core.permissions import PermissionManager, PermissionResolution
from libre_claw.core.tools import ToolCall, ToolContext, ToolResult
from libre_claw.tools_builtin.shell import BashTool


class WorktreeError(ReviewError):
    """Managed worktree operation cannot be completed without losing work."""


@dataclass(frozen=True)
class ManagedWorktree:
    worktree_id: str
    repository: str
    path: str
    run_id: str
    base_oid: str
    initial_tree_oid: str
    branch: str | None
    included_changes: bool
    created_at: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransferPreview:
    worktree: ManagedWorktree
    review: ReviewSnapshot
    target_revision: str
    target_head: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "worktree": self.worktree.to_payload(), "review": self.review.to_payload(),
            "target_revision": self.target_revision, "target_head": self.target_head,
        }


PermissionCallback = Callable[[ToolCall], Awaitable[PermissionResolution]]


class WorktreeManager:
    """Own isolated checkouts; never reset, force-remove, commit, or execute setup implicitly."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    async def create(
        self, repository: Path | str, run_id: str, *, ref: str = "HEAD",
        branch: str | None = None, include_changes: bool = False,
    ) -> ManagedWorktree:
        repo = await repository_root(repository)
        if not run_id.strip():
            raise WorktreeError("A worktree must be associated with a task ID.")
        if self.root.is_relative_to(repo):
            raise WorktreeError("Managed worktrees must be stored outside the source repository.")
        base = await resolve_commit(repo, ref)
        source_head = await resolve_commit(repo, "HEAD")
        if include_changes and base != source_head:
            raise WorktreeError("Copying uncommitted changes requires starting at the source HEAD.")
        if branch is not None:
            if not branch or branch.startswith("-") or branch != branch.strip():
                raise WorktreeError("Provide a valid new branch name.")
            await git_bytes(repo, "check-ref-format", "--branch", branch)
        worktree_id = uuid4().hex
        path = self.root / "checkouts" / worktree_id
        async with repository_lock(repo):
            initial_tree = await create_checkpoint(repo) if include_changes else (await git_bytes(repo, "rev-parse", base + "^{tree}")).decode().strip()
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            arguments = ["worktree", "add"]
            arguments.extend(["-b", branch] if branch is not None else ["--detach"])
            arguments.extend([str(path), base])
            await git_bytes(repo, *arguments)
            record = ManagedWorktree(worktree_id, str(repo), str(path), run_id, base, initial_tree, branch, include_changes, datetime.now(timezone.utc).isoformat())
            # Record ownership immediately so failures leave a discoverable checkout for recovery.
            await self._save(record)
            await git_bytes(repo, "update-ref", f"refs/libre-claw/worktrees/{worktree_id}", initial_tree)
            if include_changes:
                base_tree = (await git_bytes(repo, "rev-parse", base + "^{tree}")).decode().strip()
                review = await review_between(repo, base_tree, initial_tree)
                if review.patch:
                    data = review.patch.encode("utf-8", "surrogateescape")
                    await git_bytes(path, "apply", "--check", "-", stdin=data)
                    await git_bytes(path, "apply", "-", stdin=data)
            return record

    async def get(self, worktree_id: str) -> ManagedWorktree:
        self._validate_id(worktree_id)
        try:
            payload = await asyncio.to_thread(self._metadata_path(worktree_id).read_text, encoding="utf-8")
            record = ManagedWorktree(**json.loads(payload))
        except (OSError, ValueError, TypeError) as exc:
            raise WorktreeError("Managed worktree was not found or its metadata is invalid.") from exc
        expected_path = self.root / "checkouts" / worktree_id
        if record.worktree_id != worktree_id or Path(record.path) != expected_path or expected_path.is_symlink():
            raise WorktreeError("Managed worktree metadata points outside its owned checkout.")
        return record

    async def list(self, repository: Path | str | None = None) -> list[ManagedWorktree]:
        repo = str(await repository_root(repository)) if repository is not None else None
        paths = await asyncio.to_thread(lambda: sorted((self.root / "records").glob("*.json")))
        records = [await self.get(path.stem) for path in paths]
        return [record for record in records if repo is None or record.repository == repo]

    async def associate(self, worktree_id: str, run_id: str) -> ManagedWorktree:
        if not run_id.strip():
            raise WorktreeError("A worktree must be associated with a task ID.")
        record = replace(await self.get(worktree_id), run_id=run_id)
        await self._save(record)
        return record

    async def remove(self, worktree_id: str, *, active_run_ids: Collection[str]) -> None:
        record = await self.get(worktree_id)
        if record.run_id in active_run_ids:
            raise WorktreeError("Stop the active task before removing its worktree.")
        repo, path = Path(record.repository), Path(record.path)
        async with repository_lock(repo):
            if await repository_root(path) != path:
                raise WorktreeError("The managed checkout no longer points to its registered worktree.")
            dirty = await git_bytes(path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")
            if dirty:
                raise WorktreeError("This worktree contains changes or untracked/ignored files; preserve them before removal.")
            # Protect detached commits and local branch work that never reached the source.
            head = await resolve_commit(path, "HEAD")
            source_head = await resolve_commit(repo, "HEAD")
            if head != record.base_oid:
                try:
                    await git_bytes(repo, "merge-base", "--is-ancestor", head, source_head)
                except ReviewError as exc:
                    raise WorktreeError("This worktree has commits absent from the source branch; preserve them before removal.") from exc
            await git_bytes(repo, "worktree", "remove", str(path))
            await git_bytes(repo, "update-ref", "-d", f"refs/libre-claw/worktrees/{worktree_id}")
            await asyncio.to_thread(self._metadata_path(worktree_id).unlink)

    async def preview_transfer(self, worktree_id: str) -> TransferPreview:
        record = await self.get(worktree_id)
        repo, path = Path(record.repository), Path(record.path)
        if await repository_root(path) != path:
            raise WorktreeError("The managed checkout no longer points to its registered worktree.")
        current = await create_checkpoint(path)
        review = await review_between(path, record.initial_tree_oid, current)
        target = await create_checkpoint(repo)
        target_head = await resolve_commit(repo, "HEAD")
        return TransferPreview(record, review, target, target_head)

    async def apply_to_source(
        self, worktree_id: str, *, expected_revision: str, expected_target_revision: str,
    ) -> TransferPreview:
        record = await self.get(worktree_id)
        repo = Path(record.repository)
        async with repository_lock(repo):
            preview = await self.preview_transfer(worktree_id)
            if preview.review.revision != expected_revision or preview.target_revision != expected_target_revision:
                raise WorktreeError("The worktree or source changed since review. Refresh the transfer before applying it.")
            if preview.target_head != record.base_oid:
                raise WorktreeError("The source branch moved or differs from the worktree base; reconcile branches before applying.")
            for file in preview.review.files:
                validate_repo_path(repo, file.path)
            if not preview.review.patch:
                raise WorktreeError("There are no new worktree changes to bring back.")
            data = preview.review.patch.encode("utf-8", "surrogateescape")
            # No --3way or --index: preserve the user's staged state and refuse conflicts.
            await git_bytes(repo, "apply", "--check", "-", stdin=data)
            await git_bytes(repo, "apply", "-", stdin=data)
            # Future transfers contain only additional edits made after this reviewed application.
            updated = replace(record, initial_tree_oid=preview.review.target_oid)
            await self._save(updated)
            await git_bytes(repo, "update-ref", f"refs/libre-claw/worktrees/{worktree_id}", updated.initial_tree_oid)
            return await self.preview_transfer(worktree_id)

    async def run_setup(
        self, worktree_id: str, commands: Sequence[str], *,
        context: ToolContext, permission_manager: PermissionManager,
        request_permission: PermissionCallback | None = None,
    ) -> tuple[ToolResult, ...]:
        """Run only explicitly supplied setup commands through the ordinary shell permissions."""
        record = await self.get(worktree_id)
        path = Path(record.path)
        if await repository_root(path) != path:
            raise WorktreeError("The managed checkout no longer points to its registered worktree.")
        if len(commands) > 32 or any(not isinstance(command, str) or not command.strip() for command in commands):
            raise WorktreeError("Setup requires at most 32 nonempty commands.")
        tool = BashTool(replace(context, working_directory=path))
        results: list[ToolResult] = []
        for command in commands:
            tool.context.sandbox_policy().validate_command(command)
            call = ToolCall(uuid4().hex, tool.name, {"command": command})
            decision = permission_manager.check(call, tool)
            if decision == "deny":
                raise WorktreeError("Setup command is denied by the shell permission policy.")
            if decision == "ask":
                if request_permission is None:
                    raise WorktreeError("Setup command requires an explicit shell approval.")
                resolution = await request_permission(call)
                if not permission_manager.apply_resolution(call, resolution):
                    raise WorktreeError("Setup command was not approved.")
            result = await tool.execute(command=command)
            if not result.is_error and result.metadata.get("exit_code", 0) != 0:
                # Bash reports process exits as metadata so the agent can inspect
                # them. Setup is sequential: a failed prerequisite must stop the
                # remaining commands and be surfaced as a failure to every UI.
                result = replace(result, error=result.as_text())
            results.append(result)
            if result.is_error:
                break
        return tuple(results)

    async def _save(self, record: ManagedWorktree) -> None:
        await asyncio.to_thread(atomic_json, self._metadata_path(record.worktree_id), record.to_payload())

    def _metadata_path(self, worktree_id: str) -> Path:
        self._validate_id(worktree_id)
        return self.root / "records" / f"{worktree_id}.json"

    @staticmethod
    def _validate_id(worktree_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", worktree_id):
            raise WorktreeError("Invalid managed worktree ID.")
