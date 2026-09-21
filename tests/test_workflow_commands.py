# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from libre_claw.config import load_config
from libre_claw.core.git_review import create_checkpoint
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.tui.workflows import handle_workflow_command, workflow_state, worktree_manager
from libre_claw.workflow_cli import workflow_group


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Workflow Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "example.txt").write_text("".join(f"line {index}\n" for index in range(30)))
    git(root, "add", ".")
    git(root, "commit", "-m", "Create workflow fixture")
    return root


class FakeApp:
    def __init__(self, repo: Path, root: Path) -> None:
        self.config = load_config(working_directory=repo)
        self.run_store = RunStore(root / "runs")
        self.session = Session()
        self._active_run_id = None
        self._resumed_run_id = None
        self._active_task = None
        self._workflow_worktrees_root = root / "worktrees"
        self.messages: list[str] = []
        self.resumed: list[str] = []

    def _append_system(self, text: str) -> None:
        self.messages.append(text)

    async def _handle_resume_command(self, run_id: str) -> None:
        run = await self.run_store.load_run(run_id)
        assert run is not None
        self.config = replace(self.config, general=replace(self.config.general, working_directory=Path(run.working_directory)))
        self.session = await self.run_store.load_session(run_id)
        self._resumed_run_id = run_id
        self.resumed.append(run_id)


@pytest.mark.parametrize("argument", ["", "latest", "previous", "next", "close"])
async def test_original_review_drawer_commands_are_not_intercepted(repo: Path, tmp_path: Path, argument: str) -> None:
    app = FakeApp(repo, tmp_path / "state")
    assert not await handle_workflow_command(app, "/review", argument)
    assert not app.messages


