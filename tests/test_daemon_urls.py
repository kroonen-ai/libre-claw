# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from aiohttp.test_utils import TestServer

from libre_claw.config import LibreClawConfig, load_config
from libre_claw.core.runs import RunStore
from libre_claw.core.session import ChatMessage
from libre_claw.core.tools import ToolRegistry
from libre_claw.daemon import DaemonClient, DaemonServer, daemon_base_url
from libre_claw.providers.base import Done, LLMProvider, StreamEvent, TextDelta, ToolSchema


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LibreClawConfig:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LIBRE_CLAW_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.chdir(tmp_path)
    config = load_config(working_directory=tmp_path)
    return replace(
        config,
        general=replace(config.general, default_provider="anthropic", default_model="url-test-model"),
        automations=replace(config.automations, enabled=False, root=tmp_path / "automations"),
        fallback=replace(config.fallback, enabled=False),
        memory=replace(config.memory, enabled=False, auto_extract=False),
        petdex=replace(config.petdex, enabled=False),
        telegram=replace(config.telegram, enabled=False),
    )


@pytest.mark.parametrize("bind_host,client_host", [
    ("0.0.0.0", "127.0.0.1"),
    ("::", "[::1]"),
    ("[::]", "[::1]"),
    ("127.0.0.1", "127.0.0.1"),
    ("localhost", "localhost"),
    ("192.168.1.20", "192.168.1.20"),
    ("::1", "[::1]"),
    ("[::1]", "[::1]"),
    ("fd00::20", "[fd00::20]"),
    ("[fd00::20]", "[fd00::20]"),
    ("claw.internal", "claw.internal"),
])
def test_daemon_base_url_uses_a_connectable_host_without_changing_bind_config(
    isolated_config: LibreClawConfig, bind_host: str, client_host: str,
) -> None:
    config = replace(isolated_config, daemon=replace(isolated_config.daemon, host=bind_host, port=8766))

    assert daemon_base_url(config) == f"http://{client_host}:8766"
    assert config.daemon.host == bind_host
    assert config.daemon.port == 8766


@pytest.mark.parametrize("host,expected_host", [
    ("0.0.0.0", "127.0.0.1"),
    ("::", "[::1]"),
    ("[fd00::20]", "[fd00::20]"),
])
def test_daemon_base_url_normalizes_explicit_host_and_port_overrides(
    isolated_config: LibreClawConfig, host: str, expected_host: str,
) -> None:
    config = replace(isolated_config, daemon=replace(isolated_config.daemon, host="claw.internal", port=8766))

    assert daemon_base_url(config, host=host, port=9876) == f"http://{expected_host}:9876"
    assert config.daemon.host == "claw.internal"
    assert config.daemon.port == 8766


@pytest.mark.parametrize("url,expected", [
    ("http://0.0.0.0:8766", "http://127.0.0.1:8766"),
    ("http://0.0.0.0:8766/", "http://127.0.0.1:8766"),
    ("http://[::]:8766", "http://[::1]:8766"),
    ("https://[::]:9876/", "https://[::1]:9876"),
    ("http://127.0.0.1:8766/", "http://127.0.0.1:8766"),
    ("http://[::1]:8766", "http://[::1]:8766"),
    ("http://[fd00::20]:8766", "http://[fd00::20]:8766"),
    ("https://claw.internal:9876/", "https://claw.internal:9876"),
])
def test_daemon_client_normalizes_user_supplied_wildcard_urls(url: str, expected: str) -> None:
    assert DaemonClient(url).base_url == expected


async def test_daemon_run_passes_bind_overrides_to_embedded_telegram_bridge(
    isolated_config: LibreClawConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        isolated_config,
        daemon=replace(isolated_config.daemon, host="127.0.0.1", port=8766),
        telegram=replace(isolated_config.telegram, enabled=True, use_daemon=True),
    )
    bridge_started = asyncio.Event()
    bridge_urls: list[str] = []
    bound_addresses: list[tuple[str, int]] = []

    async def fake_telegram_runner(bridge_config: LibreClawConfig) -> None:
        bridge_urls.append(daemon_base_url(bridge_config))
        bridge_started.set()
        await asyncio.Event().wait()

    server = DaemonServer(
        config, run_store=RunStore(tmp_path / "runs"), telegram_bot_runner=fake_telegram_runner,
    )

    class FakeSite:
        def __init__(self, runner: object, host: str, port: int) -> None:
            bound_addresses.append((host, port))

        async def start(self) -> None:
            await bridge_started.wait()
            assert server._shutdown_event is not None
            server._shutdown_event.set()

    monkeypatch.setattr("libre_claw.daemon.web.TCPSite", FakeSite)
    monkeypatch.setattr("libre_claw.daemon._telegram_token_available", lambda _config: True)
    async with asyncio.timeout(2):
        await server.run(host="0.0.0.0", port=9876)

    assert bound_addresses == [("0.0.0.0", 9876)]
    assert bridge_urls == ["http://127.0.0.1:9876"]
    assert server.config.daemon.host == "0.0.0.0"
    assert server.config.daemon.port == 9876
    assert config.daemon.host == "127.0.0.1"
    assert config.daemon.port == 8766


