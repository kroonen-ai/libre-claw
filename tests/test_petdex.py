# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from xml.etree import ElementTree

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
@pytest.mark.parametrize("state", ["working", "running"], ids=["working-alias", "running-canonical"])
async def test_petdex_posts_authenticated_normalized_state(tmp_path: Path, state: str) -> None:
    config = _config(tmp_path)
    config.token_path.write_text("secret-token\n", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PetdexClient(config, http_client=http_client)
        result = await client.send_state(state, message="Inspecting files", details={"tool": "read_file"})

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
        "text": "Inspecting files · read_file",
        "agent_source": "libre-claw-test",
        "source_label": "Libre Claw",
        "source_icon": "agents/libre-claw.svg",
    }


@pytest.mark.asyncio
async def test_petdex_installs_lobster_agent_avatar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.token_path.write_text("secret-token\n", encoding="utf-8")
    requests: list[httpx.Request] = []
    webview = tmp_path / "webview"
    agents = webview / "agents"
    agents.mkdir(parents=True)
    index = webview / "index.html"
    index.write_text(
        "const AGENT_AVATARS = {\n"
        "    'codex': 'agents/codex.svg',\n"
        "  };\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PetdexClient(config, http_client=http_client)
        result = await client.send_state("success", message="Run done")

    assert result.ok is True
    avatar_svg = (agents / "libre-claw.svg").read_text(encoding="utf-8")
    bundled_svg = resources.files("libre_claw.web.assets").joinpath("lobster-icon.svg").read_text(encoding="utf-8")
    assert avatar_svg == bundled_svg
    avatar = ElementTree.fromstring(avatar_svg)
    assert avatar.tag == "{http://www.w3.org/2000/svg}svg"
    assert len(avatar) > 0
    assert "'libre-claw': 'agents/libre-claw.svg'" in index.read_text(encoding="utf-8")
    assert json.loads(requests[1].content)["text"] == "Run done"


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
