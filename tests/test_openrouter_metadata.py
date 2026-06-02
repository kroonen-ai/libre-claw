# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import httpx

from libre_claw.config import load_config
from libre_claw.providers.openrouter_metadata import apply_openrouter_model_limits, detect_openrouter_model_limits


def _config_path(tmp_path: Path, *, base_url: str, model: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "openrouter"',
                f'default_model = "{model}"',
                "",
                "[providers.openrouter]",
                'api_key_env = "OPENROUTER_API_KEY"',
                f'base_url = "{base_url}"',
                f'default_model = "{model}"',
                "max_tokens = 16384",
                "auto_context_window = true",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


async def test_detect_openrouter_limits_from_model_list(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    base_url = "https://openrouter-metadata-list.test/api/v1"
    config = load_config(config_path=_config_path(tmp_path, base_url=base_url, model="qwen/qwen3.7-max"))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "qwen/qwen3.7-max",
                        "context_length": 1_048_576,
                        "top_provider": {"context_length": 262_144, "max_completion_tokens": 32_768},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        limits = await detect_openrouter_model_limits(config, client=client)

    assert limits.context_window_tokens == 1_048_576
    assert limits.max_completion_tokens == 32_768
    assert limits.source == "models"

    updated = apply_openrouter_model_limits(config, limits, model="qwen/qwen3.7-max")
    assert updated.agent.context_window_tokens == 1_048_576
    assert updated.providers["openrouter"]["detected_context_window_tokens"] == 1_048_576
    assert updated.providers["openrouter"]["detected_max_completion_tokens"] == 32_768
    assert updated.providers["openrouter"]["detected_context_model"] == "qwen/qwen3.7-max"


async def test_detect_openrouter_limits_falls_back_to_endpoints(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    base_url = "https://openrouter-metadata-endpoints.test/api/v1"
    config = load_config(config_path=_config_path(tmp_path, base_url=base_url, model="minimax/minimax-m3"))
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": []})
        assert request.url.path == "/api/v1/models/minimax/minimax-m3/endpoints"
        return httpx.Response(
            200,
            json={
                "data": {
                    "endpoints": [
                        {"max_prompt_tokens": 131_072, "max_completion_tokens": 16_384},
                        {"context_length": 262_144, "max_completion_tokens": 8_192},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        limits = await detect_openrouter_model_limits(config, client=client)

    assert seen_paths == ["/api/v1/models", "/api/v1/models/minimax/minimax-m3/endpoints"]
    assert limits.context_window_tokens == 262_144
    assert limits.max_completion_tokens == 16_384
    assert limits.source == "endpoints"


async def test_openrouter_metadata_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = _config_path(tmp_path, base_url="https://openrouter-disabled.test/api/v1", model="openrouter/auto")
    config_path.write_text(config_path.read_text(encoding="utf-8").replace("auto_context_window = true", "auto_context_window = false"), encoding="utf-8")
    config = load_config(config_path=config_path)

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled OpenRouter metadata should not call the network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        limits = await detect_openrouter_model_limits(config, client=client)

    assert limits.source == "disabled"
    assert limits.detected is False
