# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import re
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runner import COMPARISON_TASK_IDS, SUITE_VERSION, fixture_hash, revision_metadata, run_suite


def load_targets(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text())
    targets = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(targets, list) or not 2 <= len(targets) <= 8:
        raise ValueError("Comparison requires 2-8 explicit targets")
    result: list[dict[str, str]] = []
    for target in targets:
        if not isinstance(target, dict) or any(not isinstance(target.get(key), str) or not target[key].strip() for key in ("id", "provider", "model")):
            raise ValueError("Every target requires explicit id, provider, and model strings")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", target["id"]):
            raise ValueError("Target id must be a short lowercase path-safe identifier")
        if target["id"] in {item["id"] for item in result}:
            raise ValueError("Target ids must be unique")
        item = {key: target[key] for key in ("id", "provider", "model")}
        if target.get("config"):
            if not isinstance(target["config"], str):
                raise ValueError("Target config must be a file path")
            config = (path.parent / target["config"]).resolve()
            if not config.is_file():
                raise ValueError("Target config does not exist")
            item["config"] = str(config)
        result.append(item)
    return result


def _sum_known(results: Sequence[dict[str, Any]], field: str) -> float | int | None:
    values = [result.get(field) for result in results]
    return sum(values) if values and all(type(value) in {int, float} and math.isfinite(value) for value in values) else None


def portable_report(report: dict[str, Any], output: Path) -> dict[str, Any]:
    """Keep checked-in reports portable without shipping local workspace paths."""
    serialized = json.dumps(report)
    for path, label in ((output.resolve(), "<output>"), (Path(__file__).resolve().parents[2], "<source>"), (Path.home(), "<home>")):
        serialized = serialized.replace(str(path), label)
    return json.loads(serialized)


async def run_comparison(
    targets: Sequence[dict[str, str]], output: Path, *, trials: int = 1,
    task_ids: Sequence[str] = COMPARISON_TASK_IDS, timeout: float = 180, offline: bool = False,
) -> dict[str, Any]:
    if not 2 <= len(targets) <= 8 or type(trials) is not int or not 1 <= trials <= 3:
        raise ValueError("Use 2-8 targets and 1-3 trials")
    if not 1 <= timeout <= 180:
        raise ValueError("Task timeout must be between 1 and 180 seconds")
    if not task_ids or len(set(task_ids)) != len(task_ids) or any(task not in COMPARISON_TASK_IDS for task in task_ids):
        raise ValueError("Use unique versioned task IDs")
    if any(not target.get("provider") or not target.get("model") or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", target.get("id", "")) for target in targets) or len({target["id"] for target in targets}) != len(targets):
        raise ValueError("Use unique target ids and explicit provider/model pairs")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Comparison output directory must be empty")
    revision = revision_metadata()
    report: dict[str, Any] = {
        "suite_version": SUITE_VERSION, "started_at": datetime.now(UTC).isoformat(), "revision": revision,
        "mode": "offline_harness_validation" if offline else "live_provider_comparison", "python": platform.python_version(),
        "task_ids": list(task_ids), "fixture_sha256": {task: fixture_hash(task) for task in task_ids},
        "trials_per_target": trials, "per_task_deadline_seconds": timeout, "targets": [],
        "scope_note": "Small fixed regression tasks; not a leaderboard or statistical ranking. Offline runs validate only the harness. Recovery is scripted and excluded from model scores. Missing token usage/cost remains null; cost totals require complete provider-reported costs. Codex CLI output tokens and native tool calls cannot be capped by the bridge and are bounded only by the deadline.",
    }
    for target in targets:
        target_result: dict[str, Any] = {key: target[key] for key in ("id", "provider", "model")}
        target_result["trials"] = []
        for trial in range(1, trials + 1):
            directory = output / target["id"] / f"trial-{trial}"
            try:
                suite = await run_suite(directory, offline=offline, provider=target["provider"], model=target["model"], task_ids=task_ids, timeout=timeout, config_path=Path(target["config"]) if target.get("config") else None)
                target_result["trials"].append({"trial": trial, "report": directory.relative_to(output).as_posix() + "/results.json", **suite})
            except Exception as exc:
                target_result["trials"].append({"trial": trial, "results": [], "all_checks_passed": False, "error": type(exc).__name__ + ": " + str(exc)})
        results = [item for suite in target_result["trials"] for item in suite["results"] if item.get("category") in {"coding", "review"}]
        expected_count = len(task_ids) * trials
        target_result["summary"] = {
            "completed_attempts": len(results), "expected_attempts": expected_count,
            "passed": sum(result["passed"] for result in results),
            "model_completion_rate": sum(result["passed"] for result in results) / expected_count if not offline else None,
            "elapsed_seconds": _sum_known(results, "elapsed_seconds"),
            **{field: _sum_known(results, field) if len(results) == expected_count else None for field in ("input_tokens", "output_tokens", "cached_tokens", "cost_usd")},
            "review_precision": [item["verification"].get("precision") for item in results if item["category"] == "review"],
            "review_recall": [item["verification"].get("recall") for item in results if item["category"] == "review"],
            "usage_source": "provider-reported, when available" if not offline else "not applicable",
        }
        report["targets"].append(target_result)
        (output / "comparison.json").write_text(json.dumps(portable_report(report, output), indent=2) + "\n")
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["revision_after"] = revision_metadata()
    report["source_changed_during_run"] = revision["source_tree_sha256"] != report["revision_after"]["source_tree_sha256"]
    report["all_checks_passed"] = all(suite["all_checks_passed"] for target in report["targets"] for suite in target["trials"])
    report["distinct_provider_paths"] = len({target["provider"] for target in targets})
    report = portable_report(report, output)
    (output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare explicit provider/model targets on identical fixed coding and review fixtures.")
    parser.add_argument("--targets", required=True, type=Path, help="JSON file with targets [{id, provider, model, config?}]")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--task", action="append", choices=COMPARISON_TASK_IDS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="libre-claw-comparison-"))
    report = asyncio.run(run_comparison(load_targets(args.targets), output.resolve(), trials=args.trials, task_ids=args.task or COMPARISON_TASK_IDS, timeout=args.timeout, offline=args.offline))
    sys.stdout.write(json.dumps({"report": str(output.resolve() / "comparison.json"), "all_checks_passed": report["all_checks_passed"]}) + "\n")
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