async def test_tui_creates_task_and_switches_real_workspace_preserving_context(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    app.session.add_user_message("Keep this task context")
    (repo / "example.txt").write_text("user working copy\n")
    assert await handle_workflow_command(app, "/worktree", "create --include-changes --branch workflow-feature")
    assert app._workflow_error is None
    record = (await worktree_manager(app).list())[0]
    assert app.config.general.working_directory == Path(record.path)
    assert app.resumed == [record.run_id]
    assert app.session.messages[0].content[0]["text"] == "Keep this task context"
    assert (Path(record.path) / "example.txt").read_text() == "user working copy\n"
    assert (repo / "example.txt").read_text() == "user working copy\n"
    assert (await app.run_store.load_run(record.run_id)).state == "done"


async def test_tui_review_uses_saved_revision_and_hunk_prefix(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    path = repo / "example.txt"
    path.write_text(path.read_text().replace("line 2\n", "changed 2\n").replace("line 24\n", "changed 24\n"))
    await handle_workflow_command(app, "/review", "unstaged")
    review = workflow_state(app).review
    assert review is not None
    await handle_workflow_command(app, "/review", f"stage example.txt {review.files[0].hunks[0].hunk_id[:12]}")
    assert app._workflow_error is None and "+changed 2" in git(repo, "diff", "--cached")
    assert "+changed 24" not in git(repo, "diff", "--cached")
    path.write_text("new edit\n")
    await handle_workflow_command(app, "/review", "revert example.txt")
    assert "changed since review" in app._workflow_error
    assert path.read_text() == "new edit\n"


async def test_tui_comments_feed_independent_review_without_touching_session(repo: Path, tmp_path: Path, monkeypatch) -> None:
    app = FakeApp(repo, tmp_path / "state")
    app.session.add_user_message("Original request")
    (repo / "example.txt").write_text("changed\n")
    await handle_workflow_command(app, "/review", "unstaged")
    await handle_workflow_command(app, "/review", 'comment example.txt:1 "Check this change"')
    assert app._workflow_error is None
    calls = []

    async def review_changes(config, snapshot, *, feedback):
        calls.append((config, snapshot, feedback))
        return "No actionable findings."

    monkeypatch.setattr("libre_claw.tui.workflows.review_changes", review_changes)
    await handle_workflow_command(app, "/review", "analyze")
    assert app.messages[-1] == "No actionable findings."
    assert "example.txt:1: Check this change" in calls[0][2]
    assert len(app.session.messages) == 1


async def test_tui_transfer_requires_review_and_confirmation_and_refresh(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    await handle_workflow_command(app, "/worktree", "create")
    record = (await worktree_manager(app).list())[0]
    (Path(record.path) / "example.txt").write_text("agent change\n")
    await handle_workflow_command(app, "/worktree", f"apply {record.worktree_id} --confirm")
    assert "preview" in app._workflow_error
    await handle_workflow_command(app, "/worktree", f"preview {record.worktree_id}")
    await handle_workflow_command(app, "/worktree", f"apply {record.worktree_id}")
    assert "--confirm" in app._workflow_error
    (repo / "unrelated.txt").write_text("user edit\n")
    await handle_workflow_command(app, "/worktree", f"apply {record.worktree_id} --confirm")
    assert "changed since review" in app._workflow_error
    await handle_workflow_command(app, "/worktree", f"preview {record.worktree_id}")
    await handle_workflow_command(app, "/worktree", f"apply {record.worktree_id} --confirm")
    assert app._workflow_error is None
    assert (repo / "example.txt").read_text() == "agent change\n"
    assert (repo / "unrelated.txt").read_text() == "user edit\n"


async def test_tui_setup_preserves_shell_quotes_and_honors_disabled_bash(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    await handle_workflow_command(app, "/worktree", "create")
    record = (await worktree_manager(app).list())[0]
    await handle_workflow_command(app, "/worktree", f'''setup {record.worktree_id} printf '%s' 'hello world' > setup.txt''')
    assert app._workflow_error is None
    assert (Path(record.path) / "setup.txt").read_text() == "hello world"
    app.config = replace(app.config, agent=replace(app.config.agent, tool_denylist=("bash",)))
    await handle_workflow_command(app, "/worktree", f"setup {record.worktree_id} touch disabled.txt")
    assert "disabled" in app._workflow_error
    assert not (Path(record.path) / "disabled.txt").exists()


async def test_tui_refuses_mutations_while_another_task_uses_repository(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    (repo / "example.txt").write_text("change\n")
    await handle_workflow_command(app, "/review", "unstaged")
    await app.run_store.create_run("Another task", kind="chat", provider="openai", model="model", working_directory=repo, state="running")
    await handle_workflow_command(app, "/review", "revert example.txt")
    assert "running in this workspace" in app._workflow_error
    assert (repo / "example.txt").read_text() == "change\n"


async def test_tui_removing_current_clean_worktree_restores_source_workspace(repo: Path, tmp_path: Path) -> None:
    app = FakeApp(repo, tmp_path / "state")
    await handle_workflow_command(app, "/worktree", "create")
    record = (await worktree_manager(app).list())[0]
    await handle_workflow_command(app, "/worktree", f"remove {record.worktree_id}")
    assert app._workflow_error is None
    assert app.config.general.working_directory == repo
    assert (await app.run_store.load_run(record.run_id)).working_directory == str(repo)


def test_cli_review_persists_snapshot_and_rejects_stale_changes(repo: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    options = ["--repository", str(repo), "--runs-root", str(tmp_path / "state" / "runs")]
    (repo / "example.txt").write_text("first\n")
    result = runner.invoke(workflow_group, [*options, "review", "unstaged"])
    assert result.exit_code == 0, result.output
    (repo / "example.txt").write_text("second\n")
    stale = runner.invoke(workflow_group, [*options, "review", "revert", "example.txt"])
    assert stale.exit_code != 0 and "changed since review" in stale.output
    assert (repo / "example.txt").read_text() == "second\n"
    assert runner.invoke(workflow_group, [*options, "review", "unstaged"]).exit_code == 0
    stage = runner.invoke(workflow_group, [*options, "review", "stage", "example.txt"])
    assert stage.exit_code == 0, stage.output
    assert "+second" in git(repo, "diff", "--cached")


def test_cli_worktree_round_trip_is_reviewed_and_preserves_source(repo: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    manager_root = tmp_path / "managed"
    options = ["--repository", str(repo), "--runs-root", str(tmp_path / "state" / "runs"), "--worktrees-root", str(manager_root)]
    created = runner.invoke(workflow_group, [*options, "worktree", "create"])
    assert created.exit_code == 0, created.output
    match = re.search(r"Worktree ([0-9a-f]{32})", created.output)
    assert match is not None
    worktree_id = match.group(1)
    (manager_root / "checkouts" / worktree_id / "example.txt").write_text("cli change\n")
    assert runner.invoke(workflow_group, [*options, "worktree", "preview", worktree_id]).exit_code == 0
    applied = runner.invoke(workflow_group, [*options, "worktree", "apply", worktree_id, "--confirm"])
    assert applied.exit_code == 0, applied.output
    assert (repo / "example.txt").read_text() == "cli change\n"


def test_cli_last_turn_loads_durable_task_checkpoint(repo: Path, tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "state" / "runs")

    async def prepare():
        run = await runs.create_run("Task", kind="chat", provider="openai", model="model", working_directory=repo, state="done")
        session = Session()
        session.checkpoint["last_turn_tree"] = await create_checkpoint(repo)
        await runs.save_session(run.run_id, session)
        return run

    run = asyncio.run(prepare())
    (repo / "example.txt").write_text("next turn\n")
    result = CliRunner().invoke(workflow_group, ["--repository", str(repo), "--runs-root", str(runs.root), "--run-id", run.run_id, "review", "last-turn"])
    assert result.exit_code == 0, result.output
    assert "last-turn review" in result.output and "+next turn" in result.output
