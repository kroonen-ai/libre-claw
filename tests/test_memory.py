# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from libre_claw.core.memory import (
    MemoryStore,
    extract_memories_with_provider,
    new_session_archive_id,
    parse_extracted_memories,
    redact_secrets,
    summarize_session_for_memory,
)
from libre_claw.core.session import ChatMessage, Session, estimate_context_tokens, text_block, tool_result_block, tool_use_block
from libre_claw.core.tools import ToolContext
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolSchema
from libre_claw.tools_builtin.filesystem import EditFileTool, WriteFileTool


class FakeMemoryProvider(LLMProvider):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, system, stream, temperature, max_tokens
        yield TextDelta('{"memories":[{"kind":"preference","scope":"global","text":"Robin prefers New York time."}]}')
        yield Done()


async def test_memory_store_fact_crud(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    fact = await store.add_fact("User likes tabs.")
    facts = await store.list_facts()
    removed = await store.forget_fact(fact.id)

    assert facts == [fact]
    assert removed is True
    assert await store.list_facts() == []


async def test_memory_store_migrates_facts_to_searchable_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    fact = await store.add_fact("User likes tabs.")
    matches = await store.search_memory_items("tabs")
    removed = await store.forget_fact(fact.id)
    active = await store.search_memory_items("tabs")

    assert matches[0].text == "User likes tabs."
    assert removed is True
    assert active == []


async def test_memory_item_crud_search_and_forget(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    item = await store.add_memory_item(
        text="Libre Claw uses Telegram callback tokens.",
        kind="project",
        scope="project",
        source_type="manual",
        project_root=tmp_path,
    )
    matches = await store.search_memory_items("Telegram callback", project_root=tmp_path)
    removed = await store.forget_memory_item(item.id)

    assert matches == [item]
    assert removed is True
    assert await store.search_memory_items("Telegram callback", project_root=tmp_path) == []


async def test_manual_memory_items_are_always_injectable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    item = await store.add_memory_item(text="Robin prefers concise summaries.", source_type="manual")
    await store.add_memory_item(text="Run summary from a task.", source_type="run", source_id="run-1")
    memories = await store.list_always_injected_memories()
    removed = await store.forget_memory_item(item.id)

    assert memories == ["Robin prefers concise summaries."]
    assert removed is True
    assert await store.list_always_injected_memories() == []


async def test_session_archive_appends_redacted_jsonl(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.session_root = tmp_path / "sessions"
    session_id = new_session_archive_id("test")

    await store.append_session_event(session_id, "user_message", {"content": "token=sk-secretsecretsecret"})
    events = await store.load_session_events(session_id)

    assert len(events) == 1
    assert events[0].type == "user_message"
    assert "sk-secret" not in events[0].data["content"]


def test_memory_redaction_and_parser() -> None:
    text = redact_secrets(
        "Authorization: Bearer abcdefghijklmnop\n"
        "OPENAI_API_KEY=sk-abcdefghijklmnop\n"
        "TELEGRAM_BOT_TOKEN=123456789:abcdefghijklmnopqrstuvwxyz"
    )
    parsed = parse_extracted_memories(
        """
        ```json
        {"memories":[
          {"kind":"preference","scope":"global","text":"Robin prefers EST."},
          {"kind":"fact","scope":"global","text":"OPENAI_API_KEY=sk-abcdefghijklmnop"}
        ]}
        ```
        """
    )

    assert "abcdefghijklmnop" not in text
    assert "123456789:" not in text
    assert [memory.text for memory in parsed] == ["Robin prefers EST."]


async def test_extract_memories_with_fake_provider() -> None:
    memories = await extract_memories_with_provider(
        FakeMemoryProvider(),
        user_message="remember my timezone",
        assistant_text="Got it.",
    )

    assert memories[0].kind == "preference"
    assert memories[0].text == "Robin prefers New York time."


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


def test_session_repeated_compaction_bounds_the_rolling_summary() -> None:
    session = Session(summary="old context\n" + ("x" * 80))
    for index in range(6):
        session.add_user_message(f"recent message {index} " + ("y" * 30))

    summary = session.compact(keep_last=2, max_summary_chars=120)

    assert summary is not None
    assert len(summary) <= 120
    assert summary.startswith("[Earlier compacted context omitted]\n")
    assert "recent message 3" in summary
    assert len(session.messages) == 2


def test_summarize_session_for_memory_redacts_and_bounds() -> None:
    session = Session()
    session.add_user_message("remember this")
    session.add_assistant_message("OPENAI_API_KEY=sk-abcdefghijklmnop " + ("x" * 5000))

    summary = summarize_session_for_memory(session, limit=200)

    assert len(summary) <= 200
    assert "sk-abcdefghijklmnop" not in summary


def test_estimate_context_tokens_counts_text_and_tool_blocks() -> None:
    session = Session()
    session.add_user_message("hello world")
    session.add_assistant_blocks([tool_use_block("toolu_1", "read_file", {"path": "README.md"})])
    session.add_tool_result_blocks([tool_result_block("toolu_1", "file contents")])

    tokens = estimate_context_tokens(session.messages, summary="previous summary", extra_texts=("system prompt",))

    assert tokens > 0
