# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from libre_claw.config import load_config
from libre_claw.core.automations import AutomationStore
from libre_claw.core.runs import RunStore
from libre_claw.core.session import ChatMessage
from libre_claw.core.tools import BaseTool, ToolContext, ToolRegistry, ToolResult
from libre_claw.daemon import DaemonClient, DaemonServer, _automation_telegram_message
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolCallReady, ToolSchema, Usage


class RequestStub:
    def __init__(
        self,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        match_info: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("No JSON body")
        return self._body


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.system_prompts: list[str | None] = []
        self.message_batches: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.message_batches.append(list(messages))
        del tools, stream, temperature, max_tokens
        self.system_prompts.append(system)
        for event in self.responses.pop(0):
            yield event


class AskEchoTool(BaseTool):
    name = "ask_echo"
    description = "Echo a value after approval."
    parameters = {"value": {"type": "string"}}
    required = ("value",)
    permission_level = "ask"

    async def execute(self, value: str) -> ToolResult:
        return ToolResult(content=f"echo:{value}", metadata={"duration_ms": 1})


def _registry(_config: object, _memory_store: object) -> ToolRegistry:
    return ToolRegistry([AskEchoTool(ToolContext(working_directory=Path.cwd()))])


def _response_payload(response: object) -> dict[str, Any]:
    text = getattr(response, "text")
    assert isinstance(text, str)
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


async def _wait_for_state(server: DaemonServer, run_id: str, state: str) -> dict[str, Any]:
    for _ in range(100):
        response = await server.get_run(RequestStub(match_info={"run_id": run_id}))  # type: ignore[arg-type]
        payload = _response_payload(response)
        if payload["run"]["state"] == state:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"Run {run_id} did not reach {state}")


async def _wait_for_event(server: DaemonServer, run_id: str, event_type: str) -> dict[str, Any]:
    for _ in range(100):
        response = await server.get_events(RequestStub(match_info={"run_id": run_id}))  # type: ignore[arg-type]
        payload = _response_payload(response)
        for event in payload["events"]:
            if event["type"] == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Run {run_id} did not emit {event_type}")


async def test_daemon_autostarts_telegram_bridge_when_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, telegram=replace(config.telegram, enabled=True, use_daemon=True))
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_telegram_runner(_config: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
        telegram_bot_runner=fake_telegram_runner,
    )

    await server._on_startup(None)  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), timeout=1)
    health = _response_payload(await server.health(RequestStub()))  # type: ignore[arg-type]
    await server._on_cleanup(None)  # type: ignore[arg-type]

    assert health["telegram_bridge"] == "running"
    assert cancelled.is_set()


async def test_daemon_does_not_autostart_telegram_bridge_without_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    config = load_config()
    config = replace(config, telegram=replace(config.telegram, enabled=True, use_daemon=True))

    async def fake_telegram_runner(_config: object) -> None:
        raise AssertionError("Telegram should not start without a configured token")

    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
        telegram_bot_runner=fake_telegram_runner,
    )

    await server._on_startup(None)  # type: ignore[arg-type]
    health = _response_payload(await server.health(RequestStub()))  # type: ignore[arg-type]
    await server._on_cleanup(None)  # type: ignore[arg-type]

    assert health["telegram_bridge"] == "missing_token"
    assert server._telegram_task is None


async def test_daemon_starts_background_run_and_persists_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([[TextDelta("hel"), TextDelta("lo"), Done(Usage(input_tokens=1, output_tokens=2))]])
    config = load_config()
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    start = await server.start_run(RequestStub(body={"message": "hi"}))  # type: ignore[arg-type]
    assert start.status == 202
    run_id = _response_payload(start)["run"]["run_id"]

    detail = await _wait_for_state(server, run_id, "done")
    events = _response_payload(
        await server.get_events(RequestStub(match_info={"run_id": run_id}))  # type: ignore[arg-type]
    )

    assert detail["run"]["working_directory"] == str(tmp_path)
    assert detail["artifacts"]["plan.md"]["exists"] is True
    assert detail["artifacts"]["summary.md"]["size"] == 5
    assert [event["type"] for event in events["events"]] == [
        "run_started",
        "user_message",
        "assistant_delta",
        "assistant_delta",
        "usage",
        "run_finished",
    ]


async def test_daemon_serves_local_dashboard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.dashboard(RequestStub())  # type: ignore[arg-type]

    assert response.content_type == "text/html"
    assert "Libre Claw Dashboard" in response.text
    assert "fetch(path" in response.text
    assert "/runs" in response.text
    assert "/automations" in response.text
    assert "/usage?limit=250" in response.text


