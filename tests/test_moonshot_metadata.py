# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from libre_claw.config import load_config
from libre_claw.providers.moonshot_metadata import apply_moonshot_model_limits


def test_moonshot_metadata_applies_published_kimi_k3_limits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                'default_model = "k3"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(config_path=config_path)

    updated = apply_moonshot_model_limits(config)

    assert updated.agent.context_window_tokens == 1_048_576
    assert updated.providers["moonshot"]["detected_context_window_tokens"] == 1_048_576
    assert "detected_max_completion_tokens" not in updated.providers["moonshot"]
    assert updated.providers["moonshot"]["detected_context_source"] == "kimi-code-docs"
    assert updated.providers["moonshot"]["detected_context_model"] == "k3"


def test_moonshot_metadata_uses_provider_default_after_provider_only_switch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                'default_model = "claude-opus-4-8"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(config_path=config_path)

    updated = apply_moonshot_model_limits(config)

    assert updated.agent.context_window_tokens == 1_048_576
    assert updated.providers["moonshot"]["detected_context_model"] == "k3"


def test_moonshot_metadata_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[general]",
                'default_provider = "moonshot"',
                'default_model = "kimi-k2.7-code"',
                "",
                "[providers.moonshot]",
                "auto_context_window = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(config_path=config_path)

    updated = apply_moonshot_model_limits(config)

    assert updated is config
    assert updated.agent.context_window_tokens == 200_000
