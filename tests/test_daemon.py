# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from libre_claw.config import FallbackConfig, FallbackRouteConfig, load_config
from libre_claw.core.automations import AutomationStore
from libre_claw.core.runs import RunStore
from libre_claw.core.session import ChatMessage
from libre_claw.core.tools import BaseTool, ToolContext, ToolRegistry, ToolResult
from libre_claw.daemon import DaemonClient, DaemonServer, _automation_finalizer_prompt, _automation_telegram_message
from libre_claw.providers.base import Done, LLMProvider, ProviderError, StreamEvent, TextDelta, ToolCallReady, ToolSchema, Usage
from libre_claw.providers.openrouter_metadata import OpenRouterModelLimits
from libre_claw.web.dashboard import dashboard_html


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
        self.max_tokens_values: list[int | None] = []

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
        self.max_tokens_values.append(max_tokens)
        del tools, stream, temperature
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
    await _wait_for_event(server, run_id, "run_finished")
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
    assert response.headers["Cache-Control"] == "no-store"
    assert "Libre Claw Dashboard" in response.text
    assert "fetch(path" in response.text
    assert "/runs" in response.text
    assert "/automations" in response.text
    assert "/automations/${id}/run" in response.text
    assert "Run now" in response.text
    assert "/usage?limit=250" in response.text
    assert "/config/theme" in response.text
    assert "/assets/lobster-icon.svg" in response.text
    assert "https://github.com/kroonen-ai/libre-claw" in response.text
    assert "https://git.kroonen.ai/kroonen-ai/libre-claw" in response.text
    assert "GitLab mirror" in response.text
    assert "Edit Schedule" in response.text
    assert 'method = editingId ? "PUT" : "POST"' in response.text
    assert "libre-claw-dashboard-theme" in response.text
    assert 'const fallback = "lobster";' in response.text
    assert "Lobster" in response.text
    assert "Lobster Light" in response.text
    assert 'html[data-theme="lobster-light"]' in response.text
    assert 'id="themeSelect"' in response.text
    assert "GitHub Dark" in response.text
    assert "GitHub Light" in response.text
    assert "Monokai Pro" in response.text
    assert "Night Owl" in response.text
    assert "Tokyo Night" in response.text
    assert "Ayu Mirage" in response.text
    assert "Dracula" in response.text
    assert "Catppuccin Mocha" in response.text
    assert "Catppuccin Latte" in response.text
    assert "Gruvbox Dark" in response.text
    assert "Nord" in response.text
    assert "Solarized Dark" in response.text
    assert "Solarized Light" in response.text
    assert "One Dark Pro" in response.text
    assert "Rose Pine" in response.text
    assert "Kanagawa" in response.text
    assert "Matrix" in response.text


def test_dashboard_html_uses_config_theme_fallback() -> None:
    assert 'const fallback = "matrix";' in dashboard_html(theme="matrix")
    assert 'const fallback = "lobster-light";' in dashboard_html(theme="lobster-light")
    assert 'const fallback = "lobster-light";' in dashboard_html(theme="clear")
    assert 'const fallback = "github-light";' in dashboard_html(theme="light")
    assert 'const fallback = "lobster";' in dashboard_html(theme="dark")
    assert 'const fallback = "lobster";' in dashboard_html(theme="libre-default")
    assert 'const fallback = "lobster";' in dashboard_html(theme="unknown-theme")


async def test_daemon_theme_update_persists_global_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.update_theme(RequestStub(body={"theme": "tokyo-night"}))  # type: ignore[arg-type]
    payload = _response_payload(response)
    config_path = tmp_path / ".libre-claw" / "config.toml"

    assert response.status == 200
    assert payload["theme"] == "tokyo-night"
    assert payload["label"] == "Tokyo Night"
    assert payload["persisted_path"] == str(config_path)
    assert server.config.general.theme == "tokyo-night"
    assert 'theme = "tokyo-night"' in config_path.read_text(encoding="utf-8")
    assert load_config().general.theme == "tokyo-night"


