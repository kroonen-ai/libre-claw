# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from libre_claw.config import PetdexConfig
from libre_claw.integrations.petdex import PetdexClient, petdex_tool_details


def _config(tmp_path: Path, *, enabled: bool = True) -> PetdexConfig:
    return PetdexConfig(
        enabled=enabled,
        base_url="http://127.0.0.1:7777",
        token_path=tmp_path / "update-token",
        source="libre-claw-test",
        bubble_prefix="🦞",
        timeout=1.0,
        notify_tui=True,
        notify_daemon=True,
        notify_telegram=True,
        notify_automations=True,
    )


@pytest.mark.asyncio
async def test_petdex_disabled_skips(tmp_path: Path) -> None:
    client = PetdexClient(_config(tmp_path, enabled=False))

    result = await client.send_state("running", message="hello")

    assert result.ok is False
    assert result.skipped is True
    assert "disabled" in result.message


@pytest.mark.asyncio
async def test_petdex_missing_token_skips(tmp_path: Path) -> None:
    client = PetdexClient(_config(tmp_path))

    result = await client.send_state("running", message="hello")

    assert result.ok is False
    assert result.skipped is True
    assert "token" in result.message.lower()


@pytest.mark.asyncio
async def test_petdex_posts_authenticated_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.token_path.write_text("secret-token\n", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PetdexClient(config, http_client=http_client)
        result = await client.send_state("working", message="Inspecting files", details={"tool": "read_file"})

    assert result.ok is True
    assert len(requests) == 2
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:7777/state"
    assert request.headers["x-petdex-update-token"] == "secret-token"
    payload = json.loads(request.content)
    assert payload == {
        "state": "running",
        "agent_source": "libre-claw-test",
    }
    bubble_request = requests[1]
    assert str(bubble_request.url) == "http://127.0.0.1:7777/bubble"
    assert bubble_request.headers["x-petdex-update-token"] == "secret-token"
    assert json.loads(bubble_request.content) == {
        "text": "🦞 Inspecting files · read_file",
        "agent_source": "libre-claw-test",
        "source_label": "Libre Claw",
        "source_icon": "🦞",
    }


@pytest.mark.asyncio
async def test_petdex_http_error_is_nonfatal(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.token_path.write_text("secret-token", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PetdexClient(config, http_client=http_client)
        result = await client.send_state("error", message="provider failed")

    assert result.ok is False
    assert result.skipped is False
    assert "Petdex update failed" in result.message


def test_petdex_tool_details_compacts_known_tools() -> None:
    assert petdex_tool_details("bash", {"command": "pytest -q"}) == {"tool": "bash", "command": "pytest -q"}
    assert petdex_tool_details("http_request", {"url": "https://libreclaw.sh"}) == {
        "tool": "http_request",
        "target": "https://libreclaw.sh",
    }