async def test_telegram_stack_passes_bind_overrides_to_server_and_bot(
    isolated_config: LibreClawConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from libre_claw.cli import _run_telegram_stack

    config = replace(isolated_config, daemon=replace(isolated_config.daemon, host="127.0.0.1", port=8766))
    server_configs: list[LibreClawConfig] = []
    bot_configs: list[LibreClawConfig] = []
    bound_addresses: list[tuple[str, int]] = []

    class FakeServer:
        def __init__(self, server_config: LibreClawConfig, *, start_telegram_bridge: bool) -> None:
            assert start_telegram_bridge is False
            self.config = server_config
            server_configs.append(server_config)

        async def run(self, host: str | None = None, port: int | None = None) -> None:
            bound_addresses.append((host or self.config.daemon.host, port or self.config.daemon.port))
            await asyncio.Event().wait()

    class FakeTelegramBot:
        def __init__(self, bot_config: LibreClawConfig) -> None:
            bot_configs.append(bot_config)

        async def run(self) -> None:
            return

    monkeypatch.setattr("libre_claw.cli.DaemonServer", FakeServer)
    monkeypatch.setattr("libre_claw.cli.TelegramBot", FakeTelegramBot)
    async with asyncio.timeout(2):
        await _run_telegram_stack(config, host="0.0.0.0", port=9876)

    assert bound_addresses == [("0.0.0.0", 9876)]
    assert len(server_configs) == len(bot_configs) == 1
    for effective_config in (server_configs[0], bot_configs[0]):
        assert effective_config.daemon.host == "0.0.0.0"
        assert effective_config.daemon.port == 9876
        assert daemon_base_url(effective_config) == "http://127.0.0.1:9876"
    assert config.daemon.host == "127.0.0.1"
    assert config.daemon.port == 8766


class StaticProvider(LLMProvider):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("Local daemon regression passed.")
        yield Done()


@pytest.mark.parametrize("use_config_helper", [True, False], ids=["config-helper", "user-url"])
async def test_wildcard_daemon_accepts_native_runs_and_model_updates_over_http(
    isolated_config: LibreClawConfig, tmp_path: Path, use_config_helper: bool,
) -> None:
    config = replace(isolated_config, daemon=replace(isolated_config.daemon, host="0.0.0.0"))

    async def reject_telegram(*_args: object) -> None:
        pytest.fail("The daemon URL regression must not contact Telegram.")

    server = DaemonServer(
        config,
        run_store=RunStore(tmp_path / "runs"),
        provider_factory=lambda _config: StaticProvider(),
        registry_factory=lambda _config, _memory: ToolRegistry(),
        telegram_sender=reject_telegram,
        telegram_bot_runner=reject_telegram,
        start_telegram_bridge=False,
    )
    async with TestServer(server.app(), host=config.daemon.host) as listener:
        assert listener.host == "0.0.0.0"
        server.config = replace(config, daemon=replace(config.daemon, port=listener.port))
        url = (
            daemon_base_url(server.config)
            if use_config_helper
            else f"http://0.0.0.0:{listener.port}"
        )
        client = DaemonClient(url, transport=httpx.AsyncHTTPTransport())
        assert client.base_url == f"http://127.0.0.1:{listener.port}"
        assert (await client.health())["host"] == "0.0.0.0"

        async with httpx.AsyncClient(base_url=client.base_url, trust_env=False) as raw_client:
            for headers in (
                {"Host": f"0.0.0.0:{listener.port}"},
                {"Host": "rebound.attacker.invalid"},
                {"Origin": "https://attacker.invalid"},
            ):
                response = await raw_client.post("/runs", json={"message": "Reject this run"}, headers=headers)
                assert response.status_code == 403
        assert await server.run_store.list_runs() == []

        updated = await client.update_model("ollama", "url-test-updated", persist_global=False)
        assert updated["provider"] == "ollama"
        assert updated["model"] == "url-test-updated"
        assert updated["persisted_path"] is None
        assert (await client.current_model())["model"] == "url-test-updated"
        assert not (tmp_path / ".libre-claw" / "config.toml").exists()

        started = await client.start_run("Check the local daemon", surface="telegram")
        run_id = started["run"]["run_id"]
        async with asyncio.timeout(5):
            while True:
                run = (await client.get_run(run_id))["run"]
                if run["state"] in {"done", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.01)
        assert run["state"] == "done"
        assert run["provider"] == "ollama"
        assert run["model"] == "url-test-updated"
        events = (await client.get_events(run_id))["events"]
        assert any(
            event["type"] == "assistant_delta" and event["data"]["text"] == "Local daemon regression passed."
            for event in events
        )
        assert server.config.daemon.host == "0.0.0.0"
