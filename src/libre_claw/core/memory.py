# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from libre_claw.core.session import ChatMessage, Session
from libre_claw.core.cordis_bindings import provider_stream
from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.core.session import text_block
from libre_claw.core.runs import settle_finalization
from libre_claw.providers.base import LLMProvider, ProviderError, TextDelta


@dataclass(frozen=True)
class MemoryFact:
    id: int
    fact: str
    created_at: str


@dataclass(frozen=True)
class StoredSession:
    id: int
    name: str
    summary: str
    messages: list[ChatMessage]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FileEditLog:
    id: int
    path: str
    tool_name: str
    before: str
    after: str
    created_at: str


@dataclass(frozen=True)
class MemoryItem:
    id: int
    kind: str
    scope: str
    text: str
    source_type: str
    source_id: str
    project_root: str
    created_at: str
    updated_at: str
    disabled_at: str | None = None


@dataclass(frozen=True)
class SessionArchiveEvent:
    event_id: int
    timestamp: str
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    scope: str
    text: str


@asynccontextmanager
async def _memory_connection(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Retain ownership of threaded SQLite acquisition and close during cancellation."""
    async def connect() -> aiosqlite.Connection:
        return await aiosqlite.connect(path)

    db: aiosqlite.Connection | None = None
    completed = False
    try:
        # Cancelling aiosqlite's acquisition future can discard a connection
        # created later by its worker. Join acquisition so it can be closed.
        db, cancelled = await settle_finalization(asyncio.create_task(connect()))
        if cancelled:
            raise asyncio.CancelledError
        yield db
        completed = True
    finally:
        if db is not None:
            _, cancelled = await settle_finalization(asyncio.create_task(db.close()))
            # Keep an existing body/acquisition exception; cancellation during
            # otherwise successful cleanup must still reach the caller.
            if cancelled and completed:
                raise asyncio.CancelledError


class MemoryStore:
    """SQLite-backed persistent memory for facts, sessions, and file edits."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_memory_path()
        self.session_root = default_sessions_path()
        self._archive_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with _memory_connection(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL DEFAULT '',
                    messages_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    project_root TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    disabled_at TEXT,
                    UNIQUE(source_type, source_id, text)
                )
                """
            )
            await _ensure_memory_fts(db)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS file_edits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    before TEXT NOT NULL,
                    after TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await _migrate_facts_to_memory_items(db)
            await db.commit()

    async def add_fact(self, fact: str) -> MemoryFact:
        await self.initialize()
        created_at = _now_seconds()
        redacted = redact_secrets(fact)
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO facts (fact, created_at) VALUES (?, ?)",
                (redacted, created_at),
            )
            fact_id = int(cursor.lastrowid)
            await _upsert_memory_item(
                db,
                kind="fact",
                scope="global",
                text=redacted,
                source_type="manual",
                source_id=f"fact:{fact_id}",
                project_root="",
            )
            await db.commit()
            return MemoryFact(id=fact_id, fact=redacted, created_at=created_at)

    async def list_facts(self) -> list[MemoryFact]:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute("SELECT id, fact, created_at FROM facts ORDER BY id")
            rows = await cursor.fetchall()
        return [MemoryFact(id=row[0], fact=row[1], created_at=row[2]) for row in rows]

    async def forget_fact(self, fact_id: int) -> bool:
        await self.initialize()
        now = _now_seconds()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            await db.execute(
                """
                UPDATE memory_items
                SET disabled_at = COALESCE(disabled_at, ?), updated_at = ?
                WHERE source_type = 'manual' AND source_id = ?
                """,
                (now, now, f"fact:{fact_id}"),
            )
            await _rebuild_memory_fts(db)
            await db.commit()
            return cursor.rowcount > 0

    async def add_memory_item(
        self,
        *,
        text: str,
        kind: str = "fact",
        scope: str = "global",
        source_type: str = "manual",
        source_id: str = "",
        project_root: str | Path = "",
    ) -> MemoryItem:
        await self.initialize()
        cleaned = redact_secrets(text).strip()
        if not cleaned:
            raise ValueError("Memory text cannot be empty.")
        source = source_id or f"manual:{uuid4().hex}"
        root = _project_root_text(project_root)
        async with _memory_connection(self.path) as db:
            item_id = await _upsert_memory_item(
                db,
                kind=_clean_label(kind, default="fact"),
                scope=_clean_label(scope, default="global"),
                text=cleaned,
                source_type=_clean_label(source_type, default="manual"),
                source_id=source,
                project_root=root,
            )
            await db.commit()
        item = await self.get_memory_item(item_id)
        if item is None:
            raise RuntimeError("Memory item was not saved.")
        return item

    async def list_memory_items(self, *, include_disabled: bool = False, limit: int = 50) -> list[MemoryItem]:
        await self.initialize()
        where = "" if include_disabled else "WHERE disabled_at IS NULL"
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                f"""
                SELECT id, kind, scope, text, source_type, source_id, project_root, created_at, updated_at, disabled_at
                FROM memory_items
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            rows = await cursor.fetchall()
        return [_memory_item_from_row(row) for row in rows]

    async def list_always_injected_memories(self, *, limit: int = 100) -> list[str]:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                """
                SELECT text
                FROM memory_items
                WHERE source_type = 'manual'
                  AND disabled_at IS NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

    async def get_memory_item(self, item_id: int) -> MemoryItem | None:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                """
                SELECT id, kind, scope, text, source_type, source_id, project_root, created_at, updated_at, disabled_at
                FROM memory_items
                WHERE id = ?
                """,
                (item_id,),
            )
            row = await cursor.fetchone()
        return _memory_item_from_row(row) if row is not None else None

    async def search_memory_items(
        self,
        query: str,
        *,
        project_root: str | Path = "",
        limit: int = 8,
        include_disabled: bool = False,
    ) -> list[MemoryItem]:
        await self.initialize()
        cleaned = redact_secrets(query).strip()
        root = _project_root_text(project_root)
        if not cleaned:
            return await self.list_memory_items(include_disabled=include_disabled, limit=limit)
        async with _memory_connection(self.path) as db:
            rows = await _search_memory_fts(
                db,
                cleaned,
                project_root=root,
                limit=max(1, limit),
                include_disabled=include_disabled,
            )
            if not rows:
                rows = await _search_memory_like(
                    db,
                    cleaned,
                    project_root=root,
                    limit=max(1, limit),
                    include_disabled=include_disabled,
                )
        return [_memory_item_from_row(row) for row in rows]

    async def forget_memory_item(self, item_id: int) -> bool:
        await self.initialize()
        now = _now_seconds()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                """
                UPDATE memory_items
                SET disabled_at = COALESCE(disabled_at, ?), updated_at = ?
                WHERE id = ?
                """,
                (now, now, item_id),
            )
            await _rebuild_memory_fts(db)
            await db.commit()
            return cursor.rowcount > 0

    async def memory_status(self) -> dict[str, int]:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            active = await db.execute_fetchall("SELECT COUNT(*) FROM memory_items WHERE disabled_at IS NULL")
            disabled = await db.execute_fetchall("SELECT COUNT(*) FROM memory_items WHERE disabled_at IS NOT NULL")
            sessions = await asyncio.to_thread(_count_session_archives, self.session_root)
        return {
            "active": int(active[0][0]) if active else 0,
            "disabled": int(disabled[0][0]) if disabled else 0,
            "session_archives": sessions,
        }

    async def append_session_event(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> SessionArchiveEvent:
        redacted = _redact_payload(data or {})
        async with self._archive_lock:
            return await asyncio.to_thread(self._append_session_event_sync, session_id, event_type, redacted)

    async def load_session_events(self, session_id: str) -> list[SessionArchiveEvent]:
        return await asyncio.to_thread(self._load_session_events_sync, session_id)

    def _append_session_event_sync(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> SessionArchiveEvent:
        safe_id = safe_session_id(session_id)
        path = self.session_root / safe_id
        path.mkdir(parents=True, exist_ok=True)
        events_path = path / "events.jsonl"
        event_id = _next_archive_event_id(events_path)
        event = SessionArchiveEvent(event_id=event_id, timestamp=_now_microseconds(), type=event_type, data=data)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_archive_event_to_json(event), sort_keys=True, default=str) + "\n")
        meta_path = path / "meta.json"
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps({"session_id": safe_id, "created_at": event.timestamp}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return event

    def _load_session_events_sync(self, session_id: str) -> list[SessionArchiveEvent]:
        events_path = self.session_root / safe_session_id(session_id) / "events.jsonl"
        if not events_path.exists():
            return []
        events: list[SessionArchiveEvent] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(
                SessionArchiveEvent(
                    event_id=int(payload.get("event_id", 0) or 0),
                    timestamp=str(payload.get("timestamp", "")),
                    type=str(payload.get("type", "")),
                    data=dict(payload.get("data", {})) if isinstance(payload.get("data"), dict) else {},
                )
            )
        return events

    async def save_session(self, name: str, session: Session, summary: str = "") -> StoredSession:
        await self.initialize()
        now = _now_seconds()
        summary_text = summary or session.summary or ""
        messages_json = json.dumps([message.as_provider_dict() for message in session.messages])
        async with _memory_connection(self.path) as db:
            await db.execute(
                """
                INSERT INTO sessions (name, summary, messages_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    summary = excluded.summary,
                    messages_json = excluded.messages_json,
                    updated_at = excluded.updated_at
                """,
                (name, summary_text, messages_json, now, now),
            )
            await db.commit()
        stored = await self.load_session(name)
        if stored is None:
            msg = f"Session was not saved: {name}"
            raise RuntimeError(msg)
        return stored

    async def load_session(self, name: str) -> StoredSession | None:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                "SELECT id, name, summary, messages_json, created_at, updated_at FROM sessions WHERE name = ?",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        messages = _messages_from_json(row[3])
        return StoredSession(
            id=row[0],
            name=row[1],
            summary=row[2],
            messages=messages,
            created_at=row[4],
            updated_at=row[5],
        )

    async def list_sessions(self) -> list[StoredSession]:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                "SELECT id, name, summary, messages_json, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()
        return [
            StoredSession(
                id=row[0],
                name=row[1],
                summary=row[2],
                messages=_messages_from_json(row[3]),
                created_at=row[4],
                updated_at=row[5],
            )
            for row in rows
        ]

    async def log_file_edit(self, path: str, tool_name: str, before: str, after: str) -> FileEditLog:
        await self.initialize()
        created_at = _now_seconds()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                """
                INSERT INTO file_edits (path, tool_name, before, after, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (path, tool_name, before, after, created_at),
            )
            await db.commit()
            return FileEditLog(
                id=int(cursor.lastrowid),
                path=path,
                tool_name=tool_name,
                before=before,
                after=after,
                created_at=created_at,
            )

    async def list_file_edits(self) -> list[FileEditLog]:
        await self.initialize()
        async with _memory_connection(self.path) as db:
            cursor = await db.execute(
                "SELECT id, path, tool_name, before, after, created_at FROM file_edits ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [
            FileEditLog(id=row[0], path=row[1], tool_name=row[2], before=row[3], after=row[4], created_at=row[5])
            for row in rows
        ]


def default_memory_path() -> Path:
    return Path.home() / ".libre-claw" / "memory.db"


def default_sessions_path() -> Path:
    return Path.home() / ".libre-claw" / "sessions"


def new_session_archive_id(surface: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return safe_session_id(f"{surface}-{stamp}-{uuid4().hex[:8]}")


def safe_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id.strip())
    cleaned = cleaned.strip(".-")
    return cleaned[:120] or f"session-{uuid4().hex[:8]}"


SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:sk|rk|pk|ghp|gho|ghu|ghs|ghr|xoxb|xoxp|xoxa|xoxr)-[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(_redaction_replacement, redacted)
    return redacted


def summarize_session_for_memory(session: Session, *, limit: int = 4000) -> str:
    parts: list[str] = []
    if session.summary:
        parts.append(session.summary)
    for message in session.messages[-12:]:
        text = _message_text(message)
        if text:
            parts.append(f"{message.role}: {text}")
    summary = "\n".join(parts).strip()
    return redact_secrets(summary[-limit:] if len(summary) > limit else summary)


async def extract_memories_with_provider(
    provider: LLMProvider,
    *,
    user_message: str,
    assistant_text: str,
    existing_memories: Sequence[str] = (),
    max_tokens: int = 1024,
    timeout_seconds: float = 30.0,
    engine: CordisEngine | None = None,
) -> list[ExtractedMemory]:
    async def extract() -> list[ExtractedMemory]:
        return await _extract_memories_with_provider(
            provider, user_message=user_message, assistant_text=assistant_text,
            existing_memories=existing_memories, max_tokens=max_tokens, engine=engine,
        )

    try:
        async with asyncio.timeout(timeout_seconds):
            return await engine.call("memory", "extract", handler=extract) if engine is not None else await extract()
    except TimeoutError:
        return []


async def _extract_memories_with_provider(
    provider: LLMProvider,
    *,
    user_message: str,
    assistant_text: str,
    existing_memories: Sequence[str],
    max_tokens: int,
    engine: CordisEngine | None = None,
) -> list[ExtractedMemory]:
    prompt = _memory_extraction_prompt(
        user_message=redact_secrets(user_message),
        assistant_text=redact_secrets(assistant_text),
        existing_memories=tuple(redact_secrets(memory) for memory in existing_memories),
    )
    chunks: list[str] = []
    async with provider_stream(
        provider, engine=engine,
        messages=[ChatMessage(role="user", content=[text_block(prompt)])],
        tools=[],
        system=MEMORY_EXTRACTION_SYSTEM_PROMPT,
        stream=True,
        temperature=0.0,
        max_tokens=max_tokens,
    ) as stream:
        async for event in stream:
            if isinstance(event, TextDelta):
                chunks.append(event.text)
                continue
            if isinstance(event, ProviderError):
                return []
    return parse_extracted_memories("".join(chunks))


def parse_extracted_memories(raw: str) -> list[ExtractedMemory]:
    text = raw.strip()
    if not text:
        return []
    payload_text = _json_payload_text(text)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        raw_items = payload.get("memories", [])
    else:
        raw_items = payload
    if not isinstance(raw_items, list):
        return []
    memories: list[ExtractedMemory] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, str):
            kind = "fact"
            scope = "global"
            memory_text = item
        elif isinstance(item, dict):
            kind = str(item.get("kind", "fact"))
            scope = str(item.get("scope", "global"))
            memory_text = str(item.get("text", ""))
        else:
            continue
        cleaned = redact_secrets(memory_text).strip()
        if not cleaned or cleaned.lower() in {"none", "n/a", "no durable memory"}:
            continue
        if _looks_secret(cleaned):
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        memories.append(
            ExtractedMemory(
                kind=_clean_label(kind, default="fact"),
                scope=_clean_label(scope, default="global"),
                text=cleaned[:1200],
            )
        )
    return memories[:8]


MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "You extract durable memory for Libre Claw. Return only JSON. "
    "Do not include secrets, credentials, tokens, transient chatter, or one-off tool output."
)


