# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class UpdateResult:
    repo_root: Path
    local_head: str
    remote_head: str
    remote_ref: str
    backup_dir: Path | None
    updated: bool
    dry_run: bool = False


class UpdateError(RuntimeError):
    """Raised when the self-update command cannot safely continue."""


def update_checkout(
    repo_path: Path | None = None,
    *,
    remote: str = "origin",
    branch: str = "main",
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> UpdateResult:
    """Safely fast-forward a Libre Claw checkout after writing a rollback backup."""
    repo_root = _resolve_update_repo(repo_path)
    current_branch = _git_stdout(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if current_branch == "HEAD":
        raise UpdateError("Libre Claw update requires a branch checkout, but this repository is detached.")
    if current_branch != branch:
        raise UpdateError(f"Libre Claw update only updates `{branch}`. Current branch is `{current_branch}`.")

    remote_ref = f"{remote}/{branch}"
    if progress is not None:
        progress(f"Checking {remote_ref}...")
    _run_git(repo_root, ["fetch", "--prune", remote, branch])
    local_head = _git_stdout(repo_root, ["rev-parse", "HEAD"])
    remote_head = _git_stdout(repo_root, ["rev-parse", "--verify", remote_ref])
    if local_head == remote_head:
        return UpdateResult(repo_root, local_head, remote_head, remote_ref, None, updated=False, dry_run=dry_run)

    if dry_run:
        return UpdateResult(repo_root, local_head, remote_head, remote_ref, None, updated=True, dry_run=True)

    backup_dir = _create_update_backup(
        repo_root,
        remote=remote,
        branch=branch,
        local_head=local_head,
        remote_head=remote_head,
    )
    if _git_dirty(repo_root):
        raise UpdateError(
            "Working tree has uncommitted changes. "
            f"Backup written to {backup_dir}. Commit or stash your changes, then rerun the update."
        )
    if not _git_is_ancestor(repo_root, local_head, remote_head):
        raise UpdateError(
            f"Local `{branch}` cannot fast-forward to {remote_ref}. "
            f"Backup written to {backup_dir}. Rebase or merge manually."
        )

    if progress is not None:
        progress("Applying fast-forward update...")
    _run_git(repo_root, ["merge", "--ff-only", remote_ref])
    new_head = _git_stdout(repo_root, ["rev-parse", "HEAD"])
    return UpdateResult(repo_root, local_head, new_head, remote_ref, backup_dir, updated=True)


def update_result_text(
    result: UpdateResult,
    *,
    apply_command: str = "libre-claw update",
    restart_hint: str | None = None,
) -> str:
    """Render a self-update result consistently across CLI and chat interfaces."""
    lines = [
        f"Repository: {result.repo_root}",
        f"Remote: {result.remote_ref}",
        f"Current: {short_commit(result.local_head)}",
        f"Latest:  {short_commit(result.remote_head)}",
    ]
    if result.dry_run:
        if result.updated:
            lines.append(f"Update available. Run `{apply_command}` to back up and fast-forward.")
        else:
            lines.append("Libre Claw is already up to date.")
        return "\n".join(lines)
    if result.backup_dir is not None:
        lines.append(f"Backup: {result.backup_dir}")
    if result.updated:
        lines.append(f"Updated Libre Claw to {short_commit(result.remote_head)}.")
        if restart_hint:
            lines.append(restart_hint)
    else:
        lines.append("Libre Claw is already up to date.")
    return "\n".join(lines)


def short_commit(commit: str) -> str:
    return commit[:12]


def libre_claw_checkout_path() -> Path:
    """Return the source checkout containing the running Libre Claw package."""
    return Path(__file__).resolve().parents[2]


def _resolve_update_repo(repo_path: Path | None) -> Path:
    candidates = [repo_path.expanduser() if repo_path is not None else Path.cwd()]
    package_root = libre_claw_checkout_path()
    if package_root not in candidates:
        candidates.append(package_root)
    for candidate in candidates:
        try:
            start = candidate.resolve()
        except OSError:
            continue
        result = _run_git(start, ["rev-parse", "--show-toplevel"], check=False)
        if result.returncode == 0:
            root = Path(result.stdout.strip()).resolve()
            if root.is_dir():
                return root
    raise UpdateError("Could not find a Libre Claw git checkout to update.")


def _create_update_backup(
    repo_root: Path,
    *,
    remote: str,
    branch: str,
    local_head: str,
    remote_head: str,
) -> Path:
    backup_dir = _update_backup_root() / f"{_utc_timestamp()}-{short_commit(local_head)}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "remote": remote,
        "branch": branch,
        "local_head": local_head,
        "remote_head": remote_head,
    }
    (backup_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (backup_dir / "status.txt").write_text(
        _git_stdout(repo_root, ["status", "--short", "--branch"], allow_empty=True),
        encoding="utf-8",
    )
    (backup_dir / "working-tree.patch").write_text(
        _git_stdout(repo_root, ["diff", "--binary"], allow_empty=True),
        encoding="utf-8",
    )
    (backup_dir / "staged.patch").write_text(
        _git_stdout(repo_root, ["diff", "--cached", "--binary"], allow_empty=True),
        encoding="utf-8",
    )
    untracked = _git_stdout(repo_root, ["ls-files", "--others", "--exclude-standard"], allow_empty=True)
    (backup_dir / "untracked.txt").write_text(untracked, encoding="utf-8")
    _write_untracked_archive(repo_root, untracked.splitlines(), backup_dir)
    _run_git(repo_root, ["bundle", "create", str(backup_dir / "head.bundle"), "HEAD"])
    return backup_dir


def _update_backup_root() -> Path:
    return Path.home() / ".libre-claw" / "backups" / "updates"


def _write_untracked_archive(repo_root: Path, files: list[str], backup_dir: Path) -> None:
    safe_files: list[tuple[str, Path]] = []
    for name in files:
        candidate = (repo_root / name).resolve()
        if not _path_is_relative_to(candidate, repo_root) or not candidate.is_file():
            continue
        safe_files.append((name, candidate))
    if not safe_files:
        return
    with zipfile.ZipFile(backup_dir / "untracked.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in safe_files:
            archive.write(path, arcname=name)


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_dirty(repo_root: Path) -> bool:
    return bool(_git_stdout(repo_root, ["status", "--porcelain"], allow_empty=True).strip())


def _git_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return _run_git(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0


def _git_stdout(repo_root: Path, args: list[str], *, allow_empty: bool = False) -> str:
    result = _run_git(repo_root, args)
    if not result.stdout and not allow_empty:
        raise UpdateError(f"Git command returned no output: git {' '.join(args)}")
    return result.stdout.strip() if not allow_empty else result.stdout


def _run_git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed git executable with structured arguments.
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Could not run git {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise UpdateError(f"Git command failed: git {' '.join(args)}\n{details}")
    return result


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
