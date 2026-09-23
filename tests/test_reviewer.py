# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from benchmarks.coding_workflow.review_eval import prepare_review_fixture
from libre_claw.config import load_config
from libre_claw.core.agent import AgentError
from libre_claw.core.git_review import ReviewSnapshot, capture_review, git_bytes
from libre_claw.core.reviewer import ReviewSnapshotFileTool, run_review
from libre_claw.core.tools import ToolContext, ToolRegistry
from libre_claw.providers.base import Done, LLMProvider, TextDelta, ToolCallReady, Usage


async def test_reviewer_reads_immutable_staged_files_and_complete_patch(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    prepare_review_fixture(workspace)
    await git_bytes(workspace, "add", ".")
    snapshot = await capture_review(workspace, "staged")
    (workspace / "access.py").write_text("new unrelated checkout edits\n")
    tool = ReviewSnapshotFileTool(ToolContext(working_directory=workspace), snapshot)
    target = await tool.execute("access.py", "target")
    base = await tool.execute("access.py", "base")
    patch = await tool.execute("access.py", "patch", start_line=1, limit=2)
    assert "or active" in target.content and "unrelated" not in target.content
    assert "and active" in base.content
    assert patch.metadata["truncated"]
    assert (await tool.execute("../secret", "target")).is_error
    assert (await tool.execute("access.py", "target", limit=9999)).is_error


class InspectReviewer(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.prompt = ""
        self.system = ""

    async def complete(self, messages, system=None, **kwargs):
        self.calls += 1
        self.system = system or ""
        self.prompt = messages[0].content[0]["text"]
        if self.calls == 1:
            yield ToolCallReady("try-write", "write_file", {"path": "access.py", "content": "changed"})
            yield Done()
        else:
            result = next(block for message in messages for block in message.content if block.get("tool_use_id") == "try-write")
            assert result["is_error"] and "Plan mode" in result["content"]
            yield TextDelta('{"findings": [], "verification_limits": "fixture"}')
            yield Done(usage=Usage(input_tokens=123, output_tokens=12))


async def test_review_uses_read_only_plan_and_complete_snapshot_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    prepare_review_fixture(workspace)
    snapshot = await capture_review(workspace)
    snapshot = replace(snapshot, patch="x" * 160001)
    provider = InspectReviewer()
    config = load_config(working_directory=workspace)
    original = (workspace / "access.py").read_text()
    result = await run_review(config, snapshot, provider=provider, structured=True, timeout=10)
    assert result.usage is not None and result.usage.input_tokens == 123
    assert snapshot.base_oid in provider.prompt and snapshot.target_oid in provider.prompt
    assert all(file.path in provider.prompt for file in snapshot.files)
    assert "truncated" in provider.prompt
    assert "review_snapshot_file" in provider.system
    assert "verification_limits" in provider.system
    assert (workspace / "access.py").read_text() == original


async def test_reviewer_error_closes_stream_before_workers_and_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    snapshot = ReviewSnapshot(str(tmp_path), "unstaged", "revision", "base", "target", "", ())
    order = []

    class Engine:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            order.append("engine")

    class Manager:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            order.append("plugins")

    class StreamAgent:
        def __init__(self, **kwargs):
            pass

        async def run(self, prompt):
            try:
                yield AgentError("review stopped")
            finally:
                await asyncio.sleep(0)
                order.append("agent")

    monkeypatch.setattr("libre_claw.core.reviewer.CordisEngine", Engine)
    monkeypatch.setattr("libre_claw.core.reviewer.CordisManager", Manager)
    monkeypatch.setattr("libre_claw.core.reviewer.bind_cordis_manager", lambda *args: None)
    monkeypatch.setattr("libre_claw.core.reviewer.create_builtin_registry", lambda *args: ToolRegistry())
    monkeypatch.setattr("libre_claw.core.reviewer.Agent", StreamAgent)
    with pytest.raises(ValueError, match="review stopped"):
        await run_review(config, snapshot, provider=InspectReviewer())
    assert order == ["agent", "plugins", "engine"]
