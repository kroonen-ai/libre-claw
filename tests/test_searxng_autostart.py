# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from libre_claw.config import WebSearchConfig
from libre_claw.core import searxng as searxng_module
from libre_claw.core.searxng import SearxngUpResult


def _web_search(**overrides: object) -> WebSearchConfig:
    base = WebSearchConfig(
        enabled=True,
        provider="searxng",
        base_url="http://127.0.0.1:8888",
        timeout=15,
        max_results=10,
        default_language="en",
        default_safesearch=0,
        default_categories=(),
        default_engines=(),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


@dataclass
class _StubConfig:
    web_search: WebSearchConfig


def test_ensure_searxng_up_returns_error_when_docker_missing(monkeypatch, tmp_path: Path) -> None:
    """Missing Docker must not raise — it returns an error result."""
    def _raise_file_not_found(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(searxng_module.subprocess, "run", _raise_file_not_found)

    result = searxng_module.ensure_searxng_up(root=tmp_path)

    assert result.status == "error"
    assert "Docker was not found" in result.message


def test_ensure_searxng_up_started_on_success(monkeypatch, tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="Started searxng", stderr="")
    monkeypatch.setattr(searxng_module.subprocess, "run", lambda *_a, **_k: completed)

    result = searxng_module.ensure_searxng_up(root=tmp_path)

    assert result.status == "started"
    assert "127.0.0.1:8888" in result.message


def test_ensure_searxng_up_already_running(monkeypatch, tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="Running", stderr="")
    monkeypatch.setattr(searxng_module.subprocess, "run", lambda *_a, **_k: completed)

    result = searxng_module.ensure_searxng_up(root=tmp_path)

    assert result.status == "already-running"


def test_ensure_searxng_up_error_on_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(searxng_module.subprocess, "run", lambda *_a, **_k: completed)

    result = searxng_module.ensure_searxng_up(root=tmp_path)

    assert result.status == "error"
    assert "boom" in result.message


def test_maybe_autostart_skips_when_web_search_disabled(monkeypatch) -> None:
    """Disabled web search must never trigger Docker."""
    from libre_claw import cli as cli_module

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(cli_module, "ensure_searxng_up", lambda *a, **k: calls.append((a, k)) or SearxngUpResult(Path("/x"), "started", ""))

    config = _StubConfig(web_search=_web_search(enabled=False))
    cli_module._maybe_autostart_searxng(config)

    assert calls == []


def test_maybe_autostart_skips_remote_searxng(monkeypatch) -> None:
    """A non-loopback base_url must not trigger local Docker."""
    from libre_claw import cli as cli_module

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(cli_module, "ensure_searxng_up", lambda *a, **k: calls.append((a, k)) or SearxngUpResult(Path("/x"), "started", ""))

    config = _StubConfig(web_search=_web_search(base_url="https://searx.example.com"))
    cli_module._maybe_autostart_searxng(config)

    assert calls == []


def test_maybe_autostart_fires_for_local_searxng(monkeypatch) -> None:
    """Enabled + searxng + loopback URL triggers ensure_searxng_up."""
    from libre_claw import cli as cli_module

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(cli_module, "ensure_searxng_up", lambda *a, **k: calls.append((a, k)) or SearxngUpResult(Path("/x"), "started", "ok"))

    config = _StubConfig(web_search=_web_search())
    cli_module._maybe_autostart_searxng(config)

    assert len(calls) == 1