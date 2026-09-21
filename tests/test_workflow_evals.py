# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.coding_workflow.runner import COMPARISON_TASK_IDS, TASK_IDS, prepare_fixture, run_suite, verify_fixture


async def test_fixed_coding_workflows_pass_offline_without_model_claims(tmp_path: Path) -> None:
    report = await run_suite(tmp_path / "results", offline=True)
    assert report["all_checks_passed"]
    assert report["mode"] == "offline_harness_validation"
    assert report["model_completion_rate"] is None
    assert report["model_cost_usd"] is None
    assert len(report["results"]) == len(COMPARISON_TASK_IDS) + 1
    for result in report["results"]:
        assert result["kind"] == "harness_validation"
        assert result["cost_usd"] is None
        assert result["input_tokens"] is None
        if "trajectory" in result:
            assert (tmp_path / "results" / result["trajectory"]).is_file()
            assert result["baseline_failed"]
    assert (tmp_path / "results" / "results.json").is_file()
    assert all(report["results"][-1]["checks"].values())
    assert report["results"][-1]["checks"]["uncertain_tool_completion_reported"]
    assert report["results"][-1]["checks"]["each_phase_completed_once"]
    assert report["results"][-2]["verification"]["precision"] == 1
    assert report["results"][-2]["verification"]["recall"] == 1


async def test_fixture_baselines_fail_and_modified_verifiers_cannot_pass(tmp_path: Path) -> None:
    for task_id in TASK_IDS:
        workspace = tmp_path / task_id
        protected = prepare_fixture(task_id, workspace)
        baseline = await verify_fixture(workspace, protected)
        assert not baseline["passed"]
        for relative in protected:
            if relative.startswith("tests/"):
                (workspace / relative).write_text("import unittest\nclass EmptyTest(unittest.TestCase):\n    def test_empty(self):\n        self.assertTrue(True)\n")
        tampered = await verify_fixture(workspace, protected)
        assert tampered["tests_passed"]
        assert not tampered["protected_files_unchanged"]
        assert not tampered["passed"]


async def test_live_workflows_require_explicit_provider_and_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit --provider and --model"):
        await run_suite(tmp_path, offline=False)
    with pytest.raises(ValueError, match="between 1 and 180"):
        await run_suite(tmp_path, offline=True, timeout=181)


async def test_existing_evaluation_results_are_not_overwritten(tmp_path: Path) -> None:
    existing = tmp_path / "results.json"
    existing.write_text('{"keep": true}')
    with pytest.raises(ValueError, match="empty"):
        await run_suite(tmp_path, offline=True)
    assert existing.read_text() == '{"keep": true}'
