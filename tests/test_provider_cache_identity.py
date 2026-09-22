# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from libre_claw.providers.cache_identity import cache_identity


def test_cache_identity_preserves_accounts_and_separates_purposes() -> None:
    first = cache_identity("example-credential-one", purpose="models")
    assert first == cache_identity("example-credential-one", purpose="models")
    assert first != cache_identity("example-credential-two", purpose="models")
    assert first != cache_identity("example-credential-one", purpose="limits")
    assert first != cache_identity("", purpose="models")
    assert "example-credential-one" not in first
    assert len(first) == 64


def test_cache_identity_changes_between_processes() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    command = [
        sys.executable, "-c",
        "from libre_claw.providers.cache_identity import cache_identity; " +
        "print(cache_identity('example-credential', purpose='models'))",
    ]
    environment = {**os.environ, "PYTHONPATH": str(source)}
    first = subprocess.check_output(command, env=environment, text=True).strip()
    second = subprocess.check_output(command, env=environment, text=True).strip()
    assert first != second
    assert first != cache_identity("example-credential", purpose="models")
