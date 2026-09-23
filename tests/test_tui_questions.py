# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from libre_claw.config import load_config
from libre_claw.core.questions import AgentUserQuestionRequest, text_answer, validate_questions
from libre_claw.tui.app import LibreClawApp, StreamRenderBuffer


@pytest.fixture
def question_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(LibreClawApp, "_rebuild_agent", lambda self: None)
    config = load_config(working_directory=tmp_path)
    config = replace(config, tui=replace(config.tui, use_daemon=False), memory=replace(config.memory, enabled=False),
                     heartbeat=replace(config.heartbeat, enabled=False), petdex=replace(config.petdex, enabled=False))
    app = LibreClawApp(config=config)
    app.notes = []
    app.recorded_questions = []
    app.recorded_states = []
    monkeypatch.setattr(app, "_append_system", app.notes.append)
    monkeypatch.setattr(app, "_update_shell_chrome", lambda: None)
    monkeypatch.setattr(app, "_record_run_event_later", lambda kind, data: app.recorded_questions.append((kind, data)))
    monkeypatch.setattr(app, "_set_run_state_later", app.recorded_states.append)
    monkeypatch.setattr(app, "_archive_session_event_later", lambda *args: None)
    monkeypatch.setattr(app, "_flush_stream_buffer", lambda *args: None)
    return app


def questions(**changes):
    return validate_questions([{"id": "direction", "question": "What should we build?", "header": "Direction", "options": [
        {"label": "An app", "description": "Build an interface"}, {"label": "An API", "description": "Build a backend"},
    ], **changes}])


@pytest.mark.parametrize("answer,expected", [
    ("2", {"id": "direction", "selected": ["An API"]}),
    ("An app", {"id": "direction", "selected": ["An app"]}),
    ("A small local tool", {"id": "direction", "selected": [], "custom": "A small local tool"}),
])
async def test_local_question_answer_resumes_task_and_records_resolution(question_app, answer, expected):
    app = question_app
    future = asyncio.get_running_loop().create_future()
    event = AgentUserQuestionRequest("request-1", questions(), future)
    assert app._handle_agent_stream_event(event, 0, StreamRenderBuffer(0.02, 4096), stop_on_error=False) == (True, False)
    assert app.recorded_states == ["blocked"]
    assert not future.done()
    assert "pending question" in app._input_placeholder()
    assert "needs your answer" in app._status_text()
    assert "Enter send answer" in app._composer_meta_text()
    await app.handle_user_input(answer)
    assert future.result() == {"answers": [expected]}
    assert app.recorded_states == ["blocked", "running"]
    assert app.recorded_questions[-1] == ("user_question_answered", {"request_id": "request-1", "answers": [expected]})
    assert not app._user_questions and app.notes[-1] == "Answer sent."


async def test_multiple_and_multiselect_questions_display_real_ids_and_accept_json(question_app):
    app = question_app
    future = asyncio.get_running_loop().create_future()
    pending = questions(multiSelect=True) + validate_questions([{"id": "details", "question": "Any details?"}])
    app._show_user_question("request", pending, future)
    assert '"id": "direction"' in app.notes[-1] and '"id": "details"' in app.notes[-1]
    assert "/cancel" in app.notes[-1]
    assert "JSON" in app._input_placeholder()
    answers = {"answers": [{"id": "direction", "selected": ["An app", "An API"]}, {"id": "details", "selected": [], "custom": "Keep it local"}]}
    await app.handle_user_input(json.dumps(answers))
    assert future.result() == answers


async def test_invalid_or_already_resolved_answers_do_not_resume_local_question(question_app):
    app = question_app
    future = asyncio.get_running_loop().create_future()
    app._show_user_question("request", questions(), future)
    await app.handle_user_input(" ")
    assert not future.done() and "request" in app._user_questions
    assert "Could not send answer" in app.notes[-1]
    future.cancel()
    await app.handle_user_input("1")
    assert not app._user_questions and "no longer waiting" in app.notes[-1]


async def test_cancel_command_cancels_question_and_active_generation(question_app):
    app = question_app
    future = asyncio.get_running_loop().create_future()
    app._show_user_question("request", questions(), future)
    app._active_task = asyncio.create_task(asyncio.Event().wait())
    await app.handle_user_input("/cancel")
    with pytest.raises(asyncio.CancelledError):
        await app._active_task
    assert future.cancelled() and not app._user_questions


