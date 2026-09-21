# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from libre_claw.config import load_config
from libre_claw.core.agent import Agent, AgentError, AgentPermissionRequest
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.runs import RunStore
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolContext, ToolRegistry, ToolResult
from libre_claw.providers.base import Done, LLMProvider, TextDelta, ToolCallReady
from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool


class InterruptedWrite(WriteFileTool):
    """Simulate a completed side effect whose result never reaches the agent."""

    name = "record_phase"

    def __init__(self, context: ToolContext) -> None:
        super().__init__(context)
        self.started = asyncio.Event()

    async def execute(self, path: str, content: str, overwrite: bool = True) -> ToolResult:
        await super().execute(path, content, overwrite)
        self.started.set()
        await asyncio.Future()
        raise AssertionError("Interrupted write must not finish")


class InterruptedProvider(LLMProvider):
    async def complete(self, messages, **kwargs):
        yield ToolCallReady("phase-one", "record_phase", {"path": "ledger.json", "content": '{"completed": ["phase-one"], "sentinel": "preserve-me"}'})
        yield Done()


class RecoveryProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.checks: dict[str, bool] = {}

    async def complete(self, messages, system=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            blocks = [block for message in messages for block in message.content]
            self.checks["uncertain_tool_completion_reported"] = any(block.get("tool_use_id") == "phase-one" and block.get("is_error") and "Completion is unknown" in str(block.get("content")) for block in blocks)
            self.checks["requirement_restored"] = "Do not repeat completed phases" in (system or "")
            self.checks["decision_restored"] = "Append phase-two to the existing ledger" in (system or "")
            self.checks["remaining_work_restored"] = "finish phase-two" in (system or "")
            yield ToolCallReady("inspect-ledger", "read_file", {"path": "ledger.json"})
            yield Done()
        elif self.calls == 2:
            result = next(block for message in reversed(messages) for block in message.content if block.get("tool_use_id") == "inspect-ledger")
            raw = str(result.get("content"))
            self.checks["inspected_side_effect_before_retry"] = "phase-one" in raw and "preserve-me" in raw and not result.get("is_error")
            if all(self.checks.values()):
                yield ToolCallReady("phase-two", "write_file", {"path": "ledger.json", "content": '{"completed": ["phase-one", "phase-two"], "sentinel": "preserve-me"}'})
            yield Done()
        else:
            yield TextDelta("Recovered after inspecting the uncertain write; no phase was repeated.")
            yield Done()


async def run_recovery_check(output_directory: Path) -> dict[str, Any]:
    started = time.monotonic()
    workspace = output_directory / "interrupted-recovery"
    workspace.mkdir(parents=True, exist_ok=False)
    store = RunStore(workspace / "runs")
    record = await store.create_run("Two-phase ledger", kind="coding", provider="offline-oracle", model="recovery-fixture", working_directory=workspace)
    context = ToolContext(working_directory=workspace)
    interrupted_write = InterruptedWrite(context)
    config = load_config(working_directory=workspace)
    permissions = PermissionManager(config.permissions)
    permissions.always_allowed_tools.add("write_file")
    permissions.always_allowed_tools.add("record_phase")

    async def checkpoint(session: Session) -> None:
        await store.save_session(record.run_id, session)

    session = Session()
    session.update_checkpoint({"requirements": ["Do not repeat completed phases", "Preserve ledger sentinel"], "decisions": ["Append phase-two to the existing ledger"], "changed_files": ["ledger.json"], "outstanding": ["finish phase-two"]})
    first_agent = Agent(session, InterruptedProvider(), ToolRegistry([ReadFileTool(context), interrupted_write]), permissions, "Complete the ledger", checkpoint_callback=checkpoint)

    async def consume(agent: Agent, prompt: str) -> None:
        async for event in agent.run(prompt):
            if isinstance(event, AgentPermissionRequest):
                event.future.set_result("deny")
            elif isinstance(event, AgentError):
                raise RuntimeError(event.message)

    task = asyncio.create_task(consume(first_agent, "Complete both phases exactly once."))
    try:
        await asyncio.wait_for(interrupted_write.started.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await store.update_state(record.run_id, "cancelled")
    checkpoint_path = record.path / "session.json"
    interrupted_payload = checkpoint_path.read_bytes()
    (workspace / "interrupted-session.json").write_bytes(interrupted_payload)
    restarted_store = RunStore(workspace / "runs")
    restored = await restarted_store.load_session(record.run_id, recover=True)
    recovered_provider = RecoveryProvider()
    second_agent = Agent(restored, recovered_provider, ToolRegistry([ReadFileTool(context), WriteFileTool(context)]), permissions, "Complete the ledger", checkpoint_callback=checkpoint)
    await asyncio.wait_for(consume(second_agent, "Resume the unfinished phase."), 5)
    ledger = json.loads((workspace / "ledger.json").read_text())
    restored_record = await restarted_store.load_run(record.run_id)
    checks = {
        **recovered_provider.checks,
        "each_phase_completed_once": ledger["completed"] == ["phase-one", "phase-two"],
        "sentinel_preserved": ledger["sentinel"] == "preserve-me",
        "workspace_restored": restored_record is not None and restored_record.working_directory == str(workspace),
        "runtime_restored": restored_record is not None and (restored_record.provider, restored_record.model) == ("offline-oracle", "recovery-fixture"),
    }
    await restarted_store.finish_run(record.run_id, "done" if all(checks.values()) else "failed", summary="Scripted semantic recovery check")
    return {
        "task_id": "interrupted-recovery", "category": "recovery", "kind": "harness_validation", "passed": all(checks.values()),
        "elapsed_seconds": round(time.monotonic() - started, 3), "input_tokens": None, "output_tokens": None, "cached_tokens": None, "cost_usd": None,
        "checkpoint": "interrupted-recovery/interrupted-session.json", "interrupted_checkpoint_sha256": hashlib.sha256(interrupted_payload).hexdigest(),
        "checks": checks, "scope_note": "Scripted providers exercise actual tools, cancellation during a side effect, durable RunStore snapshots and semantic recovery. This does not measure model recovery quality.",
    }
