# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libre_claw.config import LibreClawConfig, load_config
from libre_claw.core.agent import Agent, AgentPermissionRequest
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import Session, session_from_payload, session_to_payload
from libre_claw.core.tools import ToolContext, ToolRegistry
from libre_claw.headless import run_headless
from libre_claw.providers.base import Done, LLMProvider, TextDelta, ToolCallReady
from libre_claw.tools_builtin.filesystem import ReadFileTool, WriteFileTool


TASK_ROOT = Path(__file__).parent / "tasks"
SUITE_VERSION = "coding-workflow-v1"
TASK_IDS = ("pagination", "catalog")
CODING_TOOLS = ("read_file", "write_file", "edit_file", "apply_patch", "list_directory", "glob", "search_files", "git_status", "think", "bash", "task_checkpoint")


class OracleProvider(LLMProvider):
    """Offline validation of fixture solvability and the real tool loop, not model quality."""

    def __init__(self, task_id: str) -> None:
        solution = TASK_ROOT / task_id / "solution"
        self.files = {str(path.relative_to(solution)): path.read_text() for path in sorted(solution.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
        self.phase = 0

    async def complete(self, messages, **kwargs):
        if self.phase == 0:
            self.phase += 1
            for index, path in enumerate(self.files):
                yield ToolCallReady(f"read-{index}", "read_file", {"path": path})
            yield Done()
        elif self.phase == 1:
            self.phase += 1
            for index, (path, content) in enumerate(self.files.items()):
                yield ToolCallReady(f"write-{index}", "write_file", {"path": path, "content": content})
            yield Done()
        else:
            yield TextDelta("Offline fixture solution applied.")
            yield Done()


def fixture_hash(task_id: str) -> str:
    digest = hashlib.sha256()
    for path in sorted((TASK_ROOT / task_id).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(TASK_ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_fixture(task_id: str, workspace: Path) -> dict[str, str]:
    if task_id not in TASK_IDS:
        raise ValueError(f"Unknown task: {task_id}")
    shutil.copytree(TASK_ROOT / task_id / "starter", workspace, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
    protected = {}
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if path.is_file() and ("tests" in relative.parts or path.name in {"AGENTS.md", "sentinel.txt"}):
            protected[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return protected


async def verify_fixture(workspace: Path, protected: dict[str, str]) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v",
        cwd=workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 20)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"passed": False, "error": "Verifier timed out", "tests_passed": False, "protected_files_unchanged": False}
    unchanged = all((workspace / path).is_file() and hashlib.sha256((workspace / path).read_bytes()).hexdigest() == digest for path, digest in protected.items())
    return {
        "passed": process.returncode == 0 and unchanged,
        "tests_passed": process.returncode == 0,
        "protected_files_unchanged": unchanged,
        "verifier_output": stdout.decode(errors="replace")[-8000:],
    }


def benchmark_config(workspace: Path, provider: str, model: str, config_path: Path | None = None) -> LibreClawConfig:
    config = load_config(config_path=config_path, working_directory=workspace)
    providers = dict(config.providers)
    selected = dict(providers.get(provider, {}))
    selected.update({"default_model": model, "max_tokens": 4096})
    if provider == "codex":
        selected.update({"sandbox": "workspace-write", "approval_policy": "never", "timeout": 180})
    providers[provider] = selected
    return replace(
        config,
        general=replace(config.general, working_directory=workspace, default_provider=provider, default_model=model),
        agent=replace(
            config.agent, max_tool_calls_per_turn=30, tool_allowlist=CODING_TOOLS,
            tool_denylist=(), provider_retry_attempts=0,
            system_prompt="Complete the coding task in this temporary fixture. Inspect before editing, follow scoped project instructions, preserve unrelated files, and verify with the existing unittest suite. Do not install dependencies or commit. Return a concise final report.",
            system_prompt_extra="",
        ),
        sandbox=replace(config.sandbox, restrict_to_working_dir=True, command_timeout=30, allow_sudo=False),
        memory=replace(config.memory, enabled=False, archive_sessions=False),
        skills=replace(config.skills, enabled=False),
        automations=replace(config.automations, enabled=False),
        fallback=replace(config.fallback, enabled=False),
        mcp=replace(config.mcp, enabled=False),
        providers=providers,
    )


async def run_task(
    task_id: str, output_directory: Path, *, offline: bool, provider: str = "", model: str = "",
    timeout: float = 180, config_path: Path | None = None,
) -> dict[str, Any]:
    task_output = output_directory / task_id
    task_output.mkdir(parents=True, exist_ok=False)
    workspace = task_output / "workspace"
    protected = prepare_fixture(task_id, workspace)
    baseline = await verify_fixture(workspace, protected)
    if baseline["passed"]:
        raise RuntimeError(f"Invalid fixture {task_id}: starter already passes")
    resolved_provider, resolved_model = ("offline-oracle", "fixture-solution") if offline else (provider, model)
    config = benchmark_config(workspace, provider or "codex", model or "offline", config_path)
    started = time.monotonic()
    error = None
    usage = None
    try:
        result = await run_headless(
            config, (TASK_ROOT / task_id / "prompt.md").read_text(),
            auto_approve=True, provider=OracleProvider(task_id) if offline else None,
            trajectory_path=task_output / "trajectory.json",
            deadline_seconds=timeout, deadline_reserve_seconds=min(5, timeout / 10),
        )
        error, usage = result.error, result.usage
        (task_output / "final.txt").write_text(result.text)
    except Exception as exc:
        error = str(exc)
    elapsed = time.monotonic() - started
    verification = await verify_fixture(workspace, protected)
    return {
        "task_id": task_id, "kind": "harness_validation" if offline else "model_evaluation",
        "provider": resolved_provider, "model": resolved_model, "fixture_sha256": fixture_hash(task_id),
        "passed": verification["passed"] and error is None, "baseline_failed": True,
        "elapsed_seconds": round(elapsed, 3), "error": error,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "cached_tokens": usage.cached_tokens if usage else None,
        "cost_usd": usage.cost if usage else None,
        "verification": verification,
        "trajectory": str(task_output / "trajectory.json"), "workspace": str(workspace),
        "config": {"max_tool_calls": 30, "max_completion_tokens": 4096, "deadline_seconds": timeout, "auto_approve_fixture_tools": True, "restrict_to_working_dir": True},
    }


class InterruptedProvider(LLMProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallReady("phase-one", "write_file", {"path": "phase-one.txt", "content": "verified first phase"})
            yield Done()
        else:
            self.started.set()
            await asyncio.Future()


class RecoveryProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.saw_first_phase = False
        self.saw_requirement = False

    async def complete(self, messages, system=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.saw_first_phase = any(block.get("tool_use_id") == "phase-one" and not block.get("is_error") for message in messages for block in message.content)
            self.saw_requirement = "Preserve the first phase" in (system or "")
            yield ToolCallReady("phase-two", "write_file", {"path": "phase-two.txt", "content": "recovered second phase"})
            yield Done()
        else:
            yield TextDelta("Recovery completed.")
            yield Done()


async def run_recovery_check(output_directory: Path) -> dict[str, Any]:
    started = time.monotonic()
    workspace = output_directory / "interrupted-recovery"
    workspace.mkdir(parents=True, exist_ok=False)
    context = ToolContext(working_directory=workspace)
    registry = ToolRegistry([ReadFileTool(context), WriteFileTool(context)])
    config = load_config(working_directory=workspace)
    permissions = PermissionManager(config.permissions)
    permissions.always_allowed_tools.add("write_file")
    checkpoint_path = workspace / "session.json"
    async def checkpoint(session: Session) -> None:
        checkpoint_path.write_text(json.dumps(session_to_payload(session)))
    session = Session()
    session.update_checkpoint({"requirements": ["Preserve the first phase"], "outstanding": ["finish second phase"]})
    provider = InterruptedProvider()
    first_agent = Agent(session, provider, registry, permissions, "Complete the two phases", checkpoint_callback=checkpoint)
    async def consume(agent: Agent, prompt: str) -> None:
        async for event in agent.run(prompt):
            if isinstance(event, AgentPermissionRequest):
                event.future.set_result("deny")
    task = asyncio.create_task(consume(first_agent, "Complete both phases."))
    await asyncio.wait_for(provider.started.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    restored = session_from_payload(json.loads(checkpoint_path.read_text()))
    recovered_provider = RecoveryProvider()
    second_agent = Agent(restored, recovered_provider, registry, permissions, "Complete the two phases", checkpoint_callback=checkpoint)
    await consume(second_agent, "Resume the unfinished second phase.")
    passed = (
        (workspace / "phase-one.txt").read_text() == "verified first phase"
        and (workspace / "phase-two.txt").read_text() == "recovered second phase"
        and recovered_provider.saw_first_phase and recovered_provider.saw_requirement
    )
    return {
        "task_id": "interrupted-recovery", "kind": "harness_validation", "passed": passed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "input_tokens": None, "output_tokens": None, "cost_usd": None,
        "checkpoint": str(checkpoint_path),
        "checks": {"first_phase_preserved": recovered_provider.saw_first_phase, "requirement_restored": recovered_provider.saw_requirement},
    }


def revision_metadata() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    def git(*args: str) -> str:
        process = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return process.stdout.strip() if process.returncode == 0 else ""
    source_digest = hashlib.sha256()
    for directory in (root / "src", Path(__file__).parent):
        for path in sorted(directory.rglob("*.py")):
            source_digest.update(str(path.relative_to(root)).encode())
            source_digest.update(path.read_bytes())
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")), "tracked_diff_sha256": hashlib.sha256(git("diff", "HEAD").encode()).hexdigest(), "source_tree_sha256": source_digest.hexdigest()}


async def run_suite(
    output_directory: Path, *, offline: bool, provider: str = "", model: str = "",
    task_ids: Sequence[str] = TASK_IDS, timeout: float = 180, config_path: Path | None = None,
) -> dict[str, Any]:
    if not offline and (not provider or not model):
        raise ValueError("Live evaluations require explicit --provider and --model")
    if not 1 <= timeout <= 180:
        raise ValueError("Task timeout must be between 1 and 180 seconds")
    output_directory.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    revision = revision_metadata()
    results = []
    for task_id in task_ids:
        results.append(await run_task(task_id, output_directory, offline=offline, provider=provider, model=model, timeout=timeout, config_path=config_path))
    results.append(await run_recovery_check(output_directory))
    model_results = [item for item in results if item["kind"] == "model_evaluation"]
    report = {
        "suite_version": SUITE_VERSION, "started_at": started_at,
        "mode": "offline_harness_validation" if offline else "live_model_evaluation",
        "revision": revision, "results": results,
        "all_checks_passed": all(item["passed"] for item in results),
        "model_completion_rate": sum(item["passed"] for item in model_results) / len(model_results) if model_results else None,
        "model_cost_usd": sum(item["cost_usd"] for item in model_results) if model_results and all(item["cost_usd"] is not None for item in model_results) else None,
    }
    (output_directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed coding tasks through Libre Claw's real headless harness.")
    parser.add_argument("--offline", action="store_true", help="Validate fixtures with scripted oracle tool calls; does not evaluate a model.")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--task", action="append", choices=TASK_IDS)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="libre-claw-workflow-eval-"))
    report = asyncio.run(run_suite(output.resolve(), offline=args.offline, provider=args.provider, model=args.model, task_ids=args.task or TASK_IDS, timeout=args.timeout, config_path=args.config))
    sys.stdout.write(json.dumps({"results": str(output.resolve() / "results.json"), "all_checks_passed": report["all_checks_passed"], "model_completion_rate": report["model_completion_rate"]}) + "\n")
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