def _memory_extraction_prompt(
    *,
    user_message: str,
    assistant_text: str,
    existing_memories: Sequence[str],
) -> str:
    existing = "\n".join(f"- {memory}" for memory in existing_memories[:20]) or "- none"
    return (
        "Extract durable memories from this turn. Keep only stable facts, preferences, "
        "project decisions, recurring workflows, and useful summaries. "
        "Return JSON exactly like: "
        '{"memories":[{"kind":"preference|project|workflow|decision|summary|fact",'
        '"scope":"global|project|session","text":"..."}]}\n\n'
        f"Existing memories:\n{existing}\n\n"
        f"User message:\n{user_message[:4000]}\n\n"
        f"Assistant response:\n{assistant_text[:8000]}\n"
    )


def _json_payload_text(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    first_array = text.find("[")
    first_object = text.find("{")
    starts = [index for index in (first_array, first_object) if index >= 0]
    if not starts:
        return text
    start = min(starts)
    end_char = "]" if text[start] == "[" else "}"
    end = text.rfind(end_char)
    if end > start:
        return text[start : end + 1]
    return text


def _messages_from_json(raw: str) -> list[ChatMessage]:
    parsed = json.loads(raw)
    messages: list[ChatMessage] = []
    for item in parsed:
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), list):
            messages.append(ChatMessage(role=item["role"], content=item["content"]))
    return messages