async def test_daemon_serves_packaged_dashboard_logo(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.dashboard_asset(RequestStub(match_info={"name": "logo-dark.jpg"}))  # type: ignore[arg-type]
    missing = await server.dashboard_asset(RequestStub(match_info={"name": "old-logo.svg"}))  # type: ignore[arg-type]

    assert response.content_type == "image/jpeg"
    assert response.body.startswith(b"\xff\xd8")
    assert missing.status == 404


async def test_daemon_rejects_request_working_directory_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.start_run(
        RequestStub(body={"message": "hi", "working_directory": str(tmp_path / "outside")})  # type: ignore[arg-type]
    )
    payload = _response_payload(response)

    assert response.status == 403
    assert "cannot override working_directory" in payload["error"]
    assert await server.run_store.list_runs() == []


async def test_daemon_start_run_uses_supplied_session_history(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    session_payload = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "first"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        ],
        "summary": "Earlier context.",
    }

    started = await server.start_run(
        RequestStub(body={"message": "second", "session": session_payload})  # type: ignore[arg-type]
    )
    await _wait_for_state(server, _response_payload(started)["run"]["run_id"], "done")

    assert [[block["text"] for block in message.content] for message in provider.message_batches[0]] == [
        ["first"],
        ["reply"],
        ["second"],
    ]


async def test_daemon_blocks_and_resumes_on_permission_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "ask_echo", {"value": "ok"}), Done()],
            [TextDelta("done"), Done()],
        ]
    )
    config = load_config()
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=_registry,
    )

    started = await server.start_run(RequestStub(body={"message": "use the tool"}))  # type: ignore[arg-type]
    run_id = _response_payload(started)["run"]["run_id"]
    permission = await _wait_for_event(server, run_id, "permission_request")

    blocked = await _wait_for_state(server, run_id, "blocked")
    assert blocked["run"]["state"] == "blocked"
    assert blocked["pending_permissions"] == ["toolu_1"]

    approval = await server.resolve_permission(
        RequestStub(
            match_info={"run_id": run_id, "tool_call_id": permission["data"]["tool_call_id"]},
            body={"resolution": "allow_once"},
        )
    )  # type: ignore[arg-type]
    assert approval.status == 200
    await _wait_for_state(server, run_id, "done")
    events = _response_payload(
        await server.get_events(RequestStub(match_info={"run_id": run_id}))  # type: ignore[arg-type]
    )

    assert "permission_response" in [event["type"] for event in events["events"]]
    assert any(event["type"] == "tool_result" and event["data"]["content"] == "echo:ok" for event in events["events"])


