# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from libre_claw.config import load_config
from libre_claw.core.questions import validate_questions, validate_answers, text_answer
from libre_claw.core.runs import RunStore
from libre_claw.daemon import DaemonServer
from libre_claw.providers.base import LLMProvider, TextDelta, ToolCallReady, Done


QUESTIONS = [{"id": "color", "question": "Choose a color", "options": [{"label": "Blue"}, {"label": "Red"}]}]


class QuestionProvider(LLMProvider):
    def __init__(self):
        self.calls = 0
        self.answer_seen = None

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallReady("ask-1", "ask_user_question", {"questions": QUESTIONS})
        else:
            for message in messages:
                if isinstance(message.content, list):
                    for block in message.content:
                        if block.get("type") == "tool_result":
                            self.answer_seen = json.loads(block["content"])
            yield TextDelta("The answer was received.")
        yield Done()


@pytest.fixture
async def engine_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = load_config(working_directory=workspace)
    config = replace(config, automations=replace(config.automations, enabled=False),
                     memory=replace(config.memory, enabled=False), petdex=replace(config.petdex, enabled=False))
    provider = QuestionProvider()
    server = DaemonServer(config, run_store=RunStore(tmp_path / "runs"),
                          provider_factory=lambda _: provider, start_telegram_bridge=False)
    async with TestClient(TestServer(server.app())) as client:
        yield client, server, provider


async def pending_question(client):
    started = await client.post("/runs", json={"message": "Ask me for a choice"})
    assert started.status == 202, await started.text()
    run_id = (await started.json())["run"]["run_id"]
    async with asyncio.timeout(10):
        while True:
            detail = await (await client.get(f"/runs/{run_id}")).json()
            if detail["pending_questions"]:
                return run_id, detail["pending_questions"][0]
            assert detail["run"]["state"] not in {"failed", "cancelled", "done"}, detail
            await asyncio.sleep(0.01)


async def test_real_app_routes_questions_and_execution_through_cordis(engine_client):
    client, server, provider = engine_client
    before = await (await client.get("/engine")).json()
    assert before["engine"] == "cordis"
    run_id, question = await pending_question(client)
    assert question["questions"][0]["question"] == "Choose a color"
    blocked = await client.post("/engine/restart", json={})
    assert blocked.status == 409
    endpoint = f"/runs/{run_id}/questions/{question['request_id']}"
    invalid = await client.post(endpoint, json={"answers": []})
    assert invalid.status == 400
    reply = {"answers": [{"id": "color", "selected": ["Blue"]}]}
    answered = await client.post(endpoint, json=reply)
    assert answered.status == 200, await answered.text()
    repeated = await client.post(endpoint, json=reply)
    assert repeated.status == 409
    async with asyncio.timeout(10):
        while True:
            detail = await (await client.get(f"/runs/{run_id}")).json()
            if detail["run"]["state"] in {"done", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.01)
    assert detail["run"]["state"] == "done", detail
    assert not detail["pending_questions"]
    assert provider.answer_seen == reply
    graph = await server.engine.inspect()
    components = {item["id"]: item for item in graph["components"]}
    assert components["agent"]["completed"] >= 1
    assert components["providers"]["completed"] >= 2
    assert components["tools"]["completed"] >= 1
    assert components["sessions"]["completed"] >= 1
    assert graph["privacy"]["payloads"] == "opaque-handles"
    assert "Choose a color" not in json.dumps(graph)
    events = await server.run_store.load_events(run_id)
    assert any(event.type == "user_question_answered" for event in events)


async def test_cancelled_question_cannot_be_answered_later(engine_client):
    client, _, _ = engine_client
    run_id, question = await pending_question(client)
    cancelled = await client.post(f"/runs/{run_id}/cancel", json={})
    assert cancelled.status == 200
    async with asyncio.timeout(10):
        while True:
            detail = await (await client.get(f"/runs/{run_id}")).json()
            if detail["run"]["state"] == "cancelled":
                break
            await asyncio.sleep(0.01)
    stale = await client.post(f"/runs/{run_id}/questions/{question['request_id']}",
                             json={"answers": [{"id": "color", "selected": ["Blue"]}]})
    assert stale.status == 409


@pytest.mark.parametrize("value", [[], [{"id": "x", "question": ""}], QUESTIONS * 2,
    [{"id": "x", "question": "Q", "options": [{"label": "A"}, {"label": "A"}]}]])
def test_invalid_questions_are_rejected(value):
    with pytest.raises(ValueError):
        validate_questions(value)


def test_answers_require_explicit_valid_choices_or_text():
    questions = validate_questions(QUESTIONS)
    with pytest.raises(ValueError):
        validate_answers(questions, {"answers": [{"id": "color", "selected": []}]})
    with pytest.raises(ValueError):
        validate_answers(questions, {"answers": [{"id": "color", "selected": ["Blue", "Red"]}]})
    assert text_answer(questions, "1") == {"answers": [{"id": "color", "selected": ["Blue"]}]}
    assert text_answer(questions, "Green") == {"answers": [{"id": "color", "selected": [], "custom": "Green"}]}
