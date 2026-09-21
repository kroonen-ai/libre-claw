# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.core.git_review import capture_review
from libre_claw.core.reviewer import run_review
from libre_claw.providers.base import Done, LLMProvider, TextDelta


REVIEW_ROOT = Path(__file__).parent / "tasks" / "review-quality"


def prepare_review_fixture(workspace: Path) -> None:
    shutil.copytree(REVIEW_ROOT / "before", workspace, ignore=shutil.ignore_patterns("__pycache__"))
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Workflow Eval", "-c", "user.email=eval@example.invalid", "commit", "-qm", "Create review baseline")):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    shutil.copytree(REVIEW_ROOT / "after", workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))


def workspace_hash(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(workspace.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(workspace).parts and "__pycache__" not in path.parts:
            digest.update(path.relative_to(workspace).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def parse_review_report(text: str) -> dict[str, Any]:
    """Accept a final JSON report, including preceding streamed commentary/fences."""
    decoder = json.JSONDecoder()
    report = None
    for match in re.finditer(r"\{", text):
        try:
            value, end = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        suffix = text[match.start() + end:].strip()
        if isinstance(value, dict) and "findings" in value and suffix in {"", "```"}:
            report = value
            break
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list) or not isinstance(report.get("verification_limits"), str):
        raise ValueError("Reviewer must return a JSON findings array and verification_limits string")
    for finding in report["findings"]:
        if not isinstance(finding, dict):
            raise ValueError("Every review finding must be an object")
        if finding.get("priority") not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("Every finding needs priority P0-P3")
        if any(not isinstance(finding.get(key), str) or not finding[key].strip() for key in ("file", "title", "body")):
            raise ValueError("Every finding needs nonempty file, title, and body")
        if type(finding.get("line")) is not int or finding["line"] < 1:
            raise ValueError("Every finding needs a positive integer line")
        if Path(finding["file"]).is_absolute() or ".." in Path(finding["file"]).parts:
            raise ValueError("Review paths must be repository-relative")
    return report


def parse_review(text: str) -> list[dict[str, Any]]:
    return parse_review_report(text)["findings"]


def score_review(text: str, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conservatively score grounded bug evidence; names or keywords alone do not pass."""
    expected = expected or json.loads((REVIEW_ROOT / "expected.json").read_text())
    try:
        report = parse_review_report(text)
        findings = report["findings"]
    except ValueError as exc:
        return {"passed": False, "schema_valid": False, "error": str(exc), "precision": None, "recall": 0.0}
    matched: set[str] = set()
    false_positives: list[int] = []
    benign_findings: list[int] = []
    for index, finding in enumerate(findings):
        path = Path(finding["file"]).as_posix()
        evidence = finding["title"] + " " + finding["body"]
        bug = next((bug for bug in expected["bugs"] if bug["id"] not in matched and path == bug["file"] and finding["line"] in bug["lines"] and all(re.search(pattern, evidence, re.IGNORECASE) for pattern in bug["evidence_patterns"])), None)
        if bug is None:
            false_positives.append(index)
        else:
            matched.add(bug["id"])
        if path in expected["benign_files"]:
            benign_findings.append(index)
    precision = len(matched) / len(findings) if findings else 0.0
    recall = len(matched) / len(expected["bugs"])
    return {
        "passed": recall == 1 and not false_positives, "schema_valid": True,
        "precision": precision, "recall": recall, "true_positives": len(matched),
        "false_positives": len(false_positives), "false_negatives": len(expected["bugs"]) - len(matched),
        "matched_bug_ids": sorted(matched), "false_positive_finding_indexes": false_positives,
        "benign_change_findings": len(benign_findings), "findings": findings,
        "verification_limits": report["verification_limits"],
        "scoring_note": "Fixed-fixture rubric: path, changed-code line, and trigger/consequence evidence; unmatched or duplicate findings count as false positives. This is not a general review-quality judge.",
    }


class OracleReviewer(LLMProvider):
    async def complete(self, messages, **kwargs):
        expected = json.loads((REVIEW_ROOT / "expected.json").read_text())
        yield TextDelta(json.dumps({"findings": expected["oracle_findings"], "verification_limits": "Scripted fixture oracle; not model quality."}))
        yield Done()


async def run_review_task(output_directory: Path, config: LibreClawConfig, *, offline: bool, timeout: float, fixture_sha256: str) -> dict[str, Any]:
    task_output = output_directory / "review-quality"
    workspace = task_output / "workspace"
    task_output.mkdir(parents=True, exist_ok=False)
    prepare_review_fixture(workspace)
    before = workspace_hash(workspace)
    snapshot = await capture_review(workspace)
    started = time.monotonic()
    error, usage, text = None, None, ""
    try:
        result = await run_review(config, snapshot, feedback=(REVIEW_ROOT / "prompt.md").read_text(), structured=True, provider=OracleReviewer() if offline else None, timeout=timeout)
        text, usage = result.text, result.usage
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    (task_output / "final.txt").write_text(text)
    verification = score_review(text)
    verification["workspace_unchanged"] = before == workspace_hash(workspace)
    return {
        "task_id": "review-quality", "category": "review", "kind": "harness_validation" if offline else "model_evaluation",
        "provider": "offline-oracle" if offline else config.general.default_provider,
        "model": "fixture-solution" if offline else config.general.default_model,
        "fixture_sha256": fixture_sha256, "passed": verification["passed"] and verification["workspace_unchanged"] and error is None,
        "elapsed_seconds": round(time.monotonic() - started, 3), "error": error,
        "input_tokens": usage.input_tokens if usage else None, "output_tokens": usage.output_tokens if usage else None,
        "cached_tokens": usage.cached_tokens if usage else None, "cost_usd": usage.cost if usage else None,
        "verification": verification, "workspace": "review-quality/workspace", "response": "review-quality/final.txt",
        "config": {"max_tool_calls": min(32, config.agent.max_tool_calls_per_turn), "max_completion_tokens": 4096, "deadline_seconds": timeout, "read_only": True, "completion_token_limit_enforced": config.general.default_provider != "codex", "tool_call_limit_enforced": config.general.default_provider != "codex"},
    }
