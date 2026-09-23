# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import aclosing
from dataclasses import dataclass, replace
from pathlib import Path

from libre_claw.config import LibreClawConfig
from libre_claw.core.agent import Agent, AgentDone, AgentError, AgentPermissionRequest, AgentTextDelta
from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.core.cordis import CordisManager
from libre_claw.core.questions import AgentUserQuestionRequest
from libre_claw.core.git_review import ReviewSnapshot, git_bytes
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session
from libre_claw.tools_builtin import create_builtin_registry, bind_cordis_manager
from libre_claw.providers.factory import create_provider
from libre_claw.providers.base import LLMProvider, Usage
from libre_claw.core.tools import BaseTool, ToolContext, ToolResult


class ReviewSnapshotFileTool(BaseTool):
    """Read immutable versions, even when the checkout differs from staged changes."""

    name = "review_snapshot_file"
    description = "Read lines of a file or its patch from the exact review snapshot. Use base or target for full file contents; patch for the complete file diff."
    parameters = {"path": {"type": "string"}, "version": {"type": "string", "enum": ["base", "target", "patch"]}, "start_line": {"type": "integer"}, "limit": {"type": "integer"}}
    required = ("path", "version")
    permission_level = "allow"

    def __init__(self, context: ToolContext, snapshot: ReviewSnapshot) -> None:
        super().__init__(context)
        self.snapshot = snapshot

    def is_read_only(self, arguments: Mapping[str, object]) -> bool:
        return True

    async def execute(self, path: str, version: str, start_line: int = 1, limit: int = 200) -> ToolResult:
        if not path or Path(path).is_absolute() or ".." in Path(path).parts or "\x00" in path:
            return ToolResult(error="Use a repository-relative path without traversal")
        if version not in {"base", "target", "patch"} or type(start_line) is not int or start_line < 1 or type(limit) is not int or not 1 <= limit <= 400:
            return ToolResult(error="Use base, target, or patch; positive start_line; and limit 1-400")
        if version == "patch":
            file = next((item for item in self.snapshot.files if item.path == path), None)
            if file is None:
                return ToolResult(error="File is not in the review diff")
            content = file.patch
        else:
            oid = self.snapshot.base_oid if version == "base" else self.snapshot.target_oid
            raw = await git_bytes(Path(self.snapshot.repository), "show", f"{oid}:{path}")
            if b"\0" in raw:
                return ToolResult(error="Binary file; text review unavailable")
            content = raw.decode("utf-8", "replace")
        lines = content.splitlines()
        selected = lines[start_line - 1:start_line - 1 + limit]
        rendered = "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected))
        return ToolResult(content=f"{version} {path}: {len(lines)} total lines\n" + rendered[:40000], metadata={"total_lines": len(lines), "truncated": start_line - 1 + limit < len(lines) or len(rendered) > 40000})


@dataclass(frozen=True)
class ReviewResult:
    text: str
    usage: Usage | None


async def review_changes(config: LibreClawConfig, snapshot: ReviewSnapshot, *, feedback: str = "") -> str:
    """Run a separate read-only reviewer without modifying the coding session."""
    return (await run_review(config, snapshot, feedback=feedback)).text


async def run_review(
    config: LibreClawConfig, snapshot: ReviewSnapshot, *, feedback: str = "",
    structured: bool = False, provider: LLMProvider | None = None, timeout: float = 180,
) -> ReviewResult:
    """Run the production reviewer, optionally returning a machine-readable report."""
    if not 1 <= timeout <= 180:
        raise ValueError("Review timeout must be between 1 and 180 seconds")
    config = replace(config, general=replace(config.general, working_directory=Path(snapshot.repository)))
    system_prompt = "You are an independent code reviewer. Inspect the diff and relevant repository code. Do not implement fixes. Report only actionable regressions introduced by the change, with priority P0-P3, file and line, evidence, and a concise suggested fix. Distinguish verified facts from assumptions. Say explicitly when no actionable findings are found, and describe any verification limits."
    if structured:
        system_prompt += '\nReturn only a JSON object with "findings" (an array) and "verification_limits" (a string). Each finding must contain "priority" (P0, P1, P2, or P3), "file" (repository-relative path), "line" (one-based integer), "title", and "body". Include the triggering condition, consequence, and suggested fix in body. Use an empty findings array when there are no actionable findings.'
    registry = create_builtin_registry(config)
    cordis = CordisManager(config=config, tool_timeout=config.cordis.tool_timeout, persistent=True)
    bind_cordis_manager(registry, cordis)
    registry.register(ReviewSnapshotFileTool(ToolContext(working_directory=Path(snapshot.repository)), snapshot))
    system_prompt += "\nReview the immutable base/target snapshots, not unrelated checkout edits. Use review_snapshot_file to read exact versions and omitted patches. Native CLI tools may use git show <base-or-target>:<path> and git diff <base> <target> -- <path>. Report verification limits for any uninspected or truncated content."
    engine = CordisEngine()
    cordis.engine = engine
    agent = Agent(
        engine=engine,
        session=Session(mode="plan"), provider=provider or create_provider(config),
        tool_registry=registry,
        permission_manager=PermissionManager(config.permissions),
        system_prompt=system_prompt,
        max_tool_calls_per_turn=min(32, config.agent.max_tool_calls_per_turn),
        provider_retry_attempts=config.agent.provider_retry_attempts,
    )
    chunks: list[str] = []
    usage: Usage | None = None
    manifest = json.dumps([{"path": item.path, "status": item.status, "binary": item.binary} for item in snapshot.files])
    prompt = f"Review scope: {snapshot.scope}\nRevision: {snapshot.revision}\nBase object: {snapshot.base_oid}\nTarget object: {snapshot.target_oid}\nComplete changed-file manifest: {manifest}\n\nDiff:\n{snapshot.patch[:160000]}\n\nUser review comments:\n{feedback[:16000]}"
    if len(snapshot.patch) > 160000:
        prompt += "\nThe pasted diff is truncated; inspect the remaining changed files with read-only tools before concluding."
    async with engine, aclosing(cordis), asyncio.timeout(timeout), aclosing(agent.run(prompt)) as stream:
        async for event in stream:
            if isinstance(event, AgentUserQuestionRequest):
                if not event.future.done():
                    event.future.set_exception(ValueError("The independent reviewer cannot ask interactive questions."))
            elif isinstance(event, AgentTextDelta):
                chunks.append(event.text)
            elif isinstance(event, AgentPermissionRequest):
                if not event.future.done():
                    event.future.set_result("deny")
            elif isinstance(event, AgentError):
                raise ValueError(event.message)
            elif isinstance(event, AgentDone):
                usage = event.usage
    return ReviewResult("".join(chunks).strip() or "The reviewer returned no findings text.", usage)
