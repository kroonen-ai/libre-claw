# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.core.memory import MemoryStore
from libre_claw.core.session import ChatMessage, Session, text_block
from libre_claw.core.tools import ToolContext
from libre_claw.tools_builtin.filesystem import EditFileTool, WriteFileTool


async def test_memory_store_fact_crud(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    fact = await store.add_fact("User likes tabs.")
    facts = await store.list_facts()
    removed = await store.forget_fact(fact.id)

    assert facts == [fact]
    assert removed is True
    assert await store.list_facts() == []


async def test_memory_store_session_save_and_load(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    session = Session()
    session.add_user_message("hello")
    session.add_assistant_message("hi")
    session.summary = "previous summary"

    saved = await store.save_session("main", session)
    loaded = await store.load_session("main")

    assert saved.name == "main"
    assert loaded is not None
    assert loaded.summary == "previous summary"
    assert loaded.messages == [
        ChatMessage(role="user", content=[text_block("hello")]),
        ChatMessage(role="assistant", content=[text_block("hi")]),
    ]


async def test_memory_store_logs_file_edits_from_tools(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    context = ToolContext(working_directory=tmp_path, memory_store=store)
    path = tmp_path / "file.txt"

    await WriteFileTool(context).execute(path="file.txt", content="hello world")
    await EditFileTool(context).execute(path="file.txt", old_text="world", new_text="Libre Claw")
    edits = await store.list_file_edits()

    assert [edit.tool_name for edit in edits] == ["write_file", "edit_file"]
    assert edits[0].path == str(path)
    assert edits[1].before == "hello world"
    assert edits[1].after == "hello Libre Claw"


def test_session_compaction_summarizes_older_messages() -> None:
    session = Session()
    for index in range(12):
        session.add_user_message(f"message {index}")

    summary = session.compact(keep_last=4)

    assert summary is not None
    assert "message 0" in summary
    assert len(session.messages) == 4
    assert session.messages[0].content == [text_block("message 8")]
