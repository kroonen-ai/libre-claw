# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libre_claw.core.git_review import ReviewCommentStore, ReviewError, ReviewSnapshot, capture_review, mutate_review, repository_root
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.reviewer import review_changes
from libre_claw.core.runs import RunRecord
from libre_claw.core.worktrees import TransferPreview, WorktreeManager
from libre_claw.tools_builtin import create_builtin_registry


ACTIVE_STATES = {"queued", "running", "blocked"}
WORKTREE_HELP = """Worktrees:
/worktree create [ref] [--branch new-name] [--include-changes]
/worktree list | use <id> | remove <id>
/worktree preview <id>
/worktree apply <id> --confirm
/worktree setup <id> <shell command>"""
REVIEW_HELP = """Git review:
/review unstaged | staged | branch <base> | last-turn
/review stage|unstage|revert <path> [hunk-id]
/review comment <path>:<line> [--left] <comment>
/review analyze"""


@dataclass
class WorkflowState:
    review: ReviewSnapshot | None = None
    base_ref: str | None = None
    checkpoint: str | None = None
    previews: dict[str, TransferPreview] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def workflow_state(app: Any) -> WorkflowState:
    state = getattr(app, "_workflow_state", None)
    if not isinstance(state, WorkflowState):
        state = WorkflowState()
        app._workflow_state = state
    return state


def worktree_manager(app: Any) -> WorktreeManager:
    explicit = getattr(app, "_workflow_worktrees_root", None)
    root = explicit or os.getenv("LIBRE_CLAW_WORKTREE_ROOT") or app.run_store.root.parent / "worktrees"
    return WorktreeManager(root)


def comment_store(app: Any) -> ReviewCommentStore:
    return ReviewCommentStore(app.run_store.root.parent / "review-comments")


async def handle_workflow_command(app: Any, command: str, argument: str) -> bool:
    """Dispatch optional workflows without replacing the existing TUI review drawer."""
    if command not in {"/worktree", "/review"}:
        return False
    if command == "/review" and argument.strip() in {"", "latest", "previous", "next", "close"}:
        return False
    try:
        async with workflow_state(app).lock:
            if command == "/worktree":
                await _worktree(app, argument)
            else:
                await _review(app, argument)
    except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
        app._append_system(f"Workflow error: {exc}")
        app._workflow_error = str(exc)
    else:
        app._workflow_error = None
    return True


async def _active_runs(app: Any) -> list[RunRecord]:
    return [run for run in await app.run_store.list_runs(limit=100000) if run.state in ACTIVE_STATES]


async def _require_idle(app: Any, *paths: Path | str) -> None:
    task = getattr(app, "_active_task", None)
    if task is not None and not task.done():
        raise ReviewError("Stop the active response before changing its workspace or Git state.")
    roots = [await repository_root(path) for path in paths]
    for run in await _active_runs(app):
        if not run.working_directory:
            continue
        workspace = Path(run.working_directory).expanduser().resolve()
        if any(workspace == root or workspace.is_relative_to(root) or root.is_relative_to(workspace) for root in roots):
            raise ReviewError(f"Task {run.run_id} is {run.state} in this workspace. Stop it before this action.")


async def _current_run(app: Any, *, create: bool = False) -> RunRecord | None:
    run_id = getattr(app, "_active_run_id", None) or getattr(app, "_resumed_run_id", None)
    run = await app.run_store.load_run(run_id) if run_id else None
    if run is None and create:
        config = app.config.general
        run = await app.run_store.create_run(
            "Worktree task", kind="chat", provider=config.default_provider,
            model=config.default_model, working_directory=config.working_directory, state="done",
        )
        await app.run_store.save_session(run.run_id, app.session)
    return run


async def _switch(app: Any, run_id: str, workspace: Path) -> None:
    current = await _current_run(app)
    if current is not None:
        await app.run_store.save_session(current.run_id, app.session)
    await app.run_store.set_workspace(run_id, workspace)
    await app._handle_resume_command(run_id)
    workflow_state(app).review = None


