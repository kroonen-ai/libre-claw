# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.core.runs import RunStore


async def test_run_store_creates_events_and_artifacts(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")

    run = await store.create_run(
        "Fix auth bug",
        kind="chat",
        provider="openrouter",
        model="qwen/qwen3.7-max",
        working_directory=tmp_path,
    )
    event = await store.append_event(run.run_id, "user_message", {"content": "hello"})
    finished = await store.finish_run(
        run.run_id,
        "done",
        plan="Read code first.\n",
        summary="All done.",
        verification="Tests passed.\n",
        diff="diff --git a/file b/file\n",
        browser="# Browser\n",
    )

    loaded = await store.load_run(run.run_id)
    events = await store.load_events(run.run_id)
    runs = await store.list_runs()

    assert event.event_id == 1
    assert loaded == finished
    assert finished.state == "done"
    assert finished.working_directory == str(tmp_path)
    assert runs == [finished]
    assert events[0].type == "user_message"
    assert events[0].data == {"content": "hello"}
    assert (run.path / "events.jsonl").exists()
    assert (run.path / "plan.md").read_text(encoding="utf-8") == "Read code first.\n"
    assert (run.path / "summary.md").read_text(encoding="utf-8") == "All done."
    assert (run.path / "verification.md").read_text(encoding="utf-8") == "Tests passed.\n"
    assert (run.path / "diff.patch").read_text(encoding="utf-8").startswith("diff --git")
    assert (run.path / "browser.md").read_text(encoding="utf-8") == "# Browser\n"


async def test_run_store_lists_newest_first(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")

    first = await store.create_run("first", kind="chat", provider="openai", model="gpt-5.5")
    second = await store.create_run("second", kind="goal", provider="openrouter", model="openrouter/auto")

    runs = await store.list_runs()

    assert [run.run_id for run in runs] == [second.run_id, first.run_id]


async def test_run_store_rejects_path_traversal_ids(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")

    assert await store.load_run("../outside") is None
