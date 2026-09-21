# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from libre_claw.core.git_review import ReviewCommentStore, ReviewError, capture_review, create_checkpoint, mutate_review


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Review Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "example.txt").write_text("".join(f"line {index}\n" for index in range(30)))
    (root / ".gitignore").write_text("ignored.txt\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Create review fixture")
    return root


async def test_stage_unstage_and_revert_individual_hunks(repo: Path) -> None:
    path = repo / "example.txt"
    path.write_text(path.read_text().replace("line 2\n", "changed 2\n").replace("line 24\n", "changed 24\n"))
    review = await capture_review(repo)
    assert len(review.files[0].hunks) == 2
    await mutate_review(repo, "stage", expected_revision=review.revision, path="example.txt", hunk_id=review.files[0].hunks[0].hunk_id)
    assert "+changed 2" in git(repo, "diff", "--cached")
    assert "+changed 24" not in git(repo, "diff", "--cached")
    staged = await capture_review(repo, "staged")
    await mutate_review(repo, "unstage", expected_revision=staged.revision, path="example.txt", hunk_id=staged.files[0].hunks[0].hunk_id)
    assert git(repo, "diff", "--cached") == ""
    unstaged = await capture_review(repo)
    await mutate_review(repo, "revert", expected_revision=unstaged.revision, path="example.txt", hunk_id=unstaged.files[0].hunks[0].hunk_id)
    assert "changed 2\n" not in path.read_text()
    assert "changed 24\n" in path.read_text()


async def test_stale_revision_refuses_to_overwrite_new_user_edit(repo: Path) -> None:
    path = repo / "example.txt"
    path.write_text("first edit\n")
    review = await capture_review(repo)
    path.write_text("new user edit\n")
    with pytest.raises(ReviewError, match="changed since review"):
        await mutate_review(repo, "revert", expected_revision=review.revision, path="example.txt")
    assert path.read_text() == "new user edit\n"


async def test_stage_untracked_binary_and_revert_deleted_file(repo: Path) -> None:
    (repo / "new.bin").write_bytes(b"\0new data\xff")
    review = await capture_review(repo)
    added = next(file for file in review.files if file.path == "new.bin")
    assert added.binary and added.status == "added"
    await mutate_review(repo, "stage", expected_revision=review.revision, path="new.bin")
    assert "new.bin" in git(repo, "diff", "--cached", "--name-only")
    (repo / "example.txt").unlink()
    deleted = await capture_review(repo)
    await mutate_review(repo, "revert", expected_revision=deleted.revision, path="example.txt")
    assert (repo / "example.txt").read_text().startswith("line 0\n")
    assert "new.bin" in git(repo, "diff", "--cached", "--name-only")


async def test_checkpoint_preserves_index_and_last_turn_scope(repo: Path) -> None:
    path = repo / "example.txt"
    path.write_text("staged\n")
    git(repo, "add", "example.txt")
    path.write_text("unstaged\n")
    (repo / "ignored.txt").write_text("ignored")
    staged = git(repo, "diff", "--cached")
    checkpoint = await create_checkpoint(repo)
    path.write_text("next turn\n")
    review = await capture_review(repo, "last-turn", checkpoint=checkpoint)
    assert "-unstaged" in review.patch and "+next turn" in review.patch
    assert "ignored.txt" not in review.patch
    assert git(repo, "diff", "--cached") == staged


async def test_branch_scope_contains_committed_and_pending_changes(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature")
    (repo / "committed.txt").write_text("feature\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Add feature fixture")
    (repo / "pending.txt").write_text("pending\n")
    review = await capture_review(repo, "branch", base_ref="main")
    assert {file.path for file in review.files} == {"committed.txt", "pending.txt"}
    with pytest.raises(ReviewError, match="requires"):
        await capture_review(repo, "branch")


async def test_named_checkpoint_is_retained_by_a_private_git_ref(repo: Path) -> None:
    tree = await create_checkpoint(repo, name="task/turn-1")
    assert tree in git(repo, "show-ref")


async def test_review_preserves_second_hunk_after_line_count_changes(repo: Path) -> None:
    path = repo / "example.txt"
    path.write_text(path.read_text().replace("line 2\n", "added a\nadded b\nadded c\n").replace("line 24\n", "changed 24\n"))
    initial = await capture_review(repo)
    await mutate_review(repo, "stage", expected_revision=initial.revision, path="example.txt", hunk_id=initial.files[0].hunks[1].hunk_id)
    assert "+changed 24" in git(repo, "diff", "--cached")
    assert "+added a" not in git(repo, "diff", "--cached")
    remainder = await capture_review(repo)
    await mutate_review(repo, "revert", expected_revision=remainder.revision, path="example.txt")
    assert "added a" not in path.read_text() and "changed 24" in path.read_text()


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", ".git/config", ":(glob)**/../outside", "folder/../../outside"])
async def test_mutations_refuse_unsafe_paths(repo: Path, path: str) -> None:
    review = await capture_review(repo)
    with pytest.raises(ReviewError):
        await mutate_review(repo, "stage", expected_revision=review.revision, path=path)


async def test_literal_filename_and_symlink_safety(repo: Path) -> None:
    (repo / ":(glob)*.txt").write_text("literal\n")
    (repo / "another.txt").write_text("other\n")
    review = await capture_review(repo)
    await mutate_review(repo, "stage", expected_revision=review.revision, path=":(glob)*.txt")
    assert git(repo, "diff", "--cached", "--name-only") == ":(glob)*.txt"
    outside = repo.parent / "outside.txt"
    outside.write_text("outside\n")
    (repo / "link.txt").symlink_to(outside)
    with pytest.raises(ReviewError, match="outside|symlink"):
        await mutate_review(repo, "stage", expected_revision=(await capture_review(repo)).revision, path="link.txt")
    assert outside.read_text() == "outside\n"


async def test_review_comments_are_scoped_and_preserve_line(repo: Path, tmp_path: Path) -> None:
    store = ReviewCommentStore(tmp_path / "comments")
    (repo / "example.txt").write_text((repo / "example.txt").read_text().replace("line 4", "changed 4"))
    review = await capture_review(repo)
    comment = await store.add(repo, "run/one", path="example.txt", line=4, side="right", body="Check this line.", revision=review.revision)
    assert (await store.list(repo, "run/one")) == [comment]
    assert (await store.list(repo, "other")) == []
    assert comment.line == 4 and comment.path == "example.txt"
    with pytest.raises(ReviewError, match="positive line"):
        await store.add(repo, "run", path="example.txt", line=0, side="left", body="bad", revision="revision")
    with pytest.raises(ReviewError, match="selected line"):
        await store.add(repo, "run", path="example.txt", line=29, side="right", body="bad", revision=review.revision)
    with pytest.raises(ReviewError, match="changed since review"):
        await store.add(repo, "run", path="example.txt", line=4, side="right", body="bad", revision="stale")
