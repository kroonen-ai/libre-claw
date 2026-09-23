# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from libre_claw.core import memory


class DatabaseLifetime:
    """Control real SQLite worker timing without replacing database operations."""

    def __init__(self, monkeypatch, *, pause_connect=False, pause_close=False, fail_connect=False):
        self.opened = threading.Event()
        self.closing = threading.Event()
        self.release_connect = threading.Event()
        self.release_close = threading.Event()
        self.connections = []
        self.failure = sqlite3.OperationalError("controlled connection failure")
        owner = self

        class Connection(sqlite3.Connection):
            explicitly_closed = False

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                owner.connections.append(self)
                owner.opened.set()
                if pause_connect and not owner.release_connect.wait(5):
                    raise RuntimeError("Test did not release SQLite acquisition")

            def close(self):
                owner.closing.set()
                if pause_close and not owner.release_close.wait(5):
                    raise RuntimeError("Test did not release SQLite close")
                super().close()
                self.explicitly_closed = True

        original = memory.aiosqlite.connect
        if fail_connect:
            def connector(*args, **kwargs):
                owner.opened.set()
                if not owner.release_connect.wait(5):
                    raise RuntimeError("Test did not release failing SQLite connector")
                raise owner.failure
            monkeypatch.setattr(sqlite3, "connect", connector)
        else:
            # Cross-thread access is enabled only so a failing regression can
            # explicitly release its own controlled handles in finally.
            monkeypatch.setattr(memory.aiosqlite, "connect", lambda path: original(
                path, factory=Connection, check_same_thread=False,
            ))

    async def entered(self, event):
        assert await asyncio.to_thread(event.wait, 2), "SQLite worker did not reach the test barrier"

    async def cancel_twice(self, task):
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Cancellation abandoned the owned SQLite worker"

    async def finish(self, task):
        self.release_connect.set()
        self.release_close.set()
        await asyncio.gather(task, return_exceptions=True)
        # This is failure-path cleanup for this test's handles, not a fixture
        # hiding leaked production connections: assertions precede this close.
        for connection in self.connections:
            if not connection.explicitly_closed:
                sqlite3.Connection.close(connection)


async def test_cancelled_memory_acquisition_joins_and_closes_late_connection(tmp_path, monkeypatch):
    lifetime = DatabaseLifetime(monkeypatch, pause_connect=True)
    store = memory.MemoryStore(tmp_path / "memory.db")
    task = asyncio.create_task(store.initialize())
    try:
        await lifetime.entered(lifetime.opened)
        await lifetime.cancel_twice(task)
        lifetime.release_connect.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert len(lifetime.connections) == 1
        assert lifetime.connections[0].explicitly_closed
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            lifetime.connections[0].execute("SELECT 1")
    finally:
        await lifetime.finish(task)


async def test_cancellation_during_memory_close_joins_worker_before_returning(tmp_path, monkeypatch):
    lifetime = DatabaseLifetime(monkeypatch, pause_close=True)
    task = asyncio.create_task(memory.MemoryStore(tmp_path / "memory.db").initialize())
    try:
        await lifetime.entered(lifetime.closing)
        await lifetime.cancel_twice(task)
        lifetime.release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert lifetime.connections[0].explicitly_closed
    finally:
        await lifetime.finish(task)


async def test_cleanup_cancellation_preserves_original_memory_operation_error(tmp_path, monkeypatch):
    lifetime = DatabaseLifetime(monkeypatch, pause_close=True)
    failure = ValueError("controlled schema operation failure")
    async def fail_schema(db):
        raise failure
    monkeypatch.setattr(memory, "_ensure_memory_fts", fail_schema)
    task = asyncio.create_task(memory.MemoryStore(tmp_path / "memory.db").initialize())
    try:
        await lifetime.entered(lifetime.closing)
        await lifetime.cancel_twice(task)
        lifetime.release_close.set()
        with pytest.raises(ValueError) as raised:
            await asyncio.wait_for(task, 2)
        assert raised.value is failure
        assert lifetime.connections[0].explicitly_closed
    finally:
        await lifetime.finish(task)


async def test_cancelled_failing_connector_preserves_original_sqlite_error(tmp_path, monkeypatch):
    lifetime = DatabaseLifetime(monkeypatch, pause_connect=True, fail_connect=True)
    task = asyncio.create_task(memory.MemoryStore(tmp_path / "memory.db").initialize())
    try:
        await lifetime.entered(lifetime.opened)
        await lifetime.cancel_twice(task)
        lifetime.release_connect.set()
        with pytest.raises(sqlite3.OperationalError) as raised:
            await asyncio.wait_for(task, 2)
        assert raised.value is lifetime.failure
    finally:
        await lifetime.finish(task)


async def test_memory_crud_closes_each_acquired_database_before_returning(tmp_path, monkeypatch):
    lifetime = DatabaseLifetime(monkeypatch)
    store = memory.MemoryStore(tmp_path / "memory.db")
    fact = await store.add_fact("Keep database lifetimes explicit.")
    assert await store.list_facts() == [fact]
    assert await store.forget_fact(fact.id)
    assert await store.list_facts() == []
    assert lifetime.connections and all(connection.explicitly_closed for connection in lifetime.connections)
