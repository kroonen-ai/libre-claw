# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Explicit, provider-scoped import of OpenCode's saved API credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from libre_claw.auth.api_keys import ApiKeyStore, KeyStorageError
from libre_claw.opencode import OPENCODE_AUTH_URL, OPENCODE_PROVIDERS, canonical_opencode_provider


def default_opencode_auth_path() -> Path:
    configured = os.environ.get("XDG_DATA_HOME", "")
    root = Path(configured).expanduser() if configured and Path(configured).is_absolute() else Path.home() / ".local" / "share"
    return root / "opencode" / "auth.json"


def read_opencode_api_key(provider: str, path: Path | None = None) -> str:
    canonical = canonical_opencode_provider(provider)
    if canonical not in OPENCODE_PROVIDERS:
        raise KeyStorageError("Choose OpenCode Zen (opencode) or Go (opencode-go).")
    source = (path or default_opencode_auth_path()).expanduser()
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise KeyStorageError("OpenCode credentials must come from a regular JSON file.")
            data = handle.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise KeyStorageError("OpenCode credential file exceeds the size limit.")
        payload = json.loads(data)
    except FileNotFoundError as exc:
        raise KeyStorageError(f"No OpenCode credential file found. Connect at {OPENCODE_AUTH_URL} or use auth set-key.") from exc
    except (OSError, ValueError, UnicodeError) as exc:
        raise KeyStorageError("Could not read OpenCode's credential file as JSON.") from exc
    entry = payload.get(canonical) if isinstance(payload, dict) else None
    if not isinstance(entry, dict):
        raise KeyStorageError(f"No saved {canonical} API key exists in the selected OpenCode file.")
    if entry.get("type") != "api":
        raise KeyStorageError("OpenCode Go/Zen require an API key. OAuth and refresh tokens are not imported.")
    key = entry.get("key")
    if not isinstance(key, str) or not key.strip() or len(key) > 16384 or any(character.isspace() or ord(character) < 32 for character in key.strip()):
        raise KeyStorageError("The selected OpenCode API key is invalid.")
    return key.strip()


def import_opencode_key(store: ApiKeyStore, provider: str, *, path: Path | None = None, replace: bool = False) -> str:
    canonical = canonical_opencode_provider(provider)
    key = read_opencode_api_key(canonical, path)
    existing = store.get_api_key(canonical)
    if existing.value and existing.value != key and not replace:
        raise KeyStorageError(f"A different {canonical} key is already stored. Use --replace to replace it explicitly.")
    location = store.set_api_key(canonical, key)
    if store.get_api_key(canonical).value != key:
        raise KeyStorageError("Could not verify the imported key in Libre Claw's credential store.")
    return location