async def _worktree(app: Any, argument: str) -> None:
    tokens = shlex.split(argument)
    if not tokens or tokens[0] in {"help", "--help"}:
        app._append_system(WORKTREE_HELP)
        return
    action, rest = tokens[0], tokens[1:]
    manager = worktree_manager(app)
    current = app.config.general.working_directory
    if action == "list":
        if rest:
            raise ReviewError("Usage: /worktree list")
        records = await manager.list()
        app._append_system("\n".join(f"{item.worktree_id}  {item.run_id}  {item.path}" for item in records) or "No managed worktrees.")
        return
    if action == "create":
        ref = "HEAD"
        branch = None
        include_changes = False
        ref_seen = False
        while rest:
            token = rest.pop(0)
            if token == "--include-changes":
                include_changes = True
            elif token == "--branch" and rest:
                branch = rest.pop(0)
            elif token.startswith("-") or ref_seen:
                raise ReviewError("Usage: /worktree create [ref] [--branch new-name] [--include-changes]")
            else:
                ref, ref_seen = token, True
        await _require_idle(app, current)
        run = await _current_run(app, create=True)
        assert run is not None
        if run.state in ACTIVE_STATES:
            raise ReviewError("Stop the task before moving it to a worktree.")
        record = await manager.create(current, run.run_id, ref=ref, branch=branch, include_changes=include_changes)
        await app.run_store.append_event(run.run_id, "worktree_created", record.to_payload())
        await _switch(app, run.run_id, Path(record.path))
        app._append_system(f"Worktree {record.worktree_id}\nTask {run.run_id}\nWorkspace: {record.path}")
        return
    if action not in {"use", "remove", "preview", "apply", "setup"} or not rest:
        raise ReviewError(WORKTREE_HELP)
    worktree_id = rest.pop(0)
    record = await manager.get(worktree_id)
    if action == "preview":
        if rest:
            raise ReviewError("Usage: /worktree preview <id>")
        preview = await manager.preview_transfer(worktree_id)
        workflow_state(app).previews[worktree_id] = preview
        app._append_system(f"Transfer to {record.repository}")
        await _show_review(app, preview.review)
        app._append_system(f"Apply this reviewed transfer with /worktree apply {worktree_id} --confirm")
        return
    if action == "use":
        if rest:
            raise ReviewError("Usage: /worktree use <id>")
        await _require_idle(app, current, record.path)
        run = await app.run_store.load_run(record.run_id)
        if run is None:
            raise ReviewError("The worktree's associated task no longer exists.")
        await _switch(app, record.run_id, Path(record.path))
        app._append_system(f"Workspace: {record.path}")
        return
    if action == "remove":
        if rest:
            raise ReviewError("Usage: /worktree remove <id>")
        await _require_idle(app, record.path)
        active = [run.run_id for run in await _active_runs(app)]
        await manager.remove(worktree_id, active_run_ids=active)
        for run in await app.run_store.list_runs(limit=100000):
            if Path(run.working_directory).resolve() == Path(record.path).resolve():
                await app.run_store.set_workspace(run.run_id, Path(record.repository))
        if Path(current).resolve() == Path(record.path).resolve():
            await _switch(app, record.run_id, Path(record.repository))
        workflow_state(app).previews.pop(worktree_id, None)
        app._append_system(f"Removed clean worktree {worktree_id}.")
        return
    if action == "apply":
        if rest != ["--confirm"]:
            raise ReviewError("First preview the transfer, then use /worktree apply <id> --confirm.")
        await _require_idle(app, record.path, record.repository)
        preview = workflow_state(app).previews.get(worktree_id)
        if preview is None:
            raise ReviewError("Run /worktree preview <id> before applying its changes.")
        result = await manager.apply_to_source(worktree_id, expected_revision=preview.review.revision, expected_target_revision=preview.target_revision)
        workflow_state(app).previews.pop(worktree_id, None)
        await app.run_store.append_event(record.run_id, "worktree_transferred", {"revision": preview.review.revision, "repository": record.repository})
        app._append_system(f"Applied reviewed changes to {result.worktree.repository}.")
        return
    # Preserve shell quoting exactly; only the action and opaque worktree ID are parsed.
    pieces = argument.strip().split(maxsplit=2)
    if len(pieces) != 3 or not pieces[2].strip():
        raise ReviewError("Usage: /worktree setup <id> <shell command>")
    await _require_idle(app, record.path)
    registry = create_builtin_registry(app.config)
    if "bash" not in registry:
        raise ReviewError("The bash tool is disabled by the current tool configuration.")
    context = registry.context
    if context is None:
        raise ReviewError("No shell tool context is available for setup.")

    async def approved(_call: Any) -> str:
        return "allow_once"

    results = await manager.run_setup(worktree_id, [pieces[2]], context=context, permission_manager=PermissionManager(app.config.permissions), request_permission=approved)
    app._append_system("\n".join(result.as_text() for result in results) or "Setup completed.")
    if any(result.is_error for result in results):
        raise ReviewError("A setup command failed; inspect its output above.")


