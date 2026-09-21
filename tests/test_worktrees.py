# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from libre_claw.config import load_config
from libre_claw.core.git_review import ReviewError
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.tools import ToolContext
from libre_claw.core.worktrees import WorktreeError, WorktreeManager


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Worktree Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "example.txt").write_text("original\n")
    (root / "unrelated.txt").write_text("unrelated\n")
    (root / ".gitignore").write_text("ignored.txt\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Create worktree fixture")
    return root


async def test_create_isolated_worktree_and_explicit_branch(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    (repo / "example.txt").write_text("user dirty\n")
    worktree = await manager.create(repo, "task-1", branch="agent-feature")
    assert (Path(worktree.path) / "example.txt").read_text() == "original\n"
    assert git(Path(worktree.path), "branch", "--show-current") == "agent-feature"
    assert git(repo, "branch", "--show-current") == "main"
    assert (repo / "example.txt").read_text() == "user dirty\n"
    assert (await manager.list(repo)) == [worktree]
    assert (await manager.associate(worktree.worktree_id, "task-2")).run_id == "task-2"


async def test_copy_changes_preserves_source_staging_and_untracked(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    (repo / "example.txt").write_text("staged\n")
    git(repo, "add", "example.txt")
    (repo / "example.txt").write_text("working\n")
    (repo / "new.txt").write_text("untracked\n")
    (repo / "ignored.txt").write_text("ignored\n")
    staged = git(repo, "diff", "--cached")
    worktree = await manager.create(repo, "task", include_changes=True)
    path = Path(worktree.path)
    assert (path / "example.txt").read_text() == "working\n"
    assert (path / "new.txt").read_text() == "untracked\n"
    assert not (path / "ignored.txt").exists()
    assert git(repo, "diff", "--cached") == staged
    assert (await manager.preview_transfer(worktree.worktree_id)).review.patch == ""


async def test_transfer_preserves_unrelated_dirty_and_staged_files(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    (Path(worktree.path) / "example.txt").write_text("agent change\n")
    (repo / "unrelated.txt").write_text("user staged\n")
    git(repo, "add", "unrelated.txt")
    (repo / "user-new.txt").write_text("keep me\n")
    staged = git(repo, "diff", "--cached")
    preview = await manager.preview_transfer(worktree.worktree_id)
    after = await manager.apply_to_source(worktree.worktree_id, expected_revision=preview.review.revision, expected_target_revision=preview.target_revision)
    assert not after.review.patch
    assert (repo / "example.txt").read_text() == "agent change\n"
    assert (repo / "user-new.txt").read_text() == "keep me\n"
    assert git(repo, "diff", "--cached") == staged


async def test_transfer_refuses_stale_target_and_overlapping_dirty_edit(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    (Path(worktree.path) / "example.txt").write_text("agent change\n")
    preview = await manager.preview_transfer(worktree.worktree_id)
    (repo / "example.txt").write_text("user edit\n")
    with pytest.raises(WorktreeError, match="changed since review"):
        await manager.apply_to_source(worktree.worktree_id, expected_revision=preview.review.revision, expected_target_revision=preview.target_revision)
    fresh = await manager.preview_transfer(worktree.worktree_id)
    with pytest.raises(ReviewError):
        await manager.apply_to_source(worktree.worktree_id, expected_revision=fresh.review.revision, expected_target_revision=fresh.target_revision)
    assert (repo / "example.txt").read_text() == "user edit\n"


async def test_cleanup_requires_clean_inactive_worktree(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    with pytest.raises(WorktreeError, match="active task"):
        await manager.remove(worktree.worktree_id, active_run_ids=["task"])
    (Path(worktree.path) / "ignored.txt").write_text("keep me")
    with pytest.raises(WorktreeError, match="ignored files"):
        await manager.remove(worktree.worktree_id, active_run_ids=[])
    (Path(worktree.path) / "ignored.txt").unlink()
    await manager.remove(worktree.worktree_id, active_run_ids=[])
    assert not Path(worktree.path).exists() and not await manager.list()


async def test_cleanup_protects_unmerged_commits(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    path = Path(worktree.path)
    (path / "new.txt").write_text("unique commit\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "Record isolated work")
    with pytest.raises(WorktreeError, match="commits absent"):
        await manager.remove(worktree.worktree_id, active_run_ids=[])
    assert path.exists()


async def test_reject_unsafe_ids_roots_and_option_refs(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    with pytest.raises(WorktreeError, match="Invalid"):
        await manager.get("../../outside")
    with pytest.raises(WorktreeError, match="outside"):
        await WorktreeManager(repo / "worktrees").create(repo, "task")
    with pytest.raises(ReviewError, match="valid Git"):
        await manager.create(repo, "task", ref="--help")


async def test_selected_ref_starts_exact_commit_and_cleanup_keeps_branch(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    git(repo, "checkout", "-b", "existing-feature")
    (repo / "feature.txt").write_text("existing feature\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Record existing branch fixture")
    git(repo, "checkout", "main")
    worktree = await manager.create(repo, "task", ref="existing-feature")
    assert (Path(worktree.path) / "feature.txt").read_text() == "existing feature\n"
    await manager.remove(worktree.worktree_id, active_run_ids=[])
    assert git(repo, "rev-parse", "existing-feature") == worktree.base_oid
    with pytest.raises(WorktreeError, match="source HEAD"):
        await manager.create(repo, "task", ref="existing-feature", include_changes=True)


async def test_transfer_refuses_moved_source_head_even_with_identical_files(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    (Path(worktree.path) / "example.txt").write_text("agent change\n")
    git(repo, "commit", "--allow-empty", "-m", "Move source branch")
    preview = await manager.preview_transfer(worktree.worktree_id)
    with pytest.raises(WorktreeError, match="source branch moved"):
        await manager.apply_to_source(worktree.worktree_id, expected_revision=preview.review.revision, expected_target_revision=preview.target_revision)
    assert (repo / "example.txt").read_text() == "original\n"


async def test_setup_runs_only_after_ordinary_shell_approval(repo: Path, tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path / "managed")
    worktree = await manager.create(repo, "task")
    permissions = PermissionManager(load_config(working_directory=repo).permissions)
    context = ToolContext(working_directory=repo)
    command = "printf ready > setup.txt"
    with pytest.raises(WorktreeError, match="explicit shell approval"):
        await manager.run_setup(worktree.worktree_id, [command], context=context, permission_manager=permissions)
    assert not (Path(worktree.path) / "setup.txt").exists()
    calls = []

    async def approve(call):
        calls.append(call)
        return "allow_once"

    results = await manager.run_setup(worktree.worktree_id, [command], context=context, permission_manager=permissions, request_permission=approve)
    assert len(calls) == 1 and not results[0].is_error
    assert (Path(worktree.path) / "setup.txt").read_text() == "ready"
    assert not (repo / "setup.txt").exists()
