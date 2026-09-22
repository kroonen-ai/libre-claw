# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4


ReviewScope = Literal["unstaged", "staged", "branch", "last-turn"]
ReviewAction = Literal["stage", "unstage", "revert"]
MAX_GIT_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 60
_LOCKS: dict[str, asyncio.Lock] = {}
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$", re.MULTILINE)


class ReviewError(ValueError):
    """A review operation cannot be applied safely to the current repository."""


@dataclass(frozen=True)
class ReviewHunk:
    hunk_id: str
    header: str
    old_start: int
    new_start: int
    old_count: int
    new_count: int
    patch: str


@dataclass(frozen=True)
class ReviewFile:
    path: str
    status: str
    binary: bool
    patch: str
    hunks: tuple[ReviewHunk, ...]


@dataclass(frozen=True)
class ReviewSnapshot:
    repository: str
    scope: ReviewScope
    revision: str
    base_oid: str
    target_oid: str
    patch: str
    files: tuple[ReviewFile, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewComment:
    comment_id: str
    repository: str
    run_id: str
    path: str
    line: int
    side: Literal["left", "right"]
    body: str
    revision: str
    created_at: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


async def git_bytes(
    repository: Path,
    *arguments: str,
    stdin: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    """Run literal git arguments with bounded capture, no hooks, and no shell."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    if environment:
        env.update(environment)
    process = await asyncio.create_subprocess_exec(
        "git", "--literal-pathspecs", "-c", "core.hooksPath=/dev/null", *arguments,
        cwd=repository, env=env, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )

    async def read(stream: asyncio.StreamReader | None) -> bytes:
        chunks: list[bytes] = []
        size = 0
        if stream is not None:
            while chunk := await stream.read(65536):
                size += len(chunk)
                if size > MAX_GIT_BYTES:
                    raise ReviewError("Git output exceeds the review size limit; narrow the changes first.")
                chunks.append(chunk)
        return b"".join(chunks)

    async def write() -> None:
        if process.stdin is not None:
            try:
                process.stdin.write(stdin or b"")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    tasks = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr)), asyncio.create_task(write())]
    try:
        async with asyncio.timeout(GIT_TIMEOUT_SECONDS):
            output, error, _ = await asyncio.gather(*tasks)
            code = await process.wait()
        if code:
            raise ReviewError(error.decode("utf-8", "replace").strip() or f"Git exited with status {code}.")
        return output
    except BaseException as exc:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, TimeoutError):
            raise ReviewError(f"Git operation timed out after {GIT_TIMEOUT_SECONDS} seconds.") from exc
        raise


async def repository_root(repository: Path | str) -> Path:
    path = Path(repository).expanduser().resolve()
    return Path((await git_bytes(path, "rev-parse", "--show-toplevel")).decode().strip()).resolve()


async def resolve_commit(repository: Path, ref: str) -> str:
    if not ref or ref.startswith("-") or "\x00" in ref:
        raise ReviewError("Provide a valid Git branch, tag, or commit reference.")
    return (await git_bytes(repository, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")).decode().strip()


def validate_repo_path(repository: Path, path: str) -> str:
    if not path or "\x00" in path or "\\" in path:
        raise ReviewError("Provide a repository-relative file path.")
    relative = PurePosixPath(path)
    if relative.is_absolute() or any(part in {"..", ".git"} for part in relative.parts) or not relative.parts:
        raise ReviewError("Review paths must stay inside the repository and outside .git.")
    root = repository.resolve()
    normalized = os.path.normpath(os.path.join(root, path))
    if not normalized.startswith(os.path.join(str(root), "")):
        raise ReviewError("Review path resolves outside the repository.")
    candidate = Path(normalized)
    # Check containment before inspecting user paths, and inspect ancestors
    # first so even validation never traverses a symlink outside the repository.
    for current in (*reversed(candidate.parents), candidate):
        if current != root and current.is_relative_to(root) and current.is_symlink():
            raise ReviewError("Review mutations through symlinks are not supported.")
    return candidate.relative_to(root).as_posix()


def repository_lock(repository: Path) -> asyncio.Lock:
    key = f"{id(asyncio.get_running_loop())}:{repository.resolve()}"
    return _LOCKS.setdefault(key, asyncio.Lock())


async def create_checkpoint(repository: Path | str, *, name: str | None = None) -> str:
    """Snapshot tracked and nonignored untracked files without changing the real index."""
    root = await repository_root(repository)
    temporary = await asyncio.to_thread(tempfile.mkdtemp, prefix="libre-claw-index-")
    index = Path(temporary) / "index"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        # Seed from the real index so partially staged changes and tracked ignored files survive.
        staged = (await git_bytes(root, "write-tree")).decode().strip()
        await git_bytes(root, "read-tree", staged, environment=env)
        await git_bytes(root, "add", "-A", "--", ".", environment=env)
        tree = (await git_bytes(root, "write-tree", environment=env)).decode().strip()
        if name is not None:
            key = hashlib.sha256(name.encode()).hexdigest()
            await git_bytes(root, "update-ref", f"refs/libre-claw/checkpoints/{key}", tree)
        return tree
    finally:
        await asyncio.to_thread(_remove_temporary_index, Path(temporary))


def _remove_temporary_index(directory: Path) -> None:
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()


async def _resolve_tree(repository: Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ReviewError("A checkpoint must be a recorded Git tree object ID.")
    return (await git_bytes(repository, "rev-parse", "--verify", "--end-of-options", value + "^{tree}")).decode().strip()


async def capture_review(
    repository: Path | str,
    scope: ReviewScope = "unstaged",
    *,
    base_ref: str | None = None,
    checkpoint: str | None = None,
) -> ReviewSnapshot:
    root = await repository_root(repository)
    if scope == "staged":
        base = await resolve_commit(root, "HEAD")
        target = (await git_bytes(root, "write-tree")).decode().strip()
    elif scope == "unstaged":
        base = (await git_bytes(root, "write-tree")).decode().strip()
        target = await create_checkpoint(root)
    elif scope == "branch":
        if not base_ref:
            raise ReviewError("Branch review requires the branch or commit to compare against.")
        requested_base = await resolve_commit(root, base_ref)
        base = (await git_bytes(root, "merge-base", "HEAD", requested_base)).decode().strip()
        target = await create_checkpoint(root)
    elif scope == "last-turn":
        if checkpoint is None:
            raise ReviewError("Last-turn review requires a checkpoint recorded before that turn.")
        base = await _resolve_tree(root, checkpoint)
        target = await create_checkpoint(root)
    else:
        raise ReviewError("Unknown review scope.")
    return await review_between(root, base, target, scope=scope)


async def review_between(repository: Path, base: str, target: str, *, scope: ReviewScope = "branch") -> ReviewSnapshot:
    flags = ("--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--full-index", "--unified=3")
    raw = await git_bytes(repository, "diff", *flags, base, target, "--")
    paths = (await git_bytes(repository, "diff", "--no-renames", "--name-only", "-z", base, target, "--")).split(b"\0")
    files: list[ReviewFile] = []
    for encoded in paths:
        if not encoded:
            continue
        path = encoded.decode("utf-8", "surrogateescape")
        patch = (await git_bytes(repository, "diff", *flags, base, target, "--", path)).decode("utf-8", "surrogateescape")
        added, deleted = "\nnew file mode " in patch, "\ndeleted file mode " in patch
        status = "added" if added and not deleted else "deleted" if deleted and not added else "modified"
        binary = "\nGIT binary patch\n" in patch or "\nBinary files " in patch
        files.append(ReviewFile(path, status, binary, patch, _parse_hunks(patch)))
    revision = hashlib.sha256(f"{repository}\0{scope}\0{base}\0{target}".encode()).hexdigest()
    return ReviewSnapshot(str(repository), scope, revision, base, target, raw.decode("utf-8", "surrogateescape"), tuple(files))


def _parse_hunks(patch: str) -> tuple[ReviewHunk, ...]:
    result: list[ReviewHunk] = []
    # Git represents a file-type change as deletion and addition under separate
    # headers even for a single path. Never attach the next component's header
    # to a hunk: Git can otherwise accept it as an unintended empty-file action.
    for component in re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE):
        matches = list(_HUNK.finditer(component))
        if not matches:
            continue
        prefix = component[:matches[0].start()]
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(component)
            body = component[match.start():end]
            result.append(ReviewHunk(
                hashlib.sha256(body.encode("utf-8", "surrogateescape")).hexdigest(), match.group(0),
                int(match.group(1)), int(match.group(3)), int(match.group(2) or "1"), int(match.group(4) or "1"), prefix + body,
            ))
    return tuple(result)


async def mutate_review(
    repository: Path | str,
    action: ReviewAction,
    *,
    expected_revision: str,
    path: str,
    hunk_id: str | None = None,
) -> ReviewSnapshot:
    if action not in {"stage", "unstage", "revert"}:
        raise ReviewError("Unknown review action.")
    root = await repository_root(repository)
    path = validate_repo_path(root, path)
    scope: ReviewScope = "staged" if action == "unstage" else "unstaged"
    async with repository_lock(root):
        snapshot = await capture_review(root, scope)
        if snapshot.revision != expected_revision:
            raise ReviewError("The diff changed since review. Refresh it before applying this action.")
        selected = next((item for item in snapshot.files if item.path == path), None)
        if selected is None:
            raise ReviewError("The selected file is not present in this diff.")
        patch = selected.patch
        if hunk_id is not None:
            if selected.binary:
                raise ReviewError("Binary files require a whole-file action.")
            hunk = next((item for item in selected.hunks if item.hunk_id == hunk_id), None)
            if hunk is None:
                raise ReviewError("The selected hunk is not present in this diff.")
            patch = hunk.patch
            if selected.status == "modified":
                # A content hunk must not also stage/revert a separate executable-bit
                # change. Addition/deletion headers are retained: their single
                # hunk creates/removes the file, including its recorded mode.
                start = _HUNK.search(patch)
                assert start is not None  # Parsed hunks always contain a hunk header.
                header, body = patch[:start.start()], patch[start.start():]
                header = "".join(line for line in header.splitlines(keepends=True)
                                 if not line.startswith(("old mode ", "new mode ")))
                patch = header + body
        options = ["--cached"] if action in {"stage", "unstage"} else []
        if action in {"unstage", "revert"}:
            options.append("--reverse")
        data = patch.encode("utf-8", "surrogateescape")
        await git_bytes(root, "apply", "--check", *options, "-", stdin=data)
        await git_bytes(root, "apply", *options, "-", stdin=data)
        return await capture_review(root, scope)


class ReviewCommentStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    async def add(
        self, repository: Path | str, run_id: str, *, path: str, line: int,
        side: Literal["left", "right"], body: str, revision: str,
        scope: ReviewScope = "unstaged", base_ref: str | None = None, checkpoint: str | None = None,
    ) -> ReviewComment:
        root = await repository_root(repository)
        path = validate_repo_path(root, path)
        if isinstance(line, bool) or not isinstance(line, int) or line < 1 or side not in {"left", "right"}:
            raise ReviewError("Comments require a positive line number and left or right side.")
        if not body.strip() or len(body) > 16000 or not run_id or not revision:
            raise ReviewError("Comments require a body, run ID, and reviewed revision.")
        snapshot = await capture_review(root, scope, base_ref=base_ref, checkpoint=checkpoint)
        if snapshot.revision != revision:
            raise ReviewError("The diff changed since review. Refresh it before adding this comment.")
        file = next((file for file in snapshot.files if file.path == path), None)
        if file is None or not any(
            (hunk.old_start <= line < hunk.old_start + hunk.old_count if side == "left"
             else hunk.new_start <= line < hunk.new_start + hunk.new_count)
            for hunk in file.hunks
        ):
            raise ReviewError("The selected line is not present on that side of the reviewed diff.")
        comment = ReviewComment(uuid4().hex, str(root), run_id, path, line, side, body.strip(), revision, datetime.now(timezone.utc).isoformat())
        directory = self._directory(root, run_id)
        await asyncio.to_thread(atomic_json, directory / f"{comment.comment_id}.json", comment.to_payload())
        return comment

    async def list(self, repository: Path | str, run_id: str) -> list[ReviewComment]:
        root = await repository_root(repository)
        return await asyncio.to_thread(self._read, self._directory(root, run_id))

    def _directory(self, repository: Path, run_id: str) -> Path:
        key = hashlib.sha256(f"{repository}\0{run_id}".encode()).hexdigest()
        return self.root / key

    @staticmethod
    def _read(directory: Path) -> list[ReviewComment]:
        if not directory.exists():
            return []
        result = [ReviewComment(**json.loads(path.read_text())) for path in directory.glob("*.json")]
        return sorted(result, key=lambda item: (item.created_at, item.comment_id))


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
