# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libre_claw.core.automations import (
    AutomationError,
    AutomationStore,
    automation_examples,
    automation_is_due,
    next_scheduled_at,
)


def test_next_scheduled_at_supports_aliases_and_cron() -> None:
    base = datetime(2026, 5, 25, 8, 15, tzinfo=timezone.utc)

    assert next_scheduled_at("daily 09:00", after=base) == datetime(2026, 5, 25, 9, 0, tzinfo=timezone.utc)
    assert next_scheduled_at("daily 07:00", after=base) == datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc)
    assert next_scheduled_at("weekly mon 10:00", after=base) == datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    assert next_scheduled_at("weekly mon 07:00", after=base) == datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    assert next_scheduled_at("every 30 minutes", after=base) == datetime(2026, 5, 25, 8, 45, tzinfo=timezone.utc)
    assert next_scheduled_at("hourly", after=base) == datetime(2026, 5, 25, 9, 15, tzinfo=timezone.utc)
    assert next_scheduled_at("0 12 * * mon", after=base) == datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_next_scheduled_at_rejects_invalid_schedule() -> None:
    with pytest.raises(AutomationError):
        next_scheduled_at("whenever")


async def test_automation_store_create_list_due_mark_and_delete(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations")
    now = datetime.now().astimezone()
    record = await store.create(
        name="Health",
        prompt="Check repo",
        schedule="daily 09:00",
        provider="openrouter",
        model="openrouter/auto",
        working_directory=tmp_path,
    )

    listed = await store.list()
    loaded = await store.load(record.automation_id)

    assert listed[0].automation_id == record.automation_id
    assert loaded is not None
    assert loaded.provider == "openrouter"
    assert loaded.model == "openrouter/auto"
    assert loaded.working_directory == str(tmp_path)
    assert not automation_is_due(record, now)

    due_record = await store.create(name="Due", prompt="Run now", schedule="every 1 minutes")
    due_payload = due_record.path.read_text(encoding="utf-8").replace(
        due_record.next_run_at,
        (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
    )
    due_record.path.write_text(due_payload, encoding="utf-8")

    due = await store.due(now=now)
    assert [item.automation_id for item in due] == [due_record.automation_id]

    report = store.report_path(due_record.automation_id, "run-123")
    updated = await store.mark_run(due_record.automation_id, "run-123", now=now, report_path=report)
    assert updated is not None
    assert updated.last_run_id == "run-123"
    assert updated.report_path == str(report)
    assert updated.next_run_at > now.isoformat(timespec="seconds")

    paused = await store.update_status(record.automation_id, "paused")
    assert paused is not None
    assert paused.status == "paused"
    assert await store.delete(record.automation_id) is True
    assert await store.load(record.automation_id) is None


async def test_automation_store_updates_editable_fields(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations")
    record = await store.create(
        name="HN watch",
        prompt="Old prompt",
        schedule="daily 09:00",
        route="report",
        provider="openrouter",
        model="openrouter/auto",
    )

    updated = await store.update(
        record.automation_id,
        name="HN watch updated",
        prompt="New prompt",
        schedule="every 45 minutes",
        route="telegram",
        status="paused",
        provider="ollama",
        model="kimi-k2.6:cloud",
        telegram_chat_id=123,
    )
    loaded = await store.load(record.automation_id)

    assert updated is not None
    assert loaded is not None
    assert loaded.name == "HN watch updated"
    assert loaded.prompt == "New prompt"
    assert loaded.schedule == "every 45 minutes"
    assert loaded.route == "telegram"
    assert loaded.status == "paused"
    assert loaded.provider == "ollama"
    assert loaded.model == "kimi-k2.6:cloud"
    assert loaded.telegram_chat_id == 123
    assert loaded.created_at == record.created_at
    assert loaded.next_run_at != record.next_run_at


async def test_automation_store_updates_global_model_for_all_records(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations")
    first = await store.create(
        name="HN watch",
        prompt="Brief HN",
        schedule="daily 09:00",
        provider="openrouter",
        model="minimax/minimax-m3",
    )
    second = await store.create(
        name="Repo health",
        prompt="Check repo",
        schedule="hourly",
        provider="anthropic",
        model="claude-opus-4-8",
    )

    updated_count = await store.update_global_model("openrouter", "xiaomi/mimo-v2.5-pro")
    loaded_first = await store.load(first.automation_id)
    loaded_second = await store.load(second.automation_id)

    assert updated_count == 2
    assert loaded_first is not None
    assert loaded_second is not None
    assert loaded_first.provider == "openrouter"
    assert loaded_first.model == "xiaomi/mimo-v2.5-pro"
    assert loaded_second.provider == "openrouter"
    assert loaded_second.model == "xiaomi/mimo-v2.5-pro"


def test_automation_examples_include_required_workflows() -> None:
    examples = {name for name, _schedule, _prompt in automation_examples()}

    assert "Daily repo health check" in examples
    assert "Weekly dependency review" in examples
    assert "Morning brief" in examples
