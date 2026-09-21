# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import copy
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import uuid4

from libre_claw.core.session import Session, session_from_payload, session_to_payload


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
        plan: str = "",
        summary: str = "",
        verification: str = "",
        diff: str = "",
        browser: str = "",
    ) -> RunRecord:
        async with self._lock:
            return await asyncio.to_thread(self._finish_run_sync, run_id, state, plan, summary, verification, diff, browser)

    async def list_runs(self, limit: int = 20) -> list[RunRecord]:
        return await asyncio.to_thread(self._list_runs_sync, limit)

    async def load_run(self, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._load_run_sync, run_id)

    async def load_events(self, run_id: str) -> list[RunEvent]:
        return await asyncio.to_thread(self._load_events_sync, run_id)

    async def save_session(self, run_id: str, session: Session) -> None:
        payload = copy.deepcopy(session_to_payload(session))
        async with self._lock:
            record = await asyncio.to_thread(self._load_run_or_raise, run_id)
            await asyncio.to_thread(_write_json, record.path / "session.json", payload)

    async def load_session(self, run_id: str, *, recover: bool = False) -> Session:
        record = await asyncio.to_thread(self._load_run_or_raise, run_id)
        try:
            text = await asyncio.to_thread((record.path / "session.json").read_text, encoding="utf-8")
            session = session_from_payload(json.loads(text))
        except (OSError, ValueError):
            session = Session()
            chunks: list[str] = []
            for event in await self.load_events(run_id):
                if event.type == "user_message":
                    if chunks:
                        session.add_assistant_message("".join(chunks))
                        chunks.clear()
                    session.add_user_message(str(event.data.get("content", "")))
                elif event.type == "assistant_delta":
                    chunks.append(str(event.data.get("text", "")))
            if chunks:
                session.add_assistant_message("".join(chunks))
        if recover:
            session.recover_interrupted_tools()
        return session

    async def set_workspace(self, run_id: str, working_directory: Path) -> RunRecord:
        async with self._lock:
            record = await asyncio.to_thread(self._load_run_or_raise, run_id)
            payload = _record_to_json(record)
            payload["working_directory"] = str(working_directory.resolve())
            await asyncio.to_thread(_write_json, record.path / "meta.json", payload)
            return await asyncio.to_thread(self._load_run_or_raise, run_id)

    async def set_runtime(self, run_id: str, *, provider: str, model: str) -> RunRecord:
        async with self._lock:
            record = await asyncio.to_thread(self._load_run_or_raise, run_id)
            payload = _record_to_json(record)
            payload.update(provider=provider, model=model)
            await asyncio.to_thread(_write_json, record.path / "meta.json", payload)
            return await asyncio.to_thread(self._load_run_or_raise, run_id)

    async def queue_message(self, run_id: str, message: str) -> dict[str, Any]:
        if not message.strip() or len(message) > 100_000:
            raise ValueError("Queued messages must contain between 1 and 100,000 characters.")
        async with self._lock:
            return await _settled_io(self._queue_message_sync, run_id, message)

    async def queued_messages(self, run_id: str) -> list[dict[str, Any]]:
        record = await asyncio.to_thread(self._load_run_or_raise, run_id)
        return await asyncio.to_thread(_read_queue, record.path)

    async def take_queued_message(self, run_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return await _settled_io(self._take_queued_message_sync, run_id)

    async def release_queued_message(
        self, run_id: str, item: dict[str, Any] | None, *, cancelled: bool = False,
    ) -> None:
        """Return explicitly unstarted work after a claim; never replay an executed turn."""
        async with self._lock:
            await _settled_io(self._release_queued_message_sync, run_id, item, cancelled)

    async def finish_turn(
        self, run_id: str, state: RunState, *, plan: str = "", summary: str = "",
        verification: str = "", diff: str = "", browser: str = "",
        drain_queue: bool = True, hold_final_state: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically choose a queued turn or publish completion after the session was saved."""
        async with self._lock:
            return await _settled_io(
                self._finish_turn_sync, run_id, state, plan, summary, verification, diff, browser,
                drain_queue, hold_final_state,
            )

    def _queue_message_sync(self, run_id: str, message: str) -> dict[str, Any]:
        record = self._load_run_or_raise(run_id)
        with _queue_file_lock(record.path):
            items = _read_queue(record.path)
            if len(items) >= 100:
                raise ValueError("This task already has 100 queued messages.")
            item = {"id": uuid4().hex, "message": message.strip(), "created_at": _now()}
            items.append(item)
            _write_json(record.path / "queue.json", {"messages": items})
            return item

    def _take_queued_message_sync(self, run_id: str) -> dict[str, Any] | None:
        record = self._load_run_or_raise(run_id)
        with _queue_file_lock(record.path):
            return self._claim_queued_message_sync(record)

    def _claim_queued_message_sync(self, record: RunRecord) -> dict[str, Any] | None:
        items = _read_queue(record.path)
        if not items:
            return None
        item = items.pop(0)
        # A crash after this journal entry must not replay an unknown side effect.
        self._append_event_sync(record.run_id, "queued_message_started", item)
        _write_json(record.path / "queue.json", {"messages": items})
        return item

    def _release_queued_message_sync(self, run_id: str, item: dict[str, Any] | None, cancelled: bool) -> None:
        record = self._load_run_or_raise(run_id)
        with _queue_file_lock(record.path):
            if item is not None:
                events = self._load_events_sync(run_id)
                claim = next((event.data for event in reversed(events)
                              if event.type == "queued_message_started" and event.data.get("id") == item.get("id")), None)
                if claim is None or claim != item:
                    raise ValueError("Only a recorded, unstarted queue claim can be returned.")
                items = _read_queue(record.path)
                if not any(pending.get("id") == item["id"] for pending in items):
                    # The journal also restores this item if the process exits
                    # before the queue snapshot below reaches disk.
                    self._append_event_sync(run_id, "queued_message_returned", item)
                    _write_json(record.path / "queue.json", {"messages": sorted([item, *items], key=lambda entry: entry.get("created_at", ""))})
            if cancelled and record.state != "cancelled":
                self._append_event_sync(run_id, "run_finished", {"state": "cancelled", "reason": "Stopped before the next turn started."})
                self._update_state_sync(run_id, "cancelled")

    def _finish_turn_sync(
        self, run_id: str, state: RunState, plan: str, summary: str, verification: str,
        diff: str, browser: str, drain_queue: bool, hold_final_state: bool,
    ) -> dict[str, Any] | None:
        record = self._load_run_or_raise(run_id)
        with _queue_file_lock(record.path):
            queued = self._claim_queued_message_sync(record) if state == "done" and drain_queue and not hold_final_state else None
            self._append_event_sync(run_id, "turn_finished", {"state": state, "queued_message_id": queued["id"] if queued else None})
            if queued is None and not hold_final_state:
                self._append_event_sync(run_id, "run_finished", {"state": state})
            final_state: RunState = "running" if queued is not None or hold_final_state else state
            self._finish_run_sync(run_id, final_state, plan, summary, verification, diff, browser)
            return queued

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
        _write_text(path / "plan.md", "")
        _write_text(path / "summary.md", "")
        _write_text(path / "verification.md", "")
        _write_text(path / "diff.patch", "")
        _write_text(path / "browser.md", "")
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
        plan: str,
        summary: str,
        verification: str,
        diff: str,
        browser: str,
    ) -> RunRecord:
        record = self._load_run_or_raise(run_id)
        _write_text(record.path / "plan.md", plan)
        _write_text(record.path / "summary.md", summary)
        _write_text(record.path / "verification.md", verification or f"Run finished with state: {state}\n")
        _write_text(record.path / "diff.patch", diff)
        _write_text(record.path / "browser.md", browser)
        return self._update_state_sync(run_id, state)

    def _list_runs_sync(self, limit: int) -> list[RunRecord]:
        if not self.root.exists():
            return []
        limit = max(1, limit)
        # meta.json is rewritten on every state update, so its mtime tracks
        # updated_at. Stat every run dir (cheap) but only parse meta.json for
        # the freshest candidates — parsing thousands of metas made listing
        # take over a minute on large stores.
        candidates: list[tuple[float, Path]] = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            try:
                mtime = (path / "meta.json").stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, path))
        candidates.sort(key=lambda item: item[0], reverse=True)
        records = [
            record
            for _, path in candidates[: limit + 25]
            for record in [_load_record(path)]
            if record is not None
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records[:limit]

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


async def _settled_io(function: Any, *arguments: Any) -> Any:
    operation = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise


async def settle_finalization(task: asyncio.Task[Any]) -> tuple[Any, bool]:
    """Finish durable cleanup despite cancellation and report whether its caller stopped."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


@contextmanager
def _queue_file_lock(path: Path) -> Iterator[None]:
    """Coordinate queue claims across the TUI, daemon, and CLI RunStore instances."""
    with (path / "queue.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_queue(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((path / "queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    started: set[str] = set()
    returned: dict[str, dict[str, Any]] = {}
    try:
        for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                data = event.get("data", {})
                item_id = str(data.get("id", ""))
                if event.get("type") == "queued_message_started":
                    started.add(item_id)
                    returned.pop(item_id, None)
                elif event.get("type") == "queued_message_returned" and item_id and isinstance(data.get("message"), str):
                    started.discard(item_id)
                    returned[item_id] = data
            except (ValueError, AttributeError):
                continue
    except OSError:
        pass
    items = [item for item in payload.get("messages", []) if isinstance(item, dict) and isinstance(item.get("message"), str) and item.get("id") not in started]
    missing = [item for key, item in returned.items() if not any(pending.get("id") == key for pending in items)]
    return sorted([*missing, *items], key=lambda item: item.get("created_at", "")) if missing else items


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
