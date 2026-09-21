# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
import stat
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


@pytest.mark.parametrize("action", ["stage", "unstage", "revert"])
@pytest.mark.parametrize("status", ["added", "deleted"])
async def test_added_and_deleted_text_hunks_preserve_other_work(repo: Path, action: str, status: str) -> None:
    relative = "new.txt" if status == "added" else "example.txt"
    path = repo / relative
    contents = "new text\nwithout final newline" if status == "added" else path.read_text()
    if status == "added":
        path.write_text(contents)
        path.chmod(0o755)
    else:
        path.unlink()
    if action == "unstage":
        git(repo, "add", "--", relative)
        # Unstaging must only touch the index, including when a deleted file has
        # since been replaced in the working tree by new user content.
        path.write_text("newer user edit\n")
    (repo / "unrelated.txt").write_text("unrelated staged\n")
    git(repo, "add", "unrelated.txt")
    (repo / "unrelated.txt").write_text("unrelated working\n")
    scope = "staged" if action == "unstage" else "unstaged"
    review = await capture_review(repo, scope)
    file = next(item for item in review.files if item.path == relative)
    assert file.status == status and len(file.hunks) == 1
    await mutate_review(repo, action, expected_revision=review.revision, path=relative, hunk_id=file.hunks[0].hunk_id)
    assert git(repo, "show", ":unrelated.txt") == "unrelated staged"
    assert (repo / "unrelated.txt").read_text() == "unrelated working\n"
    if action == "stage":
        if status == "added":
            assert git(repo, "show", ":new.txt") == contents
            assert git(repo, "ls-files", "--stage", "new.txt").startswith("100755 ")
            assert path.read_text() == contents
        else:
            assert not git(repo, "ls-files", "--", relative) and not path.exists()
    elif action == "unstage":
        assert path.read_text() == "newer user edit\n"
        if status == "added":
            assert not git(repo, "ls-files", "--", relative)
        else:
            assert git(repo, "show", ":example.txt") == contents.rstrip("\n")
    elif status == "added":
        assert not path.exists()
    else:
        assert path.read_text() == contents


@pytest.mark.parametrize("action", ["stage", "unstage", "revert"])
async def test_content_hunks_leave_independent_mode_changes_alone(repo: Path, action: str) -> None:
    path = repo / "example.txt"
    path.write_text(path.read_text().replace("line 2\n", "changed 2\n").replace("line 24\n", "changed 24\n"))
    path.chmod(0o755)
    if action == "unstage":
        git(repo, "add", "example.txt")
    scope = "staged" if action == "unstage" else "unstaged"
    review = await capture_review(repo, scope)
    assert "\nold mode " in review.files[0].patch and len(review.files[0].hunks) == 2
    await mutate_review(repo, action, expected_revision=review.revision, path="example.txt", hunk_id=review.files[0].hunks[1].hunk_id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    index = git(repo, "show", ":example.txt")
    expected_mode = "100755 " if action == "unstage" else "100644 "
    assert git(repo, "ls-files", "--stage", "example.txt").startswith(expected_mode)
    if action == "stage":
        assert "changed 24" in index and "changed 2\n" not in index
        assert "changed 2\n" in path.read_text()
    elif action == "unstage":
        assert "changed 24" not in index and "changed 2\n" in index
        assert "changed 24" in path.read_text()
    else:
        assert "changed 24" not in path.read_text() and "changed 2\n" in path.read_text()
        assert "changed" not in index


@pytest.mark.parametrize("name", [":(glob)*.txt", "space @@ name.txt", 'quote"\t\nname.txt'])
async def test_added_hunks_use_literal_git_paths(repo: Path, name: str) -> None:
    path = repo / name
    path.write_text("literal path\n")
    review = await capture_review(repo)
    file = next(item for item in review.files if item.path == name)
    await mutate_review(repo, "stage", expected_revision=review.revision, path=name, hunk_id=file.hunks[0].hunk_id)
    assert git(repo, "show", ":" + name) == "literal path"
    assert path.read_text() == "literal path\n"


async def test_stale_hunk_refuses_changed_index_and_working_file(repo: Path) -> None:
    path = repo / "example.txt"
    path.write_text("staged change\n")
    git(repo, "add", "example.txt")
    review = await capture_review(repo, "staged")
    path.write_text("newer staged change\n")
    git(repo, "add", "example.txt")
    path.write_text("newer working change\n")
    with pytest.raises(ReviewError, match="changed since review"):
        await mutate_review(repo, "unstage", expected_revision=review.revision, path="example.txt", hunk_id=review.files[0].hunks[0].hunk_id)
    assert git(repo, "show", ":example.txt") == "newer staged change"
    assert path.read_text() == "newer working change\n"


async def test_binary_hunk_requests_are_refused(repo: Path) -> None:
    (repo / "new.bin").write_bytes(b"\0binary\xff")
    review = await capture_review(repo)
    with pytest.raises(ReviewError, match="Binary files"):
        await mutate_review(repo, "stage", expected_revision=review.revision, path="new.bin", hunk_id="invalid")
    assert not git(repo, "diff", "--cached")


@pytest.mark.parametrize("action", ["stage", "unstage", "revert"])
async def test_type_change_hunks_do_not_apply_another_component_header(repo: Path, action: str) -> None:
    path = repo / "link.txt"
    path.symlink_to("example.txt")
    git(repo, "add", "link.txt")
    git(repo, "commit", "-m", "Record symlink fixture")
    target_content = (repo / "example.txt").read_text()
    path.unlink()
    path.write_text("regular file\n")
    if action == "unstage":
        git(repo, "add", "link.txt")
    scope = "staged" if action == "unstage" else "unstaged"
    review = await capture_review(repo, scope)
    file = next(item for item in review.files if item.path == "link.txt")
    assert file.status == "modified" and len(file.hunks) == 2
    assert all(hunk.patch.count("diff --git ") == 1 for hunk in file.hunks)
    hunk = file.hunks[0 if action == "stage" else 1]
    await mutate_review(repo, action, expected_revision=review.revision, path="link.txt", hunk_id=hunk.hunk_id)
    if action == "revert":
        assert not path.exists()
        assert git(repo, "ls-files", "--stage", "link.txt").startswith("120000 ")
    else:
        # Selecting the deletion component must remove the index entry, never
        # also stage an empty regular-file addition from the next header.
        assert not git(repo, "ls-files", "--", "link.txt")
        assert path.read_text() == "regular file\n"
    assert (repo / "example.txt").read_text() == target_content


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