async def test_daemon_shutdown_endpoint_sets_shutdown_event(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server._shutdown_event = asyncio.Event()

    response = await server.shutdown(RequestStub())  # type: ignore[arg-type]
    payload = _response_payload(response)

    assert payload == {"ok": True, "stopping": True}
    assert server._shutdown_event.is_set()


async def test_daemon_updates_runtime_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def fake_limits(*_args: Any, **_kwargs: Any) -> OpenRouterModelLimits:
        return OpenRouterModelLimits(context_window_tokens=1_048_576, max_completion_tokens=32_768, source="models")

    monkeypatch.setattr("libre_claw.daemon.detect_openrouter_model_limits", fake_limits)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    before = _response_payload(await server.current_model(RequestStub()))  # type: ignore[arg-type]
    response = await server.update_model(  # type: ignore[arg-type]
        RequestStub(body={"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"})
    )
    after = _response_payload(response)

    assert before["provider"] == "anthropic"
    assert before["model"] == "claude-opus-4-8"
    assert response.status == 200
    assert after["provider"] == "openrouter"
    assert after["model"] == "deepseek/deepseek-v4-pro"
    assert after["context_window_tokens"] == 1_048_576
    assert after["detected_max_completion_tokens"] == 32_768
    assert after["detected_context_source"] == "models"
    assert server.config.general.default_provider == "openrouter"
    assert server.config.general.default_model == "deepseek/deepseek-v4-pro"
    assert server.config.agent.context_window_tokens == 1_048_576
    assert server.config.providers["openrouter"]["default_model"] == "deepseek/deepseek-v4-pro"


async def test_daemon_openrouter_metadata_failure_is_nonfatal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def broken_limits(*_args: Any, **_kwargs: Any) -> OpenRouterModelLimits:
        raise PermissionError("ssl context blocked")

    monkeypatch.setattr("libre_claw.daemon.detect_openrouter_model_limits", broken_limits)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    config = replace(
        server.config,
        general=replace(
            server.config.general,
            default_provider="openrouter",
            default_model="moonshotai/kimi-k2.7-code",
        ),
    )

    updated = await server._with_openrouter_model_limits(config)

    assert updated is config
    assert updated.agent.context_window_tokens == config.agent.context_window_tokens


async def test_daemon_global_model_update_updates_scheduled_automations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def fake_limits(*_args: Any, **_kwargs: Any) -> OpenRouterModelLimits:
        return OpenRouterModelLimits(context_window_tokens=262_144, max_completion_tokens=16_384, source="models")

    monkeypatch.setattr("libre_claw.daemon.detect_openrouter_model_limits", fake_limits)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="Hacker News watch",
        prompt="Fetch HN",
        schedule="hourly",
        provider="openrouter",
        model="minimax/minimax-m3",
    )

    response = await server.update_model(  # type: ignore[arg-type]
        RequestStub(body={"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "persist_global": True})
    )
    payload = _response_payload(response)
    updated = await server.automation_store.load(automation.automation_id)

    assert response.status == 200
    assert payload["provider"] == "openrouter"
    assert payload["model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["automations_updated"] == 1
    assert updated is not None
    assert updated.provider == "openrouter"
    assert updated.model == "xiaomi/mimo-v2.5-pro"


async def test_daemon_updates_runtime_fallback_and_persists_global_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.update_fallback(  # type: ignore[arg-type]
        RequestStub(
            body={
                "enabled": True,
                "recheck_after_attempts": 2,
                "persist_global": True,
                "routes": [
                    {"provider": "openrouter", "model": "openrouter/auto", "api_key_env": "OPENROUTER_BACKUP_KEY"},
                    {"provider": "ollama", "model": "kimi-k2.6:cloud"},
                ],
            }
        )
    )
    payload = _response_payload(response)
    config_path = tmp_path / ".libre-claw" / "config.toml"

    assert response.status == 200
    assert payload["enabled"] is True
    assert payload["recheck_after_attempts"] == 2
    assert payload["routes"][0]["provider"] == "openrouter"
    assert payload["routes"][1]["model"] == "kimi-k2.6:cloud"
    assert payload["persisted_path"] == str(config_path)
    assert server.config.fallback.routes[0].model == "openrouter/auto"
    assert load_config().fallback.routes[1].provider == "ollama"


async def test_daemon_serves_packaged_dashboard_lobster_icon(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: ScriptedProvider([[TextDelta("ok"), Done()]]),
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    response = await server.dashboard_asset(  # type: ignore[arg-type]
        RequestStub(match_info={"name": "lobster-icon.svg"})
    )
    missing = await server.dashboard_asset(RequestStub(match_info={"name": "old-logo.svg"}))  # type: ignore[arg-type]

    assert response.content_type == "image/svg+xml"
    assert response.headers["Cache-Control"] == "no-store"
    assert b"Libre Claw lobster" in response.body
    assert "🦞".encode("utf-8") in response.body
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


async def test_daemon_start_run_passes_image_attachments_to_agent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([[TextDelta("ok"), Done()]])
    server = DaemonServer(
        load_config(),
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )

    started = await server.start_run(
        RequestStub(
            body={
                "message": "inspect",
                "attachments": [
                    {
                        "media_type": "image/png",
                        "data": "aGVsbG8=",
                        "filename": "shot.png",
                        "path": str(tmp_path / "shot.png"),
                    }
                ],
            }
        )  # type: ignore[arg-type]
    )
    run_id = _response_payload(started)["run"]["run_id"]
    await _wait_for_state(server, run_id, "done")

    user_message = provider.message_batches[0][-1]
    assert user_message.content == [
        {"type": "text", "text": "inspect"},
        {
            "type": "image",
            "media_type": "image/png",
            "data": "aGVsbG8=",
            "filename": "shot.png",
            "path": str(tmp_path / "shot.png"),
        },
    ]
    user_event = await _wait_for_event(server, run_id, "user_message")
    assert user_event["data"]["attachments"] == [
        {"media_type": "image/png", "filename": "shot.png", "path": str(tmp_path / "shot.png")}
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


async def test_daemon_automation_auto_approves_configured_tools(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider(
        [
            [ToolCallReady("toolu_1", "ask_echo", {"value": "ok"}), Done()],
            [TextDelta("done"), Done()],
        ]
    )
    config = load_config()
    config = replace(config, automations=replace(config.automations, auto_approve_tools=("ask_echo",)))
    run_store = RunStore(tmp_path / "runs")
    server = DaemonServer(
        config,
        run_store=run_store,
        provider_factory=lambda _config: provider,
        registry_factory=_registry,
    )
    run = await run_store.create_run(
        "Scheduled: approval smoke",
        kind="chat",
        provider=config.general.default_provider,
        model=config.general.default_model,
        working_directory=config.general.working_directory,
        state="queued",
    )

    state = await server._run_agent(run, "use the tool", config, surface="automation:report")
    events = _response_payload(
        await server.get_events(RequestStub(match_info={"run_id": run.run_id}))  # type: ignore[arg-type]
    )
    event_types = [event["type"] for event in events["events"]]

    assert state == "done"
    assert "permission_request" not in event_types
    assert any(event["type"] == "tool_result" and event["data"]["content"] == "echo:ok" for event in events["events"])


async def test_daemon_client_builds_requests(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def fake_limits(*_args: Any, **_kwargs: Any) -> OpenRouterModelLimits:
        return OpenRouterModelLimits(context_window_tokens=524_288, max_completion_tokens=16_384, source="models")

    monkeypatch.setattr("libre_claw.daemon.detect_openrouter_model_limits", fake_limits)
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
        elif request.method == "GET" and request.url.path == "/config/model":
            response = await server.current_model(RequestStub())  # type: ignore[arg-type]
        elif request.method == "PATCH" and request.url.path == "/config/model":
            response = await server.update_model(RequestStub(body=json.loads(request.content)))  # type: ignore[arg-type]
        elif request.method == "GET" and request.url.path == "/config/fallback":
            response = await server.current_fallback(RequestStub())  # type: ignore[arg-type]
        elif request.method == "PATCH" and request.url.path == "/config/fallback":
            response = await server.update_fallback(RequestStub(body=json.loads(request.content)))  # type: ignore[arg-type]
        elif request.method == "POST" and request.url.path == "/runs":
            response = await server.start_run(RequestStub(body=json.loads(request.content)))  # type: ignore[arg-type]
        else:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(response.status, content=response.body, headers={"content-type": "application/json"})

    client = DaemonClient("http://daemon.test", transport=httpx.MockTransport(handler))
    health = await client.health()
    model_before = await client.current_model()
    model_after = await client.update_model("openrouter", "deepseek/deepseek-v4-pro")
    fallback_before = await client.current_fallback()
    fallback_after = await client.update_fallback(
        FallbackConfig(
            enabled=True,
            routes=(FallbackRouteConfig(provider="openrouter", model="openrouter/auto", api_key_env=""),),
            recheck_after_attempts=3,
        )
    )
    started = await client.start_run("hello")
    await _wait_for_state(server, started["run"]["run_id"], "done")

    assert health["ok"] is True
    assert model_before["provider"] == "anthropic"
    assert model_before["model"] == "claude-opus-4-8"
    assert model_after["provider"] == "openrouter"
    assert model_after["model"] == "deepseek/deepseek-v4-pro"
    assert model_after["context_window_tokens"] == 524_288
    assert fallback_before["enabled"] is False
    assert fallback_after["enabled"] is True
    assert fallback_after["routes"][0]["model"] == "openrouter/auto"
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
    soul_path = tmp_path / ".libre-claw" / "SOUL.md"
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
    updated = _response_payload(
        await server.update_automation(  # type: ignore[arg-type]
            RequestStub(
                match_info={"automation_id": automation_id},
                body={
                    "name": "Daily health edited",
                    "prompt": "Check repo health and CI",
                    "schedule": "every 45 minutes",
                    "route": "telegram",
                    "status": "paused",
                    "provider": "ollama",
                    "model": "kimi-k2.6:cloud",
                    "telegram_chat_id": "12345",
                },
            )
        )
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
    assert updated["automation"]["name"] == "Daily health edited"
    assert updated["automation"]["prompt"] == "Check repo health and CI"
    assert updated["automation"]["schedule"] == "every 45 minutes"
    assert updated["automation"]["route"] == "telegram"
    assert updated["automation"]["provider"] == "ollama"
    assert updated["automation"]["model"] == "kimi-k2.6:cloud"
    assert updated["automation"]["telegram_chat_id"] == 12345
    assert resumed["automation"]["status"] == "active"
    assert deleted["deleted"] is True


async def test_daemon_can_run_automation_now(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    provider = ScriptedProvider([[TextDelta("manual scheduled done"), Done(Usage(input_tokens=2, output_tokens=3))]])
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="Manual",
        prompt="Run scheduled work now",
        schedule="daily 09:00",
        route="report",
        provider=config.general.default_provider,
        model=config.general.default_model,
        status="paused",
    )

    response = await server.run_automation_now(  # type: ignore[arg-type]
        RequestStub(match_info={"automation_id": automation.automation_id})
    )
    payload = _response_payload(response)
    run_id = payload["run"]["run_id"]
    await _wait_for_state(server, run_id, "done")
    updated = await server.automation_store.load(automation.automation_id)
    events = await server.run_store.load_events(run_id)

    assert response.status == 202
    assert payload["automation"]["last_run_id"] == run_id
    assert updated is not None
    assert updated.status == "paused"
    assert updated.last_run_id == run_id
    assert updated.report_path is not None
    assert Path(updated.report_path).exists()
    assert any(event.type == "automation_triggered" for event in events)


async def test_daemon_tick_runs_due_automation_and_writes_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    provider = ScriptedProvider([[TextDelta("scheduled done"), Done(Usage(input_tokens=2, output_tokens=3))]])
    server = DaemonServer(
        config,
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
        provider=config.general.default_provider,
        model=config.general.default_model,
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
    config = load_config()
    sent: list[tuple[int, str]] = []

    async def fake_telegram_sender(_config: object, chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    provider = ScriptedProvider(
        [
            [
                TextDelta(
                    "scheduled done\n\n"
                    "I have the exact output, but I don't have a Telegram send tool available "
                    "in this environment to dispatch it. If you have a Telegram bot/webhook "
                    "configured, paste the message above."
                ),
                Done(Usage(input_tokens=2, output_tokens=3)),
            ]
        ]
    )
    server = DaemonServer(
        config,
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
        provider=config.general.default_provider,
        model=config.general.default_model,
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
    updated = await server.automation_store.load(automation.automation_id)
    assert updated is not None
    assert updated.report_path is not None
    report = Path(updated.report_path).read_text(encoding="utf-8")

    assert len(sent) == 1
    assert sent[0][0] == 42
    assert "Scheduled: HN watch" in sent[0][1]
    assert "scheduled done" in sent[0][1]
    assert "Telegram send tool" not in sent[0][1]
    assert "bot/webhook" not in sent[0][1]
    assert "scheduled done" in report
    assert "Telegram send tool" not in report
    assert provider.system_prompts[0] is not None
    assert "Scheduled automation output policy" in provider.system_prompts[0]
    assert "Do not write process narration" in provider.system_prompts[0]
    assert "Libre Claw owns delivery" in provider.system_prompts[0]
    assert any(event.type == "automation_telegram_delivered" for event in events)


async def test_daemon_automation_finalizer_recovers_partial_failed_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    tool_limit = config.agent.max_tool_calls_per_turn
    provider = ScriptedProvider(
        [
            [ProviderError(f"Stopped after exceeding {tool_limit} tool calls in one turn.")],
            [TextDelta("Clean scheduled report from saved observations."), Done()],
        ]
    )
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="Research watch",
        prompt="Fetch sources and produce a clean final report.",
        schedule="every 1 minutes",
        route="report",
        provider=config.general.default_provider,
        model=config.general.default_model,
    )
    past_payload = automation.path.read_text(encoding="utf-8").replace(
        automation.next_run_at,
        "2000-01-01T00:00:00+00:00",
    )
    automation.path.write_text(past_payload, encoding="utf-8")

    await server._tick_automations()
    run = (await server.run_store.list_runs(limit=1))[0]
    await _wait_for_state(server, run.run_id, "done")
    events = await server.run_store.load_events(run.run_id)

    assert (run.path / "summary.md").read_text(encoding="utf-8") == "Clean scheduled report from saved observations."
    assert any(event.type == "automation_finalized" for event in events)
    assert provider.system_prompts[-1] is not None
    assert "scheduled-run finalizer" in provider.system_prompts[-1]
    assert provider.max_tokens_values[-1] == config.automations.finalizer_max_tokens


async def test_daemon_automation_finalizer_recovers_empty_provider_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    sent: list[tuple[int, str]] = []

    async def fake_telegram_sender(_config: object, chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    provider = ScriptedProvider(
        [
            [Done(Usage(input_tokens=20, output_tokens=4))],
            [TextDelta("Clean scheduled report from saved observations."), Done()],
        ]
    )
    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: provider,
        registry_factory=lambda _config, _memory: ToolRegistry(),
        telegram_sender=fake_telegram_sender,
    )
    server.automation_store = AutomationStore(tmp_path / "automations")
    automation = await server.automation_store.create(
        name="HN watch",
        prompt="Fetch sources and produce a clean final report.",
        schedule="every 1 minutes",
        route="telegram",
        provider=config.general.default_provider,
        model=config.general.default_model,
        telegram_chat_id=42,
    )
    past_payload = automation.path.read_text(encoding="utf-8").replace(
        automation.next_run_at,
        "2000-01-01T00:00:00+00:00",
    )
    automation.path.write_text(past_payload, encoding="utf-8")

    await server._tick_automations()
    run = (await server.run_store.list_runs(limit=1))[0]
    await _wait_for_state(server, run.run_id, "done")
    events = await server.run_store.load_events(run.run_id)

    assert (run.path / "summary.md").read_text(encoding="utf-8") == "Clean scheduled report from saved observations."
    assert len(sent) == 1
    assert "Clean scheduled report from saved observations." in sent[0][1]
    assert "No assistant summary was produced" not in sent[0][1]
    assert any(event.type == "automation_finalized" and event.data["source_state"] == "failed" for event in events)


async def test_daemon_automation_finalizer_prompt_prioritizes_saved_tool_observations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = load_config()
    store = RunStore(tmp_path / "runs")
    automation_store = AutomationStore(tmp_path / "automations")
    automation = await automation_store.create(
        name="HN watch",
        prompt="Fetch Hacker News and produce HN Brief.",
        schedule="every 1 minutes",
        route="report",
        provider=config.general.default_provider,
        model=config.general.default_model,
    )
    run = await store.create_run(
        "Scheduled: HN watch",
        kind="chat",
        provider=config.general.default_provider,
        model=config.general.default_model,
        working_directory=tmp_path,
        state="failed",
    )
    await store.append_event(
        run.run_id,
        "tool_result",
        {
            "tool_call_id": "call_1",
            "name": "http_request",
            "arguments": {"url": "https://hacker-news.firebaseio.com/v0/item/1.json"},
            "is_error": False,
            "content": (
                'GET https://hacker-news.firebaseio.com/v0/item/1.json\n'
                "status: 200 OK\n\n"
                '{"title":"ChatGPT for Google Sheets exfiltrates workbooks",'
                '"url":"https://example.test/security","score":218,"descendants":75}'
            ),
            "metadata": {"status_code": 200, "url": "https://hacker-news.firebaseio.com/v0/item/1.json"},
        },
    )
    await store.append_event(run.run_id, "error", {"message": "Provider returned no assistant text or tool calls."})

    prompt = _automation_finalizer_prompt(
        automation,
        run,
        await store.load_events(run.run_id),
        "failed",
        max_context_chars=8000,
    )

    assert "write the report from that data even when the primary run ended with no assistant text" in prompt
    assert "Tool observations:" in prompt
    assert "Run-level errors after observations:" in prompt
    assert prompt.index("Tool observations:") < prompt.index("Run-level errors after observations:")
    assert "ChatGPT for Google Sheets exfiltrates workbooks" in prompt


async def test_daemon_failed_telegram_automation_hides_partial_scratch_summary(tmp_path: Path) -> None:
    config = load_config()
    tool_limit = config.agent.max_tool_calls_per_turn
    store = RunStore(tmp_path / "runs")
    run = await store.create_run(
        "Scheduled: HN watch",
        kind="chat",
        provider=config.general.default_provider,
        model=config.general.default_model,
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
    await store.append_event(
        run.run_id,
        "error",
        {"message": f"Stopped after exceeding {tool_limit} tool calls in one turn."},
    )
    failed_run = await store.load_run(run.run_id)
    assert failed_run is not None
    automation = await AutomationStore(tmp_path / "automations").create(
        name="HN watch",
        prompt="Run scheduled work",
        schedule="every 1 minutes",
        route="telegram",
        provider=config.general.default_provider,
        model=config.general.default_model,
        telegram_chat_id=42,
    )

    message = _automation_telegram_message(automation, failed_run, tmp_path / "report.md", "failed")

    assert "finished with state: failed" in message
    assert "failed before Libre Claw produced a final clean report" in message
    assert f"Stopped after exceeding {tool_limit} tool calls" in message
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
    assert payload["attribution"]["analytics_url"] == "https://openrouter.ai/apps?url=https://libreclaw.sh"
    assert payload["attribution"]["docs_url"] == "https://libreclaw.sh/docs/"
    assert payload["attribution"]["ranking_targets"] == "Productivity, Coding Agents, Personal Agents, CLI Agents"
    assert "OpenRouter usage" in payload["text"]