def _message_text(message: ChatMessage) -> str:
    chunks: list[str] = []
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text", "")))
    return "\n".join(chunks).strip()


def _now_seconds() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_microseconds() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _memory_item_from_row(row: Any) -> MemoryItem:
    return MemoryItem(
        id=int(row[0]),
        kind=str(row[1]),
        scope=str(row[2]),
        text=str(row[3]),
        source_type=str(row[4]),
        source_id=str(row[5]),
        project_root=str(row[6]),
        created_at=str(row[7]),
        updated_at=str(row[8]),
        disabled_at=str(row[9]) if row[9] is not None else None,
    )


async def _ensure_memory_fts(db: aiosqlite.Connection) -> None:
    try:
        await db.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
            USING fts5(item_id UNINDEXED, text, kind, scope, project_root)
            """
        )
    except aiosqlite.Error:
        return


async def _migrate_facts_to_memory_items(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT id, fact, created_at FROM facts ORDER BY id")
    rows = await cursor.fetchall()
    for fact_id, fact, created_at in rows:
        source_id = f"fact:{fact_id}"
        existing = await db.execute_fetchall(
            "SELECT id FROM memory_items WHERE source_type = 'manual' AND source_id = ?",
            (source_id,),
        )
        if existing:
            continue
        await _upsert_memory_item(
            db,
            kind="fact",
            scope="global",
            text=redact_secrets(str(fact)),
            source_type="manual",
            source_id=source_id,
            project_root="",
            created_at=str(created_at),
        )


async def _upsert_memory_item(
    db: aiosqlite.Connection,
    *,
    kind: str,
    scope: str,
    text: str,
    source_type: str,
    source_id: str,
    project_root: str,
    created_at: str | None = None,
) -> int:
    now = _now_seconds()
    created = created_at or now
    await db.execute(
        """
        INSERT INTO memory_items (kind, scope, text, source_type, source_id, project_root, created_at, updated_at, disabled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(source_type, source_id, text) DO UPDATE SET
            kind = excluded.kind,
            scope = excluded.scope,
            project_root = excluded.project_root,
            updated_at = excluded.updated_at,
            disabled_at = NULL
        """,
        (kind, scope, text, source_type, source_id, project_root, created, now),
    )
    row = await db.execute_fetchall(
        """
        SELECT id FROM memory_items
        WHERE source_type = ? AND source_id = ? AND text = ?
        """,
        (source_type, source_id, text),
    )
    item_id = int(row[0][0])
    await _replace_memory_fts_row(db, item_id, text, kind, scope, project_root)
    return item_id


async def _replace_memory_fts_row(
    db: aiosqlite.Connection,
    item_id: int,
    text: str,
    kind: str,
    scope: str,
    project_root: str,
) -> None:
    try:
        await db.execute("DELETE FROM memory_items_fts WHERE item_id = ?", (item_id,))
        await db.execute(
            "INSERT INTO memory_items_fts (item_id, text, kind, scope, project_root) VALUES (?, ?, ?, ?, ?)",
            (item_id, text, kind, scope, project_root),
        )
    except aiosqlite.Error:
        return


async def _rebuild_memory_fts(db: aiosqlite.Connection) -> None:
    try:
        await db.execute("DELETE FROM memory_items_fts")
        cursor = await db.execute(
            "SELECT id, text, kind, scope, project_root FROM memory_items WHERE disabled_at IS NULL"
        )
        rows = await cursor.fetchall()
        for item_id, text, kind, scope, project_root in rows:
            await db.execute(
                "INSERT INTO memory_items_fts (item_id, text, kind, scope, project_root) VALUES (?, ?, ?, ?, ?)",
                (item_id, text, kind, scope, project_root),
            )
    except aiosqlite.Error:
        return


async def _search_memory_fts(
    db: aiosqlite.Connection,
    query: str,
    *,
    project_root: str,
    limit: int,
    include_disabled: bool,
) -> list[Any]:
    match_query = _fts_query(query)
    if not match_query:
        return []
    disabled_clause = "" if include_disabled else "AND memory_items.disabled_at IS NULL"
    try:
        cursor = await db.execute(
            f"""
            SELECT memory_items.id, memory_items.kind, memory_items.scope, memory_items.text,
                   memory_items.source_type, memory_items.source_id, memory_items.project_root,
                   memory_items.created_at, memory_items.updated_at, memory_items.disabled_at
            FROM memory_items_fts
            JOIN memory_items ON memory_items.id = memory_items_fts.item_id
            WHERE memory_items_fts MATCH ?
              AND (memory_items.project_root = '' OR memory_items.project_root = ?)
              {disabled_clause}
            ORDER BY CASE WHEN memory_items.project_root = ? THEN 0 ELSE 1 END,
                     bm25(memory_items_fts),
                     memory_items.updated_at DESC
            LIMIT ?
            """,
            (match_query, project_root, project_root, limit),
        )
        return await cursor.fetchall()
    except aiosqlite.Error:
        return []


async def _search_memory_like(
    db: aiosqlite.Connection,
    query: str,
    *,
    project_root: str,
    limit: int,
    include_disabled: bool,
) -> list[Any]:
    terms = _query_terms(query)
    disabled_clause = "" if include_disabled else "AND disabled_at IS NULL"
    if not terms:
        return []
    clauses = " AND ".join("LOWER(text) LIKE ?" for _ in terms)
    values = [f"%{term.lower()}%" for term in terms]
    cursor = await db.execute(
        f"""
        SELECT id, kind, scope, text, source_type, source_id, project_root, created_at, updated_at, disabled_at
        FROM memory_items
        WHERE {clauses}
          AND (project_root = '' OR project_root = ?)
          {disabled_clause}
        ORDER BY CASE WHEN project_root = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT ?
        """,
        (*values, project_root, project_root, limit),
    )
    return await cursor.fetchall()


def _fts_query(query: str) -> str:
    terms = _query_terms(query)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z0-9_./:-]{3,}", query) if not _looks_secret(term)]


def _looks_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _redaction_replacement(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 1:
        return f"{match.group(1)}=<redacted>"
    return "<redacted-secret>"


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _archive_event_to_json(event: SessionArchiveEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "type": event.type,
        "data": event.data,
    }


def _next_archive_event_id(events_path: Path) -> int:
    if not events_path.exists():
        return 1
    last_id = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        last_id = max(last_id, int(payload.get("event_id", 0) or 0))
    return last_id + 1


def _project_root_text(project_root: str | Path) -> str:
    if not project_root:
        return ""
    return str(Path(project_root).expanduser().resolve())


def _clean_label(value: str, *, default: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:80] or default


def _count_session_archives(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.iterdir() if path.is_dir() and (path / "events.jsonl").exists())
