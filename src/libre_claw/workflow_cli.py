# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from libre_claw.config import LibreClawConfig, load_config
from libre_claw.core.git_review import ReviewFile, ReviewHunk, ReviewSnapshot, atomic_json
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.core.worktrees import ManagedWorktree, TransferPreview
from libre_claw.tui.workflows import WorkflowState, handle_workflow_command


class _CLIWorkspace:
    def __init__(self, config: LibreClawConfig, runs_root: Path | None, worktrees_root: Path | None) -> None:
        self.config = config
        self.run_store = RunStore(runs_root)
        self.session = Session()
        self._active_run_id: str | None = None
        self._resumed_run_id: str | None = None
        self._active_task = None
        self._workflow_state = WorkflowState()
        self._workflow_worktrees_root = worktrees_root
        self._workflow_error: str | None = None
        self.messages: list[str] = []
        key = hashlib.sha256(str(config.general.working_directory.resolve()).encode()).hexdigest()
        self.state_path = self.run_store.root.parent / "workflow-state" / f"{key}.json"

    def _append_system(self, message: str) -> None:
        self.messages.append(message)

    async def _handle_resume_command(self, run_id: str) -> None:
        run = await self.run_store.load_run(run_id)
        if run is None:
            raise ValueError("Task no longer exists.")
        self.config = replace(self.config, general=replace(self.config.general, working_directory=Path(run.working_directory)))
        self.session = await self.run_store.load_session(run_id, recover=True)
        self._resumed_run_id = run_id

    async def load(self, run_id: str | None) -> None:
        try:
            payload = json.loads(await asyncio.to_thread(self.state_path.read_text, encoding="utf-8"))
        except FileNotFoundError:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Saved workflow state is invalid.")
        self._workflow_state.base_ref = payload.get("base_ref")
        self._workflow_state.checkpoint = payload.get("checkpoint")
        if payload.get("review"):
            self._workflow_state.review = _snapshot(payload["review"])
        for worktree_id, item in payload.get("previews", {}).items():
            self._workflow_state.previews[worktree_id] = TransferPreview(
                ManagedWorktree(**item["worktree"]), _snapshot(item["review"]), item["target_revision"], item["target_head"],
            )
        selected = run_id or payload.get("run_id")
        if selected:
            run = await self.run_store.load_run(selected)
            if run is None and run_id:
                raise ValueError("The selected task does not exist.")
            if run is not None and (run_id or Path(run.working_directory).resolve() == self.config.general.working_directory.resolve()):
                await self._handle_resume_command(selected)

    async def save(self) -> None:
        state = self._workflow_state
        payload = {
            "run_id": self._resumed_run_id,
            "review": state.review.to_payload() if state.review is not None else None,
            "base_ref": state.base_ref, "checkpoint": state.checkpoint,
            "previews": {key: value.to_payload() for key, value in state.previews.items()},
        }
        await asyncio.to_thread(atomic_json, self.state_path, payload)
        if self._resumed_run_id:
            await self.run_store.save_session(self._resumed_run_id, self.session)


def _snapshot(payload: dict[str, Any]) -> ReviewSnapshot:
    fields = dict(payload)
    fields["files"] = tuple(ReviewFile(**{**item, "hunks": tuple(ReviewHunk(**hunk) for hunk in item["hunks"])}) for item in fields["files"])
    return ReviewSnapshot(**fields)


@click.group("workflow")
@click.option("--repository", type=click.Path(exists=True, file_okay=False, path_type=Path), help="Repository to review or isolate; defaults to the selected working directory.")
@click.option("--runs-root", type=click.Path(file_okay=False, path_type=Path), help="Durable task storage directory.")
@click.option("--worktrees-root", type=click.Path(file_okay=False, path_type=Path), help="Managed checkout storage outside the repository.")
@click.option("--run-id", help="Load a durable task, including its workspace and last-turn checkpoint.")
@click.pass_context
def workflow_group(ctx: click.Context, repository: Path | None, runs_root: Path | None, worktrees_root: Path | None, run_id: str | None) -> None:
    """Review changes and manage isolated task workspaces."""
    inherited = dict(ctx.obj or {})
    inherited.update({"workflow_repository": repository, "workflow_runs_root": runs_root, "workflow_worktrees_root": worktrees_root, "workflow_run_id": run_id})
    ctx.obj = inherited