async def test_daemon_question_http_failure_preserves_pending_answer_and_retries(question_app):
    app = question_app

    class Client:
        failed = True
        calls = []

        async def answer_question(self, run_id, request_id, answers):
            self.calls.append((run_id, request_id, answers))
            if self.failed:
                request = httpx.Request("POST", "http://127.0.0.1/questions")
                raise httpx.HTTPStatusError("Denied", request=request, response=httpx.Response(403, request=request))

    app.daemon_client = Client()
    app._handle_daemon_event("run-1", {"type": "user_question", "data": {"request_id": "request", "questions": questions()}}, 0)
    await app.handle_user_input("1")
    assert "request" in app._user_questions and "Denied" in app.notes[-1]
    app.daemon_client.failed = False
    await app.handle_user_input("2")
    assert not app._user_questions
    assert app.daemon_client.calls[-1] == ("run-1", "request", {"answers": [{"id": "direction", "selected": ["An API"]}]})


async def test_daemon_answer_is_not_submitted_twice_while_request_is_in_flight(question_app):
    app = question_app
    entered, proceed = asyncio.Event(), asyncio.Event()

    class Client:
        calls = 0

        async def answer_question(self, *args):
            self.calls += 1
            entered.set()
            await proceed.wait()

    app.daemon_client = Client()
    app._show_user_question("request", questions(), None, "run-1")
    first = asyncio.create_task(app.handle_user_input("1"))
    await entered.wait()
    await app.handle_user_input("2")
    assert app.daemon_client.calls == 1 and "already being sent" in app.notes[-1]
    proceed.set()
    await first
    assert not app._answering_questions


def test_daemon_resume_reconciles_pending_questions_and_terminal_events_clear_them(question_app):
    app = question_app
    pending = [{"request_id": "request", "questions": questions()}]
    app._sync_daemon_questions("run-1", pending)
    assert app._user_questions["request"][2] == "run-1"
    count = len(app.notes)
    app._sync_daemon_questions("run-1", pending)
    assert len(app.notes) == count
    app._handle_daemon_event("run-1", {"type": "user_question_answered", "data": {"request_id": "request"}}, 0)
    assert not app._user_questions
    app._sync_daemon_questions("run-1", pending)
    app._show_user_question("other", questions(), None, "run-2")
    app._handle_daemon_event("run-1", {"type": "run_finished", "data": {"state": "cancelled"}}, 0)
    assert set(app._user_questions) == {"other"}
    app._sync_daemon_questions("run-2", [])
    assert not app._user_questions
    app._handle_daemon_event("run-1", {"type": "user_question", "data": {}}, 0)
    assert "invalid question" in app.notes[-1] and not app._user_questions


async def test_unmount_joins_task_cleanup_and_durable_events_before_disposing_services(question_app):
    app = question_app
    order = []

    async def journal():
        await asyncio.sleep(0)
        order.append("journal")

    async def waiting(name):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            order.append(name)
            if name == "agent":
                app._run_background_tasks.add(asyncio.create_task(journal()))

    class Manager:
        async def aclose(self):
            assert set(order) == {"agent", "memory", "catalog", "heartbeat", "sync", "queue", "journal"}
            order.append("plugins")

    class Engine:
        async def aclose(self):
            assert order[-1] == "plugins"
            order.append("engine")

    app.cordis_manager, app.engine = Manager(), Engine()
    app._active_task = asyncio.create_task(waiting("agent"))
    app._heartbeat_task = asyncio.create_task(waiting("heartbeat"))
    app._daemon_model_sync_task = asyncio.create_task(waiting("sync"))
    app._memory_background_tasks.add(asyncio.create_task(waiting("memory")))
    app._model_catalog_tasks["model"] = asyncio.create_task(waiting("catalog"))
    app._local_queue_wakeups = {"run": asyncio.create_task(waiting("queue"))}
    future = asyncio.get_running_loop().create_future()
    app._show_user_question("request", questions(), future)
    await asyncio.sleep(0)
    await app.on_unmount()
    assert order[-2:] == ["plugins", "engine"] and future.cancelled()


def test_single_multiselect_question_accepts_structured_answer_without_treating_json_as_custom():
    answers = {"answers": [{"id": "direction", "selected": ["An app", "An API"]}]}
    assert text_answer(questions(multiSelect=True), json.dumps(answers)) == answers


async def test_heartbeat_cannot_answer_a_pending_question_for_the_user(question_app):
    app = question_app
    future = asyncio.get_running_loop().create_future()
    app._show_user_question("request", questions(), future)
    await app._run_tui_heartbeat_once()
    assert not future.done() and "Heartbeat skipped" in app.notes[-1]
    future.cancel()