async def test_daemon_client_builds_requests(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            response = await server.health(RequestStub())  # type: ignore[arg-type]
        elif request.method == "POST" and request.url.path == "/runs":
            response = await server.start_run(RequestStub(body=json.loads(request.content)))  # type: ignore[arg-type]
        else:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(response.status, content=response.body, headers={"content-type": "application/json"})

    client = DaemonClient("http://daemon.test", transport=httpx.MockTransport(handler))
    health = await client.health()
    started = await client.start_run("hello")
    await _wait_for_state(server, started["run"]["run_id"], "done")

    assert health["ok"] is True
    assert started["run"]["state"] == "queued"


async def test_daemon_injects_project_skills(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    skill_path = tmp_path / ".libre-claw" / "skills" / "pytest-debug.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Pytest Debug\n\nUse for pytest failures.", encoding="utf-8")
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    config = load_config()
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    started = await server.start_run(RequestStub(body={"message": "debug pytest"}))  # type: ignore[arg-type]
    await _wait_for_state(server, _response_payload(started)["run"]["run_id"], "done")

    assert provider.system_prompts
    assert provider.system_prompts[0] is not None
    assert "Skill: Pytest Debug" in provider.system_prompts[0]


async def test_daemon_injects_soul_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    soul_path = tmp_path / ".libre-claw" / "soul.md"
    soul_path.parent.mkdir(parents=True)
    soul_path.write_text("# Project Soul\n\nBe unmistakably Libre Claw.", encoding="utf-8")
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    config = load_config()
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    started = await server.start_run(RequestStub(body={"message": "hello"}))  # type: ignore[arg-type]
    await _wait_for_state(server, _response_payload(started)["run"]["run_id"], "done")

    assert provider.system_prompts
    assert provider.system_prompts[0] is not None
    assert "Libre Claw soul/persona customization" in provider.system_prompts[0]
    assert "Be unmistakably Libre Claw." in provider.system_prompts[0]


async def test_daemon_automation_api_crud(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server.automation_store = AutomationStore(tmp_path / "automations")

    created = await server.create_automation(
        RequestStub(
            body={
                "name": "Daily health",
                "prompt": "Check repo health",
                "schedule": "daily 09:00",
                "route": "report",
            }
        )  # type: ignore[arg-type]
    )
    automation = _response_payload(created)["automation"]
    automation_id = automation["automation_id"]

    listed = _response_payload(await server.list_automations(RequestStub()))  # type: ignore[arg-type]
    paused = _response_payload(
        await server.pause_automation(RequestStub(match_info={"automation_id": automation_id}))  # type: ignore[arg-type]
    )
    resumed = _response_payload(
        await server.resume_automation(RequestStub(match_info={"automation_id": automation_id}))  # type: ignore[arg-type]
    )
    deleted = _response_payload(
        await server.delete_automation(RequestStub(match_info={"automation_id": automation_id}))  # type: ignore[arg-type]
    )

    assert created.status == 201
    assert listed["automations"][0]["automation_id"] == automation_id
    assert paused["automation"]["status"] == "paused"
    assert resumed["automation"]["status"] == "active"
    assert deleted["deleted"] is True


async def test_daemon_tick_runs_due_automation_and_writes_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([[TextDelta("scheduled done"), Done(Usage(input_tokens=2, output_tokens=3))]])
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="Due",
        prompt="Run scheduled work",
        schedule="every 1 minutes",
        route="report",
        provider="openrouter",
        model="openrouter/auto",
    )
    past_payload = automation.path.read_text(encoding="utf-8").replace(
        automation.next_run_at,
        "2000-01-01T00:00:00+00:00",
    )
    automation.path.write_text(past_payload, encoding="utf-8")

    await server._tick_automations()
    runs = await server.run_store.list_runs(limit=1)
    run = runs[0]
    await _wait_for_state(server, run.run_id, "done")
    updated = await server.automation_store.load(automation.automation_id)
    events = await server.run_store.load_events(run.run_id)

    assert run.title == "Scheduled: Due"
    assert updated is not None
    assert updated.last_run_id == run.run_id
    assert updated.report_path is not None
    assert Path(updated.report_path).exists()
    assert any(event.type == "automation_triggered" for event in events)


async def test_daemon_tick_delivers_telegram_automation_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    sent: list[tuple[int, str]] = []

    async def fake_telegram_sender(_config: object, chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    provider = ScriptedProvider([[TextDelta("scheduled done"), Done(Usage(input_tokens=2, output_tokens=3))]])
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
        telegram_sender=fake_telegram_sender,
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="HN watch",
        prompt="Run scheduled work",
        schedule="every 1 minutes",
        route="telegram",
        provider="openrouter",
        model="openrouter/auto",
        telegram_chat_id=42,
    )
    past_payload = automation.path.read_text(encoding="utf-8").replace(
        automation.next_run_at,
        "2000-01-01T00:00:00+00:00",
    )
    automation.path.write_text(past_payload, encoding="utf-8")

    await server._tick_automations()
    runs = await server.run_store.list_runs(limit=1)
    run = runs[0]
    await _wait_for_state(server, run.run_id, "done")
    events = await server.run_store.load_events(run.run_id)

    assert len(sent) == 1
    assert sent[0][0] == 42
    assert "Scheduled: HN watch" in sent[0][1]
    assert "scheduled done" in sent[0][1]
    assert provider.system_prompts[0] is not None
    assert "Scheduled automation output policy" in provider.system_prompts[0]
    assert "Do not write process narration" in provider.system_prompts[0]
    assert any(event.type == "automation_telegram_delivered" for event in events)


async def test_daemon_failed_telegram_automation_hides_partial_scratch_summary(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = await store.create_run(
        "Scheduled: HN watch",
        kind="chat",
        provider="openrouter",
        model="deepseek/deepseek-v4-flash",
        working_directory=tmp_path,
        state="queued",
    )
    await store.finish_run(
        run.run_id,
        "failed",
        plan="",
        summary="Top 30 IDs: 1, 2, 3\n\nLet me batch-fetch these items.",
        verification="Daemon run finished with state: failed\n",
        diff="",
        browser="",
    )
    failed_run = await store.load_run(run.run_id)
    assert failed_run is not None
    automation = await AutomationStore(tmp_path / "automations").create(
        name="HN watch",
        prompt="Run scheduled work",
        schedule="every 1 minutes",
        route="telegram",
        provider="openrouter",
        model="openrouter/auto",
        telegram_chat_id=42,
    )

    message = _automation_telegram_message(automation, failed_run, tmp_path / "report.md", "failed")

    assert "finished with state: failed" in message
    assert "failed before Libre Claw produced a final clean report" in message
    assert "Top 30 IDs" not in message
    assert "Let me batch-fetch" not in message


async def test_daemon_usage_endpoint_reports_provider_rollups(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    run = await server.run_store.create_run("usage", kind="chat", provider="openrouter", model="openrouter/auto")
    await server.run_store.append_event(run.run_id, "run_started", {"surface": "daemon"})
    await server.run_store.append_event(run.run_id, "usage", {"input_tokens": 11, "output_tokens": 4, "cost": 0.0002})

    response = await server.usage(RequestStub(query={"provider": "openrouter"}))  # type: ignore[arg-type]
    payload = _response_payload(response)

    assert payload["summary"]["total_tokens"] == 15
    assert payload["summary"]["by_surface"][0]["name"] == "daemon"
    assert payload["attribution"]["analytics_url"] == "https://openrouter.ai/apps?url=https://libreclaw.dev"
    assert payload["attribution"]["docs_url"] == "https://libreclaw.dev/docs/"
    assert payload["attribution"]["ranking_targets"] == "Productivity, Coding Agents, Personal Agents, CLI Agents"
    assert "OpenRouter usage" in payload["text"]
