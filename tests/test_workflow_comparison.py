# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from benchmarks.coding_workflow.comparison import load_targets, run_comparison
from benchmarks.coding_workflow.review_eval import REVIEW_ROOT, parse_review, score_review
from benchmarks.coding_workflow.runner import run_suite


def valid_review() -> dict:
    return {"findings": json.loads((REVIEW_ROOT / "expected.json").read_text())["oracle_findings"], "verification_limits": "Static inspection only."}


def test_seeded_review_fixture_has_exact_regressions_and_benign_change() -> None:
    for version in ("before", "after"):
        access = runpy.run_path(str(REVIEW_ROOT / version / "access.py"))["can_open"]
        pages = runpy.run_path(str(REVIEW_ROOT / version / "pagination.py"))["page_count"]
        labels = runpy.run_path(str(REVIEW_ROOT / version / "formatting.py"))["sorted_labels"]
        assert access("owner", "owner", True)
        assert not access("owner", "other", False)
        assert access("owner", "other", True) == (version == "after")
        assert access("owner", "owner", False) == (version == "after")
        with pytest.raises(ValueError if version == "before" else ZeroDivisionError):
            pages(10, 0)
        assert pages(10, 3) == 4
        original = ["b", "a", "a"]
        assert labels(original) == ["a", "a", "b"]
        assert original == ["b", "a", "a"]


def test_review_scoring_requires_locations_evidence_and_no_false_positives() -> None:
    report = valid_review()
    assert score_review(json.dumps(report))["passed"]
    assert score_review("Inspecting changes...\n```json\n" + json.dumps(report) + "\n```")["passed"]
    wrong_line = copy.deepcopy(report)
    wrong_line["findings"][0]["line"] = 999
    assert score_review(json.dumps(wrong_line))["recall"] == .5
    no_evidence = copy.deepcopy(report)
    no_evidence["findings"][0].update(title="Bad code", body="This seems incorrect")
    assert score_review(json.dumps(no_evidence))["recall"] == .5
    duplicated = copy.deepcopy(report)
    duplicated["findings"].append(duplicated["findings"][0])
    score = score_review(json.dumps(duplicated))
    assert not score["passed"] and score["false_positives"] == 1
    benign = copy.deepcopy(report)
    benign["findings"].append({"priority": "P2", "file": "formatting.py", "line": 6, "title": "Avoid sorted", "body": "This is a speculative style preference."})
    score = score_review(json.dumps(benign))
    assert score["recall"] == 1 and score["benign_change_findings"] == 1 and not score["passed"]
    assert not score_review('{"findings": [], "verification_limits": ""}')["passed"]
    assert not score_review("Everything is fine")["schema_valid"]


@pytest.mark.parametrize("field,value", [("line", True), ("line", 0), ("priority", "urgent"), ("file", "../access.py"), ("file", "/tmp/access.py"), ("body", "")])
def test_review_schema_rejects_ambiguous_or_ungrounded_findings(field: str, value: object) -> None:
    report = valid_review()
    report["findings"][0][field] = value
    with pytest.raises(ValueError):
        parse_review(json.dumps(report))


async def test_comparison_preserves_identical_fixtures_and_unknown_costs(tmp_path: Path) -> None:
    targets = [{"id": "one", "provider": "codex", "model": "test-one"}, {"id": "two", "provider": "openrouter", "model": "test-two"}]
    report = await run_comparison(targets, tmp_path / "report", timeout=10, offline=True)
    assert report["all_checks_passed"]
    assert report["distinct_provider_paths"] == 2
    for target in report["targets"]:
        assert target["summary"]["model_completion_rate"] is None
        assert target["summary"]["cost_usd"] is None
        assert target["summary"]["completed_attempts"] == 3
        for result in target["trials"][0]["results"]:
            if "fixture_sha256" in result:
                assert result["fixture_sha256"] == report["fixture_sha256"][result["task_id"]]
    assert str(tmp_path) not in json.dumps(report)


def test_targets_require_explicit_unique_safe_ids(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    for invalid in ([{"provider": "codex", "model": "m"}], [{"id": "../one", "provider": "codex", "model": "m"}, {"id": "two", "provider": "openrouter", "model": "n"}], [{"id": "same", "provider": "codex", "model": "m"}] * 2):
        path.write_text(json.dumps({"targets": invalid}))
        with pytest.raises(ValueError):
            load_targets(path)


async def test_comparison_bounds_and_output_reuse(tmp_path: Path) -> None:
    targets = [{"id": "one", "provider": "codex", "model": "m"}, {"id": "two", "provider": "openrouter", "model": "n"}]
    for kwargs in ({"trials": 0}, {"trials": 4}, {"timeout": 181}, {"task_ids": ["pagination", "pagination"]}):
        with pytest.raises(ValueError):
            await run_comparison(targets, tmp_path, offline=True, **kwargs)
    (tmp_path / "keep.txt").write_text("preserve")
    with pytest.raises(ValueError, match="empty"):
        await run_comparison(targets, tmp_path, offline=True)
    assert (tmp_path / "keep.txt").read_text() == "preserve"


async def test_comparison_keeps_failed_targets_and_partial_costs_unknown(tmp_path: Path, monkeypatch) -> None:
    async def run_suite(directory, *, provider, **kwargs):
        if provider == "unavailable":
            raise RuntimeError("Provider is unavailable")
        return {"all_checks_passed": True, "results": [{"category": "coding", "passed": True, "elapsed_seconds": 1, "input_tokens": 10, "output_tokens": 2, "cached_tokens": 0, "cost_usd": .01}]}
    monkeypatch.setattr("benchmarks.coding_workflow.comparison.run_suite", run_suite)
    targets = [{"id": "one", "provider": "unavailable", "model": "m"}, {"id": "two", "provider": "test", "model": "n"}]
    report = await run_comparison(targets, tmp_path / "report", task_ids=["pagination", "catalog"])
    assert not report["all_checks_passed"]
    assert report["targets"][0]["summary"]["model_completion_rate"] == 0
    assert report["targets"][1]["summary"]["model_completion_rate"] == .5
    assert report["targets"][1]["summary"]["cost_usd"] is None


async def test_suite_keeps_finished_task_results_when_recovery_fails(tmp_path: Path, monkeypatch) -> None:
    async def broken_recovery(directory):
        raise RuntimeError("Cannot reload snapshot")
    monkeypatch.setattr("benchmarks.coding_workflow.runner.run_recovery_check", broken_recovery)
    report = await run_suite(tmp_path / "report", offline=True, task_ids=["pagination"])
    assert not report["all_checks_passed"]
    assert report["results"][0]["passed"]
    assert not report["results"][1]["passed"]
    assert "Cannot reload snapshot" in report["results"][1]["error"]
