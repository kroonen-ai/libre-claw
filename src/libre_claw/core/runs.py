# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


RunState = Literal["queued", "running", "blocked", "done", "failed", "cancelled"]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    state: RunState
    title: str
    kind: str
    provider: str
    model: str
    working_directory: str
    created_at: str
    updated_at: str
    path: Path


@dataclass(frozen=True)
class RunEvent:
    event_id: int
    timestamp: str
    type: str
    data: dict[str, Any]


class RunStore:
    """File-system backed durable run log."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_runs_path()
        self._lock = asyncio.Lock()

    async def create_run(
        self,
        title: str,
        *,
        kind: str,
        provider: str,
        model: str,
        working_directory: str | Path | None = None,
        state: RunState = "running",
    ) -> RunRecord:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_run_sync,
                title,
                kind,
                provider,
                model,
                working_directory,
                state,
            )

    async def append_event(self, run_id: str, event_type: str, data: dict[str, Any] | None = None) -> RunEvent:
        async with self._lock:
            return await asyncio.to_thread(self._append_event_sync, run_id, event_type, data or {})

    async def update_state(self, run_id: str, state: RunState) -> RunRecord:
        async with self._lock:
            return await asyncio.to_thread(self._update_state_sync, run_id, state)

    async def finish_run(
        self,
        run_id: str,
        state: RunState,
        *,
        summary: str = "",
        verification: str = "",
        diff: str = "",
    ) -> RunRecord:
        async with self._lock:
            return await asyncio.to_thread(self._finish_run_sync, run_id, state, summary, verification, diff)

    async def list_runs(self, limit: int = 20) -> list[RunRecord]:
        return await asyncio.to_thread(self._list_runs_sync, limit)

    async def load_run(self, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._load_run_sync, run_id)

    async def load_events(self, run_id: str) -> list[RunEvent]:
        return await asyncio.to_thread(self._load_events_sync, run_id)

    def _create_run_sync(
        self,
        title: str,
        kind: str,
        provider: str,
        model: str,
        working_directory: str | Path | None,
        state: RunState,
    ) -> RunRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        now = _now()
        run_id = _new_run_id()
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=False)
        record = RunRecord(
            run_id=run_id,
            state=state,
            title=_clean_title(title),
            kind=kind,
            provider=provider,
            model=model,
            working_directory=str(Path(working_directory).expanduser()) if working_directory is not None else "",
            created_at=now,
            updated_at=now,
            path=path,
        )
        _write_json(path / "meta.json", _record_to_json(record))
        (path / "events.jsonl").touch()
        _write_text(path / "summary.md", "")
        _write_text(path / "verification.md", "")
        _write_text(path / "diff.patch", "")
        return record

    def _append_event_sync(self, run_id: str, event_type: str, data: dict[str, Any]) -> RunEvent:
        record = self._load_run_or_raise(run_id)
        events_path = record.path / "events.jsonl"
        event_id = _next_event_id(events_path)
        event = RunEvent(event_id=event_id, timestamp=_now(), type=event_type, data=data)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_event_to_json(event), sort_keys=True, default=str) + "\n")
        self._update_state_sync(run_id, record.state)
        return event

    def _update_state_sync(self, run_id: str, state: RunState) -> RunRecord:
        record = self._load_run_or_raise(run_id)
        updated = RunRecord(
            run_id=record.run_id,
            state=state,
            title=record.title,
            kind=record.kind,
            provider=record.provider,
            model=record.model,
            working_directory=record.working_directory,
            created_at=record.created_at,
            updated_at=_now(),
            path=record.path,
        )
        _write_json(record.path / "meta.json", _record_to_json(updated))
        return updated

    def _finish_run_sync(
        self,
        run_id: str,
        state: RunState,
        summary: str,
        verification: str,
        diff: str,
    ) -> RunRecord:
        record = self._update_state_sync(run_id, state)
        _write_text(record.path / "summary.md", summary)
        _write_text(record.path / "verification.md", verification or f"Run finished with state: {state}\n")
        _write_text(record.path / "diff.patch", diff)
        return record

    def _list_runs_sync(self, limit: int) -> list[RunRecord]:
        if not self.root.exists():
            return []
        records = [
            record
            for path in self.root.iterdir()
            if path.is_dir()
            for record in [_load_record(path)]
            if record is not None
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records[: max(1, limit)]

    def _load_run_sync(self, run_id: str) -> RunRecord | None:
        if not _safe_run_id(run_id):
            return None
        return _load_record(self.root / run_id)

    def _load_events_sync(self, run_id: str) -> list[RunEvent]:
        record = self._load_run_or_raise(run_id)
        events: list[RunEvent] = []
        events_path = record.path / "events.jsonl"
        if not events_path.exists():
            return events
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(_event_from_json(payload))
        return events

    def _load_run_or_raise(self, run_id: str) -> RunRecord:
        record = self._load_run_sync(run_id)
        if record is None:
            raise ValueError(f"Unknown run: {run_id}")
        return record


def default_runs_path() -> Path:
    return Path.home() / ".libre-claw" / "runs"


def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{uuid4().hex[:8]}"


def _safe_run_id(run_id: str) -> bool:
    if not run_id or run_id in {".", ".."}:
        return False
    path = Path(run_id)
    return not path.is_absolute() and path.name == run_id and "/" not in run_id and "\\" not in run_id


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        return "Untitled run"
    return cleaned[:120]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _record_to_json(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "state": record.state,
        "title": record.title,
        "kind": record.kind,
        "provider": record.provider,
        "model": record.model,
        "working_directory": record.working_directory,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "path": str(record.path),
    }


def _load_record(path: Path) -> RunRecord | None:
    meta_path = path / "meta.json"
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    state = payload.get("state", "failed")
    if state not in {"queued", "running", "blocked", "done", "failed", "cancelled"}:
        state = "failed"
    return RunRecord(
        run_id=str(payload.get("run_id", path.name)),
        state=state,
        title=str(payload.get("title", "Untitled run")),
        kind=str(payload.get("kind", "chat")),
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        working_directory=str(payload.get("working_directory", "")),
        created_at=str(payload.get("created_at", "")),
        updated_at=str(payload.get("updated_at", "")),
        path=path,
    )


def _next_event_id(path: Path) -> int:
    if not path.exists():
        return 1
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count + 1


def _event_to_json(event: RunEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "type": event.type,
        "data": event.data,
    }


def _event_from_json(payload: dict[str, Any]) -> RunEvent:
    return RunEvent(
        event_id=int(payload.get("event_id", 0)),
        timestamp=str(payload.get("timestamp", "")),
        type=str(payload.get("type", "")),
        data=dict(payload.get("data", {})),
    )
