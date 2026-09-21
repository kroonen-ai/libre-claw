# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from libre_claw.config import load_config
from libre_claw.core.git_review import create_checkpoint
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.web.workflow_api import WorkflowAPI


class Request:
    def __init__(self, data: dict[str, Any] | None = None, *, query: dict[str, str] | None = None, worktree_id: str = "") -> None:
        self.data = data or {}
        self.query = query or {}
        self.match_info = {"worktree_id": worktree_id}

    async def json(self) -> dict[str, Any]:
        return self.data


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()


def make_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Workflow API Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "example.txt").write_text("original\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "Create workflow API fixture")
    return path


@pytest.fixture
def api(tmp_path: Path, monkeypatch) -> WorkflowAPI:
    repo = make_repo(tmp_path / "repo")
    monkeypatch.setenv("LIBRE_CLAW_WORKTREE_ROOT", str(tmp_path / "managed"))
    server = SimpleNamespace(config=load_config(working_directory=repo), run_store=RunStore(tmp_path / "state" / "runs"), _active_sessions={}, active_runs={})
    return WorkflowAPI(server)


def payload(response) -> dict[str, Any]:
    return json.loads(response.text)


async def create_run(api: WorkflowAPI, repository: Path, state: str = "done"):
    return await api.server.run_store.create_run("Task", kind="chat", provider="openai", model="test-model", working_directory=repository, state=state)


async def test_api_review_stage_and_stale_revert_are_guarded(api: WorkflowAPI) -> None:
    repo = api.server.config.general.working_directory
    path = repo / "example.txt"
    path.write_text("reviewed\n")
    response = await api.review(Request(query={"scope": "unstaged"}))
    assert response.status == 200
    snapshot = payload(response)
    stage = await api.review_action(Request({"action": "stage", "revision": snapshot["revision"], "path": "example.txt", "hunk_id": snapshot["files"][0]["hunks"][0]["hunk_id"]}))
    assert stage.status == 200 and "+reviewed" in git(repo, "diff", "--cached")
    path.write_text("changed again\n")
    stale = await api.review_action(Request({"action": "revert", "revision": snapshot["revision"], "path": "example.txt"}))
    assert stale.status == 400 and "changed since review" in payload(stale)["error"]
    assert path.read_text() == "changed again\n"


async def test_api_idle_guard_finds_active_task_in_sibling_subdirectory(api: WorkflowAPI) -> None:
    repo = api.server.config.general.working_directory
    left, right = repo / "left", repo / "right"
    left.mkdir()
    right.mkdir()
    (repo / "example.txt").write_text("keep this change\n")
    api.server.config = replace(api.server.config, general=replace(api.server.config.general, working_directory=left))
    snapshot = payload(await api.review(Request(query={"scope": "unstaged"})))
    await create_run(api, right, "running")
    response = await api.review_action(Request({"action": "revert", "revision": snapshot["revision"], "path": "example.txt"}))
    assert response.status == 400 and "still using this workspace" in payload(response)["error"]
    assert (repo / "example.txt").read_text() == "keep this change\n"
    create = await api.create_worktree(Request())
    assert create.status == 400 and "still using this workspace" in payload(create)["error"]


async def test_api_create_moves_recorded_task_source_and_preserves_session(api: WorkflowAPI, tmp_path: Path) -> None:
    alternate = make_repo(tmp_path / "alternate")
    (alternate / "example.txt").write_text("alternate user change\n")
    run = await create_run(api, alternate)
    session = Session(mode="plan")
    session.add_user_message("Resume this task.")
    await api.server.run_store.save_session(run.run_id, session)
    response = await api.create_worktree(Request({"run_id": run.run_id, "include_changes": True}))
    assert response.status == 200, response.text
    record = payload(response)["worktree"]
    assert record["repository"] == str(alternate)
    assert (Path(record["path"]) / "example.txt").read_text() == "alternate user change\n"
    saved = await api.server.run_store.load_run(run.run_id)
    assert saved.run_id == run.run_id and saved.working_directory == record["path"]
    restored = await api.server.run_store.load_session(run.run_id)
    assert restored.mode == "plan" and "Resume this task." in str(restored.messages)
    assert (alternate / "example.txt").read_text() == "alternate user change\n"


async def test_api_last_turn_reads_durable_task_checkpoint(api: WorkflowAPI) -> None:
    repo = api.server.config.general.working_directory
    run = await create_run(api, repo)
    session = Session()
    session.checkpoint["last_turn_tree"] = await create_checkpoint(repo, name=run.run_id)
    await api.server.run_store.save_session(run.run_id, session)
    (repo / "example.txt").write_text("last turn change\n")
    response = await api.review(Request(query={"run_id": run.run_id, "scope": "last-turn"}))
    assert response.status == 200
    assert "+last turn change" in payload(response)["patch"]
    missing = await api.review(Request(query={"scope": "last-turn"}))
    assert missing.status == 400 and "checkpoint" in payload(missing)["error"]


async def test_api_comments_validate_line_side_and_revision(api: WorkflowAPI) -> None:
    repo = api.server.config.general.working_directory
    (repo / "example.txt").write_text("new value\n")
    snapshot = payload(await api.review(Request()))
    data = {"path": "example.txt", "line": 1, "side": "right", "body": "Check this value.", "revision": snapshot["revision"], "scope": "unstaged"}
    response = await api.review_comment(Request(data))
    assert response.status == 200
    comment = payload(response)["comment"]
    assert comment["path"] == "example.txt" and comment["line"] == 1
    outside = await api.review_comment(Request({**data, "line": 4}))
    assert outside.status == 400 and "selected line" in payload(outside)["error"]
    (repo / "example.txt").write_text("later edit\n")
    stale = await api.review_comment(Request(data))
    assert stale.status == 400 and "changed since review" in payload(stale)["error"]
    listed = payload(await api.review_comments(Request()))
    assert len(listed["comments"]) == 1


async def test_api_setup_requires_approval_enabled_shell_and_idle_workspace(api: WorkflowAPI) -> None:
    created = payload(await api.create_worktree(Request()))["worktree"]
    worktree_id = created["worktree_id"]
    path = Path(created["path"])
    denied = await api.setup(Request({"commands": ["touch forbidden.txt"]}, worktree_id=worktree_id))
    assert denied.status == 400 and not (path / "forbidden.txt").exists()
    initial_config = api.server.config
    api.server.config = replace(initial_config, agent=replace(initial_config.agent, tool_denylist=("bash",)))
    disabled = await api.setup(Request({"commands": ["touch forbidden.txt"], "approved": True}, worktree_id=worktree_id))
    assert disabled.status == 400 and "disabled" in payload(disabled)["error"]
    assert not (path / "forbidden.txt").exists()
    api.server.config = initial_config
    await api.server.run_store.update_state(created["run_id"], "running")
    active = await api.setup(Request({"commands": ["touch forbidden.txt"], "approved": True}, worktree_id=worktree_id))
    assert active.status == 400 and "still using" in payload(active)["error"]
    await api.server.run_store.update_state(created["run_id"], "done")
    success = await api.setup(Request({"commands": ["printf '%s' 'hello world' > setup.txt"], "approved": True}, worktree_id=worktree_id))
    assert success.status == 200, success.text
    assert not payload(success)["results"][0]["is_error"]
    assert (path / "setup.txt").read_text() == "hello world"


async def test_api_remove_restores_all_inactive_task_workspace_pointers(api: WorkflowAPI) -> None:
    created = payload(await api.create_worktree(Request()))["worktree"]
    other = await create_run(api, Path(created["path"]))
    response = await api.remove_worktree(Request(worktree_id=created["worktree_id"]))
    assert response.status == 200, response.text
    assert not Path(created["path"]).exists()
    for run_id in (created["run_id"], other.run_id):
        assert (await api.server.run_store.load_run(run_id)).working_directory == created["repository"]


async def test_api_transfer_checks_source_activity_and_preserves_unrelated_staging(api: WorkflowAPI) -> None:
    source = api.server.config.general.working_directory
    created = payload(await api.create_worktree(Request()))["worktree"]
    (Path(created["path"]) / "example.txt").write_text("isolated change\n")
    (source / "user.txt").write_text("user staging\n")
    git(source, "add", "user.txt")
    staged = git(source, "diff", "--cached")
    preview = payload(await api.preview_transfer(Request(worktree_id=created["worktree_id"])))
    data = {"revision": preview["review"]["revision"], "target_revision": preview["target_revision"]}
    active = await create_run(api, source, "running")
    blocked = await api.transfer(Request(data, worktree_id=created["worktree_id"]))
    assert blocked.status == 400 and "still using" in payload(blocked)["error"]
    await api.server.run_store.update_state(active.run_id, "done")
    response = await api.transfer(Request(data, worktree_id=created["worktree_id"]))
    assert response.status == 200, response.text
    assert (source / "example.txt").read_text() == "isolated change\n"
    assert git(source, "diff", "--cached") == staged