def _dispatch(ctx: click.Context, command: str, arguments: list[str], *, raw_tail: str | None = None) -> None:
    options = ctx.obj or {}
    argument = shlex.join(arguments)
    if raw_tail is not None:
        argument += " " + raw_tail

    async def execute() -> _CLIWorkspace:
        config = load_config(config_path=options.get("config_path"), working_directory=options.get("workflow_repository") or options.get("working_directory") or Path.cwd())
        app = _CLIWorkspace(config, options.get("workflow_runs_root"), options.get("workflow_worktrees_root"))
        await app.load(options.get("workflow_run_id"))
        await handle_workflow_command(app, command, argument)
        await app.save()
        return app

    try:
        app = asyncio.run(execute())
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    for message in app.messages:
        click.echo(message)
    if app._workflow_error:
        raise click.ClickException(app._workflow_error)


@workflow_group.group("worktree")
def worktree_group() -> None:
    """Create, inspect, use, and safely remove task worktrees."""


@worktree_group.command("create")
@click.argument("ref", default="HEAD")
@click.option("--branch", help="Create this new branch in the isolated checkout.")
@click.option("--include-changes", is_flag=True, help="Copy current staged, unstaged, and nonignored untracked content.")
@click.pass_context
def create(ctx: click.Context, ref: str, branch: str | None, include_changes: bool) -> None:
    arguments = ["create", ref]
    if branch:
        arguments += ["--branch", branch]
    if include_changes:
        arguments.append("--include-changes")
    _dispatch(ctx, "/worktree", arguments)


@worktree_group.command("list")
@click.pass_context
def list_worktrees(ctx: click.Context) -> None:
    _dispatch(ctx, "/worktree", ["list"])


def _worktree_command(action: str) -> click.Command:
    @click.command(action)
    @click.argument("worktree_id")
    @click.pass_context
    def execute(ctx: click.Context, worktree_id: str) -> None:
        _dispatch(ctx, "/worktree", [action, worktree_id])
    return execute


for _action in ("use", "remove", "preview"):
    worktree_group.add_command(_worktree_command(_action))


@worktree_group.command("apply")
@click.argument("worktree_id")
@click.option("--confirm", is_flag=True, help="Apply the exact transfer saved by the preview command.")
@click.pass_context
def apply_transfer(ctx: click.Context, worktree_id: str, confirm: bool) -> None:
    _dispatch(ctx, "/worktree", ["apply", worktree_id, *(["--confirm"] if confirm else [])])


@worktree_group.command("setup")
@click.argument("worktree_id")
@click.option("--command", "shell_command", required=True, help="Explicitly authorize this shell command once; existing sandbox restrictions still apply.")
@click.pass_context
def setup(ctx: click.Context, worktree_id: str, shell_command: str) -> None:
    _dispatch(ctx, "/worktree", ["setup", worktree_id], raw_tail=shell_command)


@workflow_group.group("review")
def review_group() -> None:
    """Inspect diffs, apply reviewed edits, and ask an independent reviewer."""


def _review_scope_command(scope: str) -> click.Command:
    @click.command(scope)
    @click.pass_context
    def execute(ctx: click.Context) -> None:
        _dispatch(ctx, "/review", [scope])
    return execute


for _scope in ("unstaged", "staged", "last-turn", "analyze"):
    review_group.add_command(_review_scope_command(_scope))


@review_group.command("branch")
@click.argument("base_ref")
@click.pass_context
def branch_review(ctx: click.Context, base_ref: str) -> None:
    _dispatch(ctx, "/review", ["branch", base_ref])


def _review_action_command(action: str) -> click.Command:
    @click.command(action)
    @click.argument("path")
    @click.argument("hunk_id", required=False)
    @click.pass_context
    def execute(ctx: click.Context, path: str, hunk_id: str | None) -> None:
        _dispatch(ctx, "/review", [action, path, *([hunk_id] if hunk_id else [])])
    return execute


for _action in ("stage", "unstage", "revert"):
    review_group.add_command(_review_action_command(_action))


@review_group.command("comment")
@click.argument("location")
@click.argument("body", nargs=-1, required=True)
@click.option("--left", is_flag=True, help="Attach the comment to the original side.")
@click.pass_context
def comment(ctx: click.Context, location: str, body: tuple[str, ...], left: bool) -> None:
    _dispatch(ctx, "/review", ["comment", location, *(["--left"] if left else []), *body])
