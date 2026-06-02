# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


AutomationStatus = Literal["active", "paused"]
AutomationRoute = Literal["report", "tui", "telegram"]

_ROUTES: set[str] = {"report", "tui", "telegram"}
_STATUSES: set[str] = {"active", "paused"}
_UNSET = object()
_WEEKDAYS = {
    "sun": 0,
    "sunday": 0,
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
}


class AutomationError(RuntimeError):
    """Raised when an automation cannot be parsed or stored safely."""


@dataclass(frozen=True)
class AutomationRecord:
    automation_id: str
    name: str
    prompt: str
    schedule: str
    route: AutomationRoute
    status: AutomationStatus
    provider: str
    model: str
    working_directory: str
    created_at: str
    updated_at: str
    next_run_at: str
    last_run_at: str | None
    last_run_id: str | None
    telegram_chat_id: int | None
    report_path: str | None
    metadata: dict[str, Any]
    path: Path


class AutomationStore:
    """File-backed schedule store for local recurring Libre Claw runs."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_automations_path()
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        name: str,
        prompt: str,
        schedule: str,
        route: AutomationRoute = "report",
        provider: str = "",
        model: str = "",
        working_directory: str | Path = "",
        status: AutomationStatus = "active",
        telegram_chat_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutomationRecord:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_sync,
                name,
                prompt,
                schedule,
                route,
                provider,
                model,
                working_directory,
                status,
                telegram_chat_id,
                metadata or {},
            )

    async def list(self, limit: int = 50) -> list[AutomationRecord]:
        return await asyncio.to_thread(self._list_sync, limit)

    async def load(self, automation_id: str) -> AutomationRecord | None:
        return await asyncio.to_thread(self._load_sync, automation_id)

    async def update_status(self, automation_id: str, status: AutomationStatus) -> AutomationRecord | None:
        async with self._lock:
            return await asyncio.to_thread(self._update_status_sync, automation_id, status)

    async def update(
        self,
        automation_id: str,
        *,
        name: str | None = None,
        prompt: str | None = None,
        schedule: str | None = None,
        route: AutomationRoute | None = None,
        status: AutomationStatus | None = None,
        provider: str | None = None,
        model: str | None = None,
        telegram_chat_id: int | None | object = _UNSET,
    ) -> AutomationRecord | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_sync,
                automation_id,
                name,
                prompt,
                schedule,
                route,
                status,
                provider,
                model,
                telegram_chat_id,
            )

    async def update_global_model(self, provider: str, model: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._update_global_model_sync, provider, model)

    async def delete(self, automation_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, automation_id)

    async def due(self, *, now: datetime | None = None, limit: int = 20) -> list[AutomationRecord]:
        current = now or _now_dt()
        records = await self.list(limit=1000)
        due_records = [record for record in records if automation_is_due(record, current)]
        due_records.sort(key=lambda record: record.next_run_at)
        return due_records[: max(1, limit)]

    async def mark_run(
        self,
        automation_id: str,
        run_id: str,
        *,
        now: datetime | None = None,
        report_path: str | Path | None = None,
    ) -> AutomationRecord | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._mark_run_sync,
                automation_id,
                run_id,
                now or _now_dt(),
                str(report_path) if report_path is not None else None,
            )

    def report_path(self, automation_id: str, run_id: str) -> Path:
        if not _safe_id(automation_id) or not _safe_id(run_id):
            raise AutomationError("Unsafe automation or run id.")
        return self.root / "reports" / automation_id / f"{run_id}.md"

    def _create_sync(
        self,
        name: str,
        prompt: str,
        schedule: str,
        route: AutomationRoute,
        provider: str,
        model: str,
        working_directory: str | Path,
        status: AutomationStatus,
        telegram_chat_id: int | None,
        metadata: dict[str, Any],
    ) -> AutomationRecord:
        name = " ".join(name.split())
        prompt = prompt.strip()
        schedule = " ".join(schedule.split())
        if not name:
            raise AutomationError("Automation name is required.")
        if not prompt:
            raise AutomationError("Automation prompt is required.")
        if route not in _ROUTES:
            raise AutomationError("Automation route must be report, tui, or telegram.")
        if status not in _STATUSES:
            raise AutomationError("Automation status must be active or paused.")

        now = _now_dt()
        next_run_at = next_scheduled_at(schedule, after=now)
        self.root.mkdir(parents=True, exist_ok=True)
        automation_id = _new_automation_id()
        path = self.root / f"{automation_id}.json"
        record = AutomationRecord(
            automation_id=automation_id,
            name=name[:120],
            prompt=prompt,
            schedule=schedule,
            route=route,
            status=status,
            provider=provider,
            model=model,
            working_directory=str(Path(working_directory).expanduser()) if working_directory else "",
            created_at=_iso(now),
            updated_at=_iso(now),
            next_run_at=_iso(next_run_at),
            last_run_at=None,
            last_run_id=None,
            telegram_chat_id=telegram_chat_id,
            report_path=None,
            metadata=dict(metadata),
            path=path,
        )
        _write_record(path, record)
        return record

    def _list_sync(self, limit: int) -> list[AutomationRecord]:
        if not self.root.exists():
            return []
        records = [
            record
            for path in self.root.glob("*.json")
            for record in [_load_record(path)]
            if record is not None
        ]
        records.sort(key=lambda record: (record.status != "active", record.next_run_at, record.name))
        return records[: max(1, limit)]

    def _load_sync(self, automation_id: str) -> AutomationRecord | None:
        if not _safe_id(automation_id):
            return None
        return _load_record(self.root / f"{automation_id}.json")

    def _update_status_sync(self, automation_id: str, status: AutomationStatus) -> AutomationRecord | None:
        if status not in _STATUSES:
            raise AutomationError("Automation status must be active or paused.")
        record = self._load_sync(automation_id)
        if record is None:
            return None
        now = _now_dt()
        updated = _replace_record(
            record,
            status=status,
            updated_at=_iso(now),
            next_run_at=record.next_run_at if status == "paused" else _iso(next_scheduled_at(record.schedule, after=now)),
        )
        _write_record(record.path, updated)
        return updated

    def _update_sync(
        self,
        automation_id: str,
        name: str | None,
        prompt: str | None,
        schedule: str | None,
        route: AutomationRoute | None,
        status: AutomationStatus | None,
        provider: str | None,
        model: str | None,
        telegram_chat_id: int | None | object,
    ) -> AutomationRecord | None:
        record = self._load_sync(automation_id)
        if record is None:
            return None

        next_name = " ".join(name.split()) if name is not None else record.name
        next_prompt = prompt.strip() if prompt is not None else record.prompt
        next_schedule = " ".join(schedule.split()) if schedule is not None else record.schedule
        next_route = route or record.route
        next_status = status or record.status
        next_provider = provider.strip() if provider is not None else record.provider
        next_model = model.strip() if model is not None else record.model
        next_chat_id = record.telegram_chat_id if telegram_chat_id is _UNSET else telegram_chat_id

        if not next_name:
            raise AutomationError("Automation name is required.")
        if not next_prompt:
            raise AutomationError("Automation prompt is required.")
        if next_route not in _ROUTES:
            raise AutomationError("Automation route must be report, tui, or telegram.")
        if next_status not in _STATUSES:
            raise AutomationError("Automation status must be active or paused.")
        if next_chat_id is not None and not isinstance(next_chat_id, int):
            raise AutomationError("Telegram chat id must be an integer or empty.")

        now = _now_dt()
        schedule_changed = next_schedule != record.schedule
        status_changed = next_status != record.status
        next_run_at = record.next_run_at
        if schedule_changed or next_status == "active" and status_changed:
            next_run_at = _iso(next_scheduled_at(next_schedule, after=now))

        updated = _replace_record(
            record,
            name=next_name[:120],
            prompt=next_prompt,
            schedule=next_schedule,
            route=next_route,
            status=next_status,
            provider=next_provider,
            model=next_model,
            telegram_chat_id=next_chat_id,
            updated_at=_iso(now),
            next_run_at=next_run_at,
        )
        _write_record(record.path, updated)
        return updated

    def _update_global_model_sync(self, provider: str, model: str) -> int:
        clean_provider = "ollama" if provider.strip().lower() == "local" else provider.strip().lower()
        clean_model = model.strip()
        if not clean_provider:
            raise AutomationError("Provider cannot be empty.")
        if not clean_model:
            raise AutomationError("Model cannot be empty.")
        if not self.root.exists():
            return 0

        now = _iso(_now_dt())
        updated_count = 0
        for path in self.root.glob("*.json"):
            record = _load_record(path)
            if record is None:
                continue
            updated = _replace_record(
                record,
                provider=clean_provider,
                model=clean_model,
                updated_at=now,
            )
            _write_record(record.path, updated)
            updated_count += 1
        return updated_count

    def _delete_sync(self, automation_id: str) -> bool:
        if not _safe_id(automation_id):
            return False
        path = self.root / f"{automation_id}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _mark_run_sync(
        self,
        automation_id: str,
        run_id: str,
        now: datetime,
        report_path: str | None,
    ) -> AutomationRecord | None:
        record = self._load_sync(automation_id)
        if record is None:
            return None
        updated = _replace_record(
            record,
            updated_at=_iso(now),
            last_run_at=_iso(now),
            last_run_id=run_id,
            next_run_at=_iso(next_scheduled_at(record.schedule, after=now)),
            report_path=report_path or record.report_path,
        )
        _write_record(record.path, updated)
        return updated


def default_automations_path() -> Path:
    return Path.home() / ".libre-claw" / "automations"


def automation_examples() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "Daily repo health check",
            "daily 09:00",
            "Inspect git status, recent changes, failing tests if practical, and summarize repo health with risks.",
        ),
        (
            "Weekly dependency review",
            "weekly mon 10:00",
            "Review dependency files and CI configuration. Report outdated or risky dependencies and suggest safe next actions.",
        ),
        (
            "Morning brief",
            "daily 08:30",
            "Summarize active runs, blocked approvals, notable repo changes, and today's recommended priorities.",
        ),
    )


def automation_is_due(record: AutomationRecord, now: datetime | None = None) -> bool:
    if record.status != "active":
        return False
    current = now or _now_dt()
    try:
        scheduled = datetime.fromisoformat(record.next_run_at)
    except ValueError:
        scheduled = next_scheduled_at(record.schedule, after=current - timedelta(minutes=1))
    if scheduled.tzinfo is None and current.tzinfo is not None:
        scheduled = scheduled.replace(tzinfo=current.tzinfo)
    return scheduled <= current


def next_scheduled_at(schedule: str, *, after: datetime | None = None) -> datetime:
    parsed = _parse_schedule(schedule)
    base = _floor_minute(after or _now_dt())
    kind = parsed["kind"]

    if kind == "every_minutes":
        return base + timedelta(minutes=int(parsed["minutes"]))

    if kind == "daily":
        candidate = base.replace(hour=int(parsed["hour"]), minute=int(parsed["minute"]))
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate

    if kind == "weekly":
        target_dow = int(parsed["weekday"])
        candidate = base.replace(hour=int(parsed["hour"]), minute=int(parsed["minute"]))
        current_dow = _cron_weekday(candidate)
        days = (target_dow - current_dow) % 7
        candidate += timedelta(days=days)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    if kind == "cron":
        return _next_cron(parsed, base)

    raise AutomationError(f"Unsupported schedule: {schedule}")


def _parse_schedule(schedule: str) -> dict[str, object]:
    cleaned = " ".join(schedule.strip().lower().split())
    if not cleaned:
        raise AutomationError("Schedule is required.")
    if cleaned == "hourly":
        return {"kind": "every_minutes", "minutes": 60}

    every = re.fullmatch(r"every\s+([1-9]\d*)\s+minutes?", cleaned)
    if every:
        minutes = int(every.group(1))
        if minutes > 24 * 60:
            raise AutomationError("Every-minute schedules are limited to 1440 minutes.")
        return {"kind": "every_minutes", "minutes": minutes}

    daily = re.fullmatch(r"daily\s+(\d{1,2}):(\d{2})", cleaned)
    if daily:
        hour, minute = _time_parts(daily.group(1), daily.group(2))
        return {"kind": "daily", "hour": hour, "minute": minute}

    weekly = re.fullmatch(r"weekly\s+([a-z]+)\s+(\d{1,2}):(\d{2})", cleaned)
    if weekly:
        weekday = _WEEKDAYS.get(weekly.group(1))
        if weekday is None:
            raise AutomationError("Weekly schedules must use a weekday like mon or monday.")
        hour, minute = _time_parts(weekly.group(2), weekly.group(3))
        return {"kind": "weekly", "weekday": weekday, "hour": hour, "minute": minute}

    fields = cleaned.split()
    if len(fields) == 5:
        minute, hour, day, month, weekday = fields
        return {
            "kind": "cron",
            "minute": _cron_field(minute, 0, 59),
            "hour": _cron_field(hour, 0, 23),
            "day": _cron_field(day, 1, 31),
            "month": _cron_field(month, 1, 12),
            "weekday": _cron_field(weekday, 0, 7, aliases=_WEEKDAYS, normalize_seven=True),
        }

    raise AutomationError(
        "Schedule must look like `daily 09:00`, `weekly mon 10:00`, `every 30 minutes`, `hourly`, or five-field cron."
    )


def _time_parts(hour_text: str, minute_text: str) -> tuple[int, int]:
    hour = int(hour_text)
    minute = int(minute_text)
    if hour > 23 or minute > 59:
        raise AutomationError("Schedule time must be in 24-hour HH:MM format.")
    return hour, minute


def _cron_field(
    field: str,
    minimum: int,
    maximum: int,
    *,
    aliases: dict[str, int] | None = None,
    normalize_seven: bool = False,
) -> set[int] | None:
    if field == "*":
        return None
    if field.startswith("*/"):
        step_text = field[2:]
        if not step_text.isdigit() or int(step_text) <= 0:
            raise AutomationError(f"Invalid cron step: {field}")
        values = set(range(minimum, maximum + 1, int(step_text)))
        if normalize_seven and 7 in values:
            values.remove(7)
            values.add(0)
        return values

    values: set[int] = set()
    for item in field.split(","):
        value = aliases.get(item, None) if aliases is not None else None
        if value is None:
            if not item.isdigit():
                raise AutomationError(f"Invalid cron field value: {item}")
            value = int(item)
        if normalize_seven and value == 7:
            value = 0
        if value < minimum or value > maximum:
            raise AutomationError(f"Cron field value out of range: {item}")
        values.add(value)
    return values


def _next_cron(parsed: dict[str, object], base: datetime) -> datetime:
    candidate = base + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _cron_matches(parsed, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise AutomationError("Could not find a matching cron time within one year.")


def _cron_matches(parsed: dict[str, object], candidate: datetime) -> bool:
    fields = {
        "minute": candidate.minute,
        "hour": candidate.hour,
        "day": candidate.day,
        "month": candidate.month,
        "weekday": _cron_weekday(candidate),
    }
    for key, value in fields.items():
        allowed = parsed.get(key)
        if isinstance(allowed, set) and value not in allowed:
            return False
    return True


def _cron_weekday(value: datetime) -> int:
    return (value.weekday() + 1) % 7


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _new_automation_id() -> str:
    return f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _safe_id(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    path = Path(value)
    return not path.is_absolute() and path.name == value and "/" not in value and "\\" not in value


def _record_to_json(record: AutomationRecord) -> dict[str, Any]:
    return {
        "automation_id": record.automation_id,
        "name": record.name,
        "prompt": record.prompt,
        "schedule": record.schedule,
        "route": record.route,
        "status": record.status,
        "provider": record.provider,
        "model": record.model,
        "working_directory": record.working_directory,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "next_run_at": record.next_run_at,
        "last_run_at": record.last_run_at,
        "last_run_id": record.last_run_id,
        "telegram_chat_id": record.telegram_chat_id,
        "report_path": record.report_path,
        "metadata": record.metadata,
    }


def _load_record(path: Path) -> AutomationRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    route = str(payload.get("route", "report"))
    if route not in _ROUTES:
        route = "report"
    status = str(payload.get("status", "paused"))
    if status not in _STATUSES:
        status = "paused"
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    telegram_chat_id = payload.get("telegram_chat_id")
    if not isinstance(telegram_chat_id, int):
        telegram_chat_id = None
    next_run_at = str(payload.get("next_run_at", ""))
    schedule = str(payload.get("schedule", ""))
    if not next_run_at:
        try:
            next_run_at = _iso(next_scheduled_at(schedule))
        except AutomationError:
            next_run_at = _iso(_now_dt())
            status = "paused"
    return AutomationRecord(
        automation_id=str(payload.get("automation_id", path.stem)),
        name=str(payload.get("name", "Untitled automation")),
        prompt=str(payload.get("prompt", "")),
        schedule=schedule,
        route=route,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        working_directory=str(payload.get("working_directory", "")),
        created_at=str(payload.get("created_at", "")),
        updated_at=str(payload.get("updated_at", "")),
        next_run_at=next_run_at,
        last_run_at=payload.get("last_run_at") if isinstance(payload.get("last_run_at"), str) else None,
        last_run_id=payload.get("last_run_id") if isinstance(payload.get("last_run_id"), str) else None,
        telegram_chat_id=telegram_chat_id,
        report_path=payload.get("report_path") if isinstance(payload.get("report_path"), str) else None,
        metadata=metadata,
        path=path,
    )


def _replace_record(record: AutomationRecord, **changes: Any) -> AutomationRecord:
    values = _record_to_json(record)
    values.update(changes)
    return AutomationRecord(
        automation_id=str(values["automation_id"]),
        name=str(values["name"]),
        prompt=str(values["prompt"]),
        schedule=str(values["schedule"]),
        route=values["route"],
        status=values["status"],
        provider=str(values["provider"]),
        model=str(values["model"]),
        working_directory=str(values["working_directory"]),
        created_at=str(values["created_at"]),
        updated_at=str(values["updated_at"]),
        next_run_at=str(values["next_run_at"]),
        last_run_at=values["last_run_at"] if isinstance(values["last_run_at"], str) else None,
        last_run_id=values["last_run_id"] if isinstance(values["last_run_id"], str) else None,
        telegram_chat_id=values["telegram_chat_id"] if isinstance(values["telegram_chat_id"], int) else None,
        report_path=values["report_path"] if isinstance(values["report_path"], str) else None,
        metadata=dict(values["metadata"]) if isinstance(values["metadata"], dict) else {},
        path=record.path,
    )


def _write_record(path: Path, record: AutomationRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(_record_to_json(record), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp_path.replace(path)