async def _review(app: Any, argument: str) -> None:
    tokens = shlex.split(argument)
    if not tokens or tokens[0] in {"help", "--help"}:
        app._append_system(REVIEW_HELP)
        return
    action, rest = tokens[0], tokens[1:]
    state = workflow_state(app)
    repo = app.config.general.working_directory
    if action in {"unstaged", "staged", "branch", "last-turn"}:
        if (action == "branch" and len(rest) != 1) or (action != "branch" and rest):
            raise ReviewError("Usage: /review unstaged|staged|branch <base>|last-turn")
        base = rest[0] if action == "branch" else None
        checkpoint = app.session.checkpoint.get("last_turn_tree") if action == "last-turn" else None
        state.review = await capture_review(repo, action, base_ref=base, checkpoint=checkpoint)
        state.base_ref, state.checkpoint = base, checkpoint
        await _show_review(app, state.review)
        return
    if action in {"stage", "unstage", "revert"}:
        if len(rest) not in {1, 2}:
            raise ReviewError("Usage: /review stage|unstage|revert <path> [hunk-id]")
        await _require_idle(app, repo)
        snapshot = await _saved_review(app)
        required_scope = "staged" if action == "unstage" else "unstaged"
        if snapshot.scope != required_scope:
            raise ReviewError(f"Run /review {required_scope} before this action.")
        hunk_id = None
        if len(rest) == 2:
            matches = [hunk.hunk_id for file in snapshot.files if file.path == rest[0] for hunk in file.hunks if hunk.hunk_id.startswith(rest[1])]
            if len(matches) != 1:
                raise ReviewError("The hunk ID must identify one hunk in the reviewed file.")
            hunk_id = matches[0]
        state.review = await mutate_review(repo, action, expected_revision=snapshot.revision, path=rest[0], hunk_id=hunk_id)
        await _show_review(app, state.review)
        return
    if action == "comment":
        if len(rest) < 2 or ":" not in rest[0]:
            raise ReviewError("Usage: /review comment <path>:<line> [--left] <comment>")
        snapshot = await _saved_review(app)
        path, line = rest.pop(0).rsplit(":", 1)
        side = "left" if rest and rest[0] == "--left" else "right"
        if side == "left":
            rest.pop(0)
        comment = await comment_store(app).add(repo, _run_id(app), path=path, line=int(line), side=side, body=" ".join(rest), revision=snapshot.revision, scope=snapshot.scope, base_ref=state.base_ref, checkpoint=state.checkpoint)
        app._append_system(f"Comment saved at {comment.path}:{comment.line} ({comment.side}).")
        return
    if action == "analyze":
        if rest:
            raise ReviewError("Usage: /review analyze")
        if state.review is None:
            state.review = await capture_review(repo)
            state.base_ref = state.checkpoint = None
        snapshot = await _saved_review(app)
        fresh = await capture_review(repo, snapshot.scope, base_ref=state.base_ref, checkpoint=state.checkpoint)
        if fresh.revision != snapshot.revision:
            raise ReviewError("The diff changed since review. Refresh it before requesting analysis.")
        comments = await comment_store(app).list(repo, _run_id(app))
        feedback = "\n".join(f"{item.path}:{item.line}: {item.body}" for item in comments if item.revision == snapshot.revision)
        text = await review_changes(app.config, snapshot, feedback=feedback)
        run = await _current_run(app)
        if run is not None:
            await app.run_store.append_event(run.run_id, "code_review", {"revision": snapshot.revision, "text": text})
        app._append_system(text)
        return
    raise ReviewError(REVIEW_HELP)


async def _saved_review(app: Any) -> ReviewSnapshot:
    snapshot = workflow_state(app).review
    if snapshot is None or Path(snapshot.repository) != await repository_root(app.config.general.working_directory):
        raise ReviewError("Open a Git review for this workspace before applying an action.")
    return snapshot


def _run_id(app: Any) -> str:
    return getattr(app, "_active_run_id", None) or getattr(app, "_resumed_run_id", None) or "workspace"


def _format_review(snapshot: ReviewSnapshot) -> str:
    lines = [f"{snapshot.scope} review: {len(snapshot.files)} file(s)", f"Revision: {snapshot.revision}"]
    for file in snapshot.files:
        lines.append(f"{file.status}: {file.path}" + (" (binary)" if file.binary else ""))
        lines.extend(f"  hunk {hunk.hunk_id[:12]} {hunk.header}" for hunk in file.hunks)
    lines.extend(["", snapshot.patch[:64000] or "No changes."])
    if len(snapshot.patch) > 64000:
        lines.append("[Diff preview truncated; the complete patch is saved in the review artifact.]")
    return "\n".join(lines)


async def _show_review(app: Any, snapshot: ReviewSnapshot) -> None:
    text = _format_review(snapshot)
    if len(snapshot.patch) > 64000:
        path = app.run_store.root.parent / "review-patches" / f"{snapshot.revision}.patch"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, snapshot.patch, encoding="utf-8", errors="surrogateescape")
        text += f"\nComplete patch: {path}"
    app._append_system(text)
