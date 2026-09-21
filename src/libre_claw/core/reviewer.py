# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from libre_claw.config import LibreClawConfig
from libre_claw.core.agent import Agent, AgentError, AgentPermissionRequest, AgentTextDelta
from libre_claw.core.git_review import ReviewSnapshot
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session
from libre_claw.tools_builtin import create_builtin_registry
from libre_claw.providers.factory import create_provider


async def review_changes(config: LibreClawConfig, snapshot: ReviewSnapshot, *, feedback: str = "") -> str:
    """Run a separate read-only reviewer without modifying the coding session."""
    config = replace(config, general=replace(config.general, working_directory=Path(snapshot.repository)))
    agent = Agent(
        session=Session(mode="plan"), provider=create_provider(config),
        tool_registry=create_builtin_registry(config),
        permission_manager=PermissionManager(config.permissions),
        system_prompt="You are an independent code reviewer. Inspect the diff and relevant repository code. Do not implement fixes. Report only actionable regressions introduced by the change, with priority P0-P3, file and line, evidence, and a concise suggested fix. Distinguish verified facts from assumptions. Say explicitly when no actionable findings are found, and describe any verification limits.",
        max_tool_calls_per_turn=32,
    )
    chunks: list[str] = []
    prompt = f"Review scope: {snapshot.scope}\nRevision: {snapshot.revision}\n\nDiff:\n{snapshot.patch[:160000]}\n\nUser review comments:\n{feedback[:16000]}"
    if len(snapshot.patch) > 160000:
        prompt += "\nThe pasted diff is truncated; inspect the remaining changed files with read-only tools before concluding."
    async with asyncio.timeout(180):
        async for event in agent.run(prompt):
            if isinstance(event, AgentTextDelta):
                chunks.append(event.text)
            elif isinstance(event, AgentPermissionRequest):
                if not event.future.done():
                    event.future.set_result("deny")
            elif isinstance(event, AgentError):
                raise ValueError(event.message)
    return "".join(chunks).strip() or "The reviewer returned no findings text."
