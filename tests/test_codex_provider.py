# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import libre_claw.providers.codex as codex_provider
from libre_claw.auth.codex import CodexCommandEvent, CodexCommandResult, CodexStatus
from libre_claw.core.session import ChatMessage, text_block
from libre_claw.providers.base import Done, ProviderError, TextDelta


def test_extract_codex_text_from_jsonl_and_plain_fallback() -> None:
    output = "\n".join(
        [
            '{"type":"agent_message_delta","delta":"Hello"}',
            '{"item":{"content":[{"text":" world"}]}}',
            "plain line",
        ]
    )

    assert codex_provider._extract_codex_text(output) == ["Hello", " world", "plain line\n"]


def test_codex_usage_is_parsed_from_turn_completed() -> None:
    usage = codex_provider._usage_from_codex_jsonl(
        '{"type":"turn.completed","usage":'
        '{"input_tokens":10,"cached_input_tokens":4,"output_tokens":3,"reasoning_output_tokens":2}}\n'
    )

    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.cached_tokens == 4
    assert usage.output_tokens == 3
    assert usage.reasoning_tokens == 2


def test_codex_completed_text_chunks_on_word_boundaries() -> None:
    assert codex_provider._chunk_text("hello beautiful world", 10) == ["hello ", "beautiful ", "world"]


async def test_codex_provider_requires_login(monkeypatch, tmp_path: Path) -> None:
    async def fake_status(executable: str = "codex") -> CodexStatus:
        del executable
        return CodexStatus(available=True, logged_in=False, detail="Not logged in")

    monkeypatch.setattr(codex_provider, "codex_status", fake_status)
    provider = codex_provider.CodexProvider(model="gpt-5.5", working_directory=tmp_path)

    events = [
        event
        async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("hi")])])
    ]

    assert isinstance(events[0], ProviderError)
    assert "/codex login" in events[0].message


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "future-codex-model"])
async def test_codex_provider_streams_codex_exec_with_prompt(monkeypatch, tmp_path: Path, model: str) -> None:
    captured: dict[str, object] = {}

    async def fake_status(executable: str = "codex") -> CodexStatus:
        del executable
        return CodexStatus(available=True, logged_in=True, detail="Logged in using ChatGPT")

    async def fake_stream(args, input_text=None):  # noqa: ANN001
        captured["args"] = args
        captured["input_text"] = input_text
        yield CodexCommandEvent(stream="stdout", text='{"item":{"type":"agent_message","text":"Codex works"}}\n')
        yield CodexCommandEvent(
            stream="stdout",
            text='{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}\n',
        )
        yield CodexCommandResult(
            args=tuple(args),
            exit_code=0,
            stdout='{"item":{"type":"agent_message","text":"Codex works"}}\n',
            stderr="",
        )

    monkeypatch.setattr(codex_provider, "codex_status", fake_status)
    monkeypatch.setattr(codex_provider, "stream_codex_command", fake_stream)
    provider = codex_provider.CodexProvider(model=model, working_directory=tmp_path, timeout=12, replay_delay=0)

    events = [
        event
        async for event in provider.complete(
            messages=[ChatMessage(role="user", content=[text_block("build this")])],
            system="System text",
        )
    ]

    assert [event.text for event in events if isinstance(event, TextDelta)] == ["Codex works"]
    done = next(event for event in events if isinstance(event, Done))
    assert done.usage is not None
    assert done.usage.input_tokens == 5
    assert done.usage.output_tokens == 2
    assert captured["args"][:5] == ["codex", "--ask-for-approval", "never", "exec", "--json"]
    assert "--model" in captured["args"]
    assert captured["args"][captured["args"].index("--model") + 1] == model
    assert "build this" in str(captured["input_text"])
    assert "System text" in str(captured["input_text"])


async def test_codex_provider_reports_stream_exit_errors(monkeypatch, tmp_path: Path) -> None:
    async def fake_status(executable: str = "codex") -> CodexStatus:
        del executable
        return CodexStatus(available=True, logged_in=True, detail="Logged in using ChatGPT")

    async def fake_stream(args, input_text=None):  # noqa: ANN001
        del input_text
        yield CodexCommandEvent(stream="stderr", text="bad flag\n")
        yield CodexCommandResult(args=tuple(args), exit_code=2, stdout="", stderr="bad flag\n")

    monkeypatch.setattr(codex_provider, "codex_status", fake_status)
    monkeypatch.setattr(codex_provider, "stream_codex_command", fake_stream)
    provider = codex_provider.CodexProvider(model="gpt-5.5", working_directory=tmp_path)

    events = [
        event
        async for event in provider.complete(messages=[ChatMessage(role="user", content=[text_block("hi")])])
    ]

    assert isinstance(events[0], ProviderError)
    assert "exited with 2" in events[0].message


async def test_closing_provider_stream_closes_native_command(monkeypatch, tmp_path: Path) -> None:
    closed = False

    async def fake_status(executable: str = "codex") -> CodexStatus:
        return CodexStatus(available=True, logged_in=True, detail="Logged in")

    async def fake_stream(args, input_text=None):
        nonlocal closed
        try:
            yield CodexCommandEvent(stream="stdout", text='{"item":{"type":"agent_message","text":"Partial result"}}\n')
            await asyncio.sleep(30)
        finally:
            closed = True

    monkeypatch.setattr(codex_provider, "codex_status", fake_status)
    monkeypatch.setattr(codex_provider, "stream_codex_command", fake_stream)
    provider = codex_provider.CodexProvider(model="arbitrary-model", working_directory=tmp_path, replay_delay=0)
    stream = provider.complete(messages=[ChatMessage(role="user", content=[text_block("inspect")])])
    assert isinstance(await anext(stream), TextDelta)
    await stream.aclose()
    assert closed
