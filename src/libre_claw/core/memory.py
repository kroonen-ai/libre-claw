# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from libre_claw.core.session import ChatMessage, Session


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


class MemoryStore:
    """SQLite-backed persistent memory for facts, sessions, and file edits."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_memory_path()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
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
            await db.commit()

    async def add_fact(self, fact: str) -> MemoryFact:
        await self.initialize()
        created_at = _now()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO facts (fact, created_at) VALUES (?, ?)",
                (fact, created_at),
            )
            await db.commit()
            return MemoryFact(id=int(cursor.lastrowid), fact=fact, created_at=created_at)

    async def list_facts(self) -> list[MemoryFact]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT id, fact, created_at FROM facts ORDER BY id")
            rows = await cursor.fetchall()
        return [MemoryFact(id=row[0], fact=row[1], created_at=row[2]) for row in rows]

    async def forget_fact(self, fact_id: int) -> bool:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def save_session(self, name: str, session: Session, summary: str = "") -> StoredSession:
        await self.initialize()
        now = _now()
        summary_text = summary or session.summary or ""
        messages_json = json.dumps([message.as_provider_dict() for message in session.messages])
        async with aiosqlite.connect(self.path) as db:
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
        async with aiosqlite.connect(self.path) as db:
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
        async with aiosqlite.connect(self.path) as db:
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
        created_at = _now()
        async with aiosqlite.connect(self.path) as db:
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
        async with aiosqlite.connect(self.path) as db:
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


def _messages_from_json(raw: str) -> list[ChatMessage]:
    parsed = json.loads(raw)
    messages: list[ChatMessage] = []
    for item in parsed:
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), list):
            messages.append(ChatMessage(role=item["role"], content=item["content"]))
    return messages


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
