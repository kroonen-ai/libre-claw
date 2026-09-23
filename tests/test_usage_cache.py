# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections import Counter
from types import SimpleNamespace

import pytest

from libre_claw.config import load_config
from libre_claw.core.runs import RunStore
from libre_claw.core.usage import UsageHistoryCache, load_usage_records
from libre_claw.daemon import DaemonServer


async def _run(store, title="Example", *, provider="deepseek", tokens=10):
    run = await store.create_run(title, kind="chat", provider=provider, model="test-model")
    await store.append_event(run.run_id, "run_started", {"surface": "dashboard"})
    await store.append_event(run.run_id, "usage", {"input_tokens": tokens, "output_tokens": 2})
    return run


def _track_reads(monkeypatch, store):
    reads = Counter()
    original = store.load_events

    async def tracked(run_id):
        reads[run_id] += 1
        return await original(run_id)

    monkeypatch.setattr(store, "load_events", tracked)
    return reads


async def test_repeated_usage_reads_reuse_unchanged_histories(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    runs = [await _run(store, f"Run {index}", tokens=index) for index in range(5)]
    expected = await load_usage_records(store)
    reads = _track_reads(monkeypatch, store)
    cache = UsageHistoryCache(store)

    for _ in range(6):
        assert await cache.load() == expected

    assert reads == Counter({run.run_id: 1 for run in runs})


async def test_only_changed_run_is_reread_including_another_writer(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    untouched = await _run(store, "Untouched")
    changed = await _run(store, "Changed")
    reads = _track_reads(monkeypatch, store)
    cache = UsageHistoryCache(store)
    assert len(await cache.load()) == 2

    other_store = RunStore(store.root)
    await other_store.append_event(changed.run_id, "usage", {"input_tokens": 23, "output_tokens": 4})
    records = await cache.load()

    assert sorted(record.total_tokens for record in records) == [12, 12, 27]
    assert reads == Counter({changed.run_id: 2, untouched.run_id: 1})


async def test_direct_event_append_without_metadata_update_is_detected(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    run = await _run(store)
    cache = UsageHistoryCache(store)
    await cache.load()
    metadata = (run.path / "meta.json").read_bytes()
    with (run.path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event_id": 3, "timestamp": "2030-01-01T00:00:00Z", "type": "usage",
            "data": {"input_tokens": 90, "output_tokens": 10},
        }) + "\n")

    records = await cache.load()

    assert len(records) == 2
    assert records[0].total_tokens == 100
    assert (run.path / "meta.json").read_bytes() == metadata


@pytest.mark.parametrize("replace_file", [False, True], ids=["rewrite", "atomic-replacement"])
async def test_equal_size_event_rewrite_with_preserved_mtime_is_detected(tmp_path, replace_file):
    store = RunStore(tmp_path / "runs")
    run = await _run(store)
    cache = UsageHistoryCache(store)
    assert (await cache.load())[0].input_tokens == 10
    path = run.path / "events.jsonl"
    before = path.stat()
    content = path.read_text().replace('"input_tokens": 10', '"input_tokens": 90')
    target = path.with_suffix(".replacement") if replace_file else path
    target.write_text(content)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    if replace_file:
        target.replace(path)
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns

    assert (await cache.load())[0].input_tokens == 90


async def test_new_deleted_and_changed_run_metadata_are_reflected(tmp_path):
    store = RunStore(tmp_path / "runs")
    old = await _run(store)
    cache = UsageHistoryCache(store)
    await cache.load()
    other_store = RunStore(store.root)
    await other_store.set_runtime(old.run_id, provider="openrouter", model="replacement-model")
    await other_store.update_state(old.run_id, "done")
    metadata_path = old.path / "meta.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["title"] = "Renamed"
    metadata_path.write_text(json.dumps(metadata))

    changed = (await cache.load())[0]
    assert (changed.title, changed.state, changed.provider, changed.model) == (
        "Renamed", "done", "openrouter", "replacement-model",
    )
    assert await cache.load(provider="deepseek") == []
    assert await cache.load(provider="OPENROUTER") == [changed]

    new = await _run(other_store, "New", tokens=30)
    assert {record.run_id for record in await cache.load()} == {old.run_id, new.run_id}
    shutil.rmtree(old.path)
    assert {record.run_id for record in await cache.load()} == {new.run_id}


async def test_provider_and_limit_match_uncached_reporting_without_poisoning_cache(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    await _run(store, "First", provider="deepseek")
    await _run(store, "Second", provider="openrouter")
    await _run(store, "Third", provider="deepseek")
    cache = UsageHistoryCache(store)
    for provider, limit in (("deepseek", 1), (None, 3), ("OPENROUTER", 2), (None, 1), (None, 0)):
        assert await cache.load(provider=provider, limit=limit) == await load_usage_records(
            store, provider=provider, limit=limit,
        )

    reads = _track_reads(monkeypatch, store)
    await cache.load(limit=3)
    await cache.load(provider="deepseek", limit=3)
    await cache.load(provider="openrouter", limit=3)
    assert not reads


async def test_concurrent_requests_share_history_reads(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    run = await _run(store)
    reads = _track_reads(monkeypatch, store)
    cache = UsageHistoryCache(store)

    responses = await asyncio.wait_for(asyncio.gather(*(cache.load() for _ in range(12))), timeout=5)

    assert all(response == responses[0] for response in responses)
    assert len(responses[0]) == 1
    assert reads == Counter({run.run_id: 1})
    responses[0].clear()
    assert len(await cache.load()) == 1


async def test_cache_capacity_bounds_retention_without_truncating_results(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    for index in range(3):
        await _run(store, f"Run {index}")
    reads = _track_reads(monkeypatch, store)
    cache = UsageHistoryCache(store, max_entries=2)

    expected = await cache.load(limit=3)
    assert len(expected) == 3
    assert sum(reads.values()) == 3
    assert await cache.load(limit=3) == expected
    # At least one history must be read again: keeping all three would exceed
    # the configured retention bound even though all three belong in the report.
    assert sum(reads.values()) > 3


async def test_empty_or_missing_event_history_is_refreshed_when_events_appear(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    run = await store.create_run("Empty", kind="chat", provider="deepseek", model="test-model")
    (run.path / "events.jsonl").unlink()
    reads = _track_reads(monkeypatch, store)
    cache = UsageHistoryCache(store)
    assert await cache.load() == []
    assert await cache.load() == []
    assert reads[run.run_id] == 1
    await store.append_event(run.run_id, "usage", {"input_tokens": 4})
    assert (await cache.load())[0].input_tokens == 4
    assert reads[run.run_id] == 2


async def test_change_during_read_is_not_cached_as_fresh(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    run = await _run(store)
    original = store.load_events
    changed = False

    async def changing_read(run_id):
        nonlocal changed
        events = await original(run_id)
        if not changed:
            changed = True
            await RunStore(store.root).append_event(run_id, "usage", {"input_tokens": 50})
        return events

    monkeypatch.setattr(store, "load_events", changing_read)
    cache = UsageHistoryCache(store)
    await cache.load()
    records = await cache.load()
    assert len(records) == 2
    assert sum(record.input_tokens for record in records) == 60


async def test_failed_read_releases_lock_and_is_retried(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    await _run(store)
    original = store.load_events

    async def failed_read(_run_id):
        raise RuntimeError("Temporary read failure")

    monkeypatch.setattr(store, "load_events", failed_read)
    cache = UsageHistoryCache(store)
    with pytest.raises(RuntimeError, match="Temporary read failure"):
        await cache.load()
    monkeypatch.setattr(store, "load_events", original)
    assert len(await asyncio.wait_for(cache.load(), timeout=5)) == 1


async def test_cancelled_read_releases_lock_and_is_retried(monkeypatch, tmp_path):
    store = RunStore(tmp_path / "runs")
    await _run(store)
    entered = asyncio.Event()
    original = store.load_events

    async def waiting_read(_run_id):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(store, "load_events", waiting_read)
    cache = UsageHistoryCache(store)
    task = asyncio.create_task(cache.load())
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(store, "load_events", original)
    assert len(await asyncio.wait_for(cache.load(), timeout=5)) == 1


async def test_daemon_usage_does_not_increment_engine_operations_or_require_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    store = RunStore(tmp_path / "runs")
    for index in range(5):
        await _run(store, f"Run {index}")
    server = DaemonServer(load_config(), run_store=store)
    await server.engine.start()
    before = await server.engine.inspect()
    reads = _track_reads(monkeypatch, store)
    request = SimpleNamespace(query={"provider": "all"})
    for _ in range(4):
        response = await server.usage(request)
        assert json.loads(response.body)["summary"]["total_tokens"] == 60
    after = await server.engine.inspect()
    assert after["components"] == before["components"]
    assert sum(reads.values()) == 5

    await server.engine.aclose()
    await _run(store, "Added while engine stopped", tokens=20)
    response = await server.usage(request)
    assert json.loads(response.body)["summary"]["total_tokens"] == 82
    assert not server.engine.running


async def test_replacing_daemon_run_store_drops_old_usage_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    old_store = RunStore(tmp_path / "old-runs")
    await _run(old_store, tokens=10)
    server = DaemonServer(load_config(), run_store=old_store)
    request = SimpleNamespace(query={})
    assert json.loads((await server.usage(request)).body)["summary"]["total_tokens"] == 12
    new_store = RunStore(tmp_path / "new-runs")
    await _run(new_store, tokens=40)
    server.run_store = new_store
    assert json.loads((await server.usage(request)).body)["summary"]["total_tokens"] == 42
