# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from libre_claw.core.git_review import ReviewCommentStore, ReviewError, capture_review, mutate_review, repository_root
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.reviewer import review_changes
from libre_claw.core.worktrees import WorktreeManager
from libre_claw.tools_builtin import create_builtin_registry

if TYPE_CHECKING:
    from libre_claw.daemon import DaemonServer


class WorkflowAPI:
    def __init__(self, server: DaemonServer) -> None:
        self.server = server
        self.worktrees = WorktreeManager(os.getenv("LIBRE_CLAW_WORKTREE_ROOT", str(server.run_store.root.parent / "worktrees")))
        self.comments = ReviewCommentStore(server.run_store.root.parent / "review-comments")

    def routes(self) -> list[web.RouteDef]:
        return [
            web.get("/workspace/review", self.review),
            web.post("/workspace/review/action", self.review_action),
            web.get("/workspace/review/comments", self.review_comments),
            web.post("/workspace/review/comments", self.review_comment),
            web.post("/workspace/review/analyze", self.analyze),
            web.get("/worktrees", self.list_worktrees),
            web.post("/worktrees", self.create_worktree),
            web.delete("/worktrees/{worktree_id}", self.remove_worktree),
            web.get("/worktrees/{worktree_id}/transfer", self.preview_transfer),
            web.post("/worktrees/{worktree_id}/transfer", self.transfer),
            web.post("/worktrees/{worktree_id}/setup", self.setup),
        ]

    async def workspace(self, data: Any) -> tuple[Path, str, str | None]:
        run_id = str(data.get("run_id", ""))
        repo = self.server.config.general.working_directory
        checkpoint = None
        if run_id:
            run = await self.server.run_store.load_run(run_id)
            if run is None:
                raise ReviewError("Unknown task.")
            repo = Path(run.working_directory) if run.working_directory else repo
            session = self.server._active_sessions.get(run_id) or await self.server.run_store.load_session(run_id)
            checkpoint = session.checkpoint.get("last_turn_tree")
        return repo, run_id, checkpoint

    async def assert_idle(self, *paths: Path) -> None:
        roots = [await repository_root(path) for path in paths]
        for run in await self.server.run_store.list_runs(limit=100000):
            if run.state not in {"queued", "running", "blocked"} or not run.working_directory:
                continue
            directory = Path(run.working_directory).resolve()
            try:
                directory = await repository_root(directory)
            except (ValueError, OSError):
                pass
            if any(directory.is_relative_to(root) or root.is_relative_to(directory) for root in roots):
                raise ReviewError(f"Task {run.run_id} is still using this workspace. Stop it before changing Git state.")

    async def snapshot(self, data: Any) -> Any:
        repo, _, checkpoint = await self.workspace(data)
        return await capture_review(repo, str(data.get("scope", "unstaged")), base_ref=data.get("base_ref") or None, checkpoint=checkpoint)

    async def review(self, request: web.Request) -> web.Response:
        try:
            return web.json_response((await self.snapshot(request.query)).to_payload())
        except (ValueError, OSError) as exc:
            return error(exc)

    async def review_action(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            repo, run_id, _ = await self.workspace(data)
            await self.assert_idle(repo)
            result = await mutate_review(repo, data.get("action", ""), expected_revision=data.get("revision", ""), path=data.get("path", ""), hunk_id=data.get("hunk_id"))
            return web.json_response(result.to_payload())
        except (ValueError, OSError) as exc:
            return error(exc)

    async def review_comments(self, request: web.Request) -> web.Response:
        try:
            repo, run_id, _ = await self.workspace(request.query)
            return web.json_response({"comments": [item.to_payload() for item in await self.comments.list(repo, run_id or "workspace")]})
        except (ValueError, OSError) as exc:
            return error(exc)

    async def review_comment(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            repo, run_id, checkpoint = await self.workspace(data)
            comment = await self.comments.add(repo, run_id or "workspace", path=data.get("path", ""), line=data.get("line"), side=data.get("side", "right"), body=data.get("body", ""), revision=data.get("revision", ""), scope=data.get("scope", "unstaged"), base_ref=data.get("base_ref"), checkpoint=checkpoint)
            return web.json_response({"comment": comment.to_payload()})
        except (ValueError, OSError, TypeError) as exc:
            return error(exc)

    async def analyze(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            snapshot = await self.snapshot(data)
            repo, run_id, _ = await self.workspace(data)
            comments = await self.comments.list(repo, run_id or "workspace")
            feedback = "\n".join(f"{item.path}:{item.line}: {item.body}" for item in comments if item.revision == snapshot.revision)
            text = await review_changes(self.server.config, snapshot, feedback=feedback)
            if run_id:
                await self.server.run_store.append_event(run_id, "code_review", {"revision": snapshot.revision, "text": text})
            return web.json_response({"text": text, "revision": snapshot.revision})
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            return error(exc)

    async def list_worktrees(self, request: web.Request) -> web.Response:
        try:
            return web.json_response({"worktrees": [item.to_payload() for item in await self.worktrees.list()]})
        except (ValueError, OSError) as exc:
            return error(exc)

    async def create_worktree(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            run_id = str(data.get("run_id", ""))
            run = await self.server.run_store.load_run(run_id) if run_id else None
            if run_id and run is None:
                raise ReviewError("Unknown task.")
            if run is not None and run.state in {"running", "queued", "blocked"}:
                raise ReviewError("Stop the task before moving it to a worktree.")
            if run is None:
                config = self.server.config
                run = await self.server.run_store.create_run(str(data.get("title") or "Worktree task"), kind="chat", provider=config.general.default_provider, model=config.general.default_model, working_directory=config.general.working_directory, state="done")
            repository = Path(run.working_directory) if run.working_directory else self.server.config.general.working_directory
            await self.assert_idle(repository)
            record = await self.worktrees.create(repository, run.run_id, ref=str(data.get("ref") or "HEAD"), branch=data.get("branch") or None, include_changes=data.get("include_changes") is True)
            run = await self.server.run_store.set_workspace(run.run_id, Path(record.path))
            await self.server.run_store.append_event(run.run_id, "worktree_created", record.to_payload())
            return web.json_response({"worktree": record.to_payload(), "run": {"run_id": run.run_id, "working_directory": run.working_directory}})
        except (ValueError, OSError) as exc:
            return error(exc)

    async def remove_worktree(self, request: web.Request) -> web.Response:
        try:
            runs = await self.server.run_store.list_runs(limit=100000)
            active = [run.run_id for run in runs if run.state in {"running", "queued", "blocked"}]
            record = await self.worktrees.get(request.match_info["worktree_id"])
            await self.assert_idle(Path(record.path))
            await self.worktrees.remove(request.match_info["worktree_id"], active_run_ids=active)
            for run in runs:
                if run.working_directory and Path(run.working_directory).resolve() == Path(record.path).resolve():
                    await self.server.run_store.set_workspace(run.run_id, Path(record.repository))
            return web.json_response({"removed": True})
        except (ValueError, OSError) as exc:
            return error(exc)

    async def preview_transfer(self, request: web.Request) -> web.Response:
        try:
            return web.json_response((await self.worktrees.preview_transfer(request.match_info["worktree_id"])).to_payload())
        except (ValueError, OSError) as exc:
            return error(exc)

    async def transfer(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            record = await self.worktrees.get(request.match_info["worktree_id"])
            await self.assert_idle(Path(record.path), Path(record.repository))
            result = await self.worktrees.apply_to_source(record.worktree_id, expected_revision=data.get("revision", ""), expected_target_revision=data.get("target_revision", ""))
            return web.json_response(result.to_payload())
        except (ValueError, OSError) as exc:
            return error(exc)

    async def setup(self, request: web.Request) -> web.Response:
        try:
            data = await body(request)
            commands = data.get("commands")
            if not isinstance(commands, list) or data.get("approved") is not True:
                raise ReviewError("Review the setup commands and explicitly approve them before execution.")
            record = await self.worktrees.get(request.match_info["worktree_id"])
            await self.assert_idle(Path(record.path))
            registry = create_builtin_registry(self.server.config)
            if registry.context is None or not any(schema.get("name") == "bash" for schema in registry.schemas()):
                raise ReviewError("Shell setup is disabled in this configuration.")
            async def approval(_call: Any) -> str:
                return "allow_once"
            results = await self.worktrees.run_setup(request.match_info["worktree_id"], commands, context=registry.context, permission_manager=PermissionManager(self.server.config.permissions), request_permission=approval)
            return web.json_response({"results": [{"content": result.as_text(), "is_error": result.is_error} for result in results]})
        except (ValueError, OSError) as exc:
            return error(exc)


async def body(request: web.Request) -> dict[str, Any]:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Request body must be an object.")
    return data


def error(exc: Exception) -> web.Response:
    return web.json_response({"error": str(exc)}, status=400)
