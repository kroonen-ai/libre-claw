# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Shared OpenCode provider identities and credential lookup rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libre_claw.auth.api_keys import ApiKeyLookup, ApiKeyStore


OPENCODE_PROVIDERS = ("opencode", "opencode-go")
OPENCODE_ALIASES = {
    "zen": "opencode", "opencode-zen": "opencode", "opencode_zen": "opencode",
    "go": "opencode-go", "opencode_go": "opencode-go",
}
OPENCODE_KEY_ENVS = {"opencode": "OPENCODE_API_KEY", "opencode-go": "OPENCODE_GO_API_KEY"}
OPENCODE_AUTH_URL = "https://opencode.ai/auth"


def canonical_opencode_provider(name: str) -> str:
    normalized = name.strip().lower()
    return OPENCODE_ALIASES.get(normalized, normalized)


def opencode_key_accounts(name: str) -> tuple[str, ...]:
    canonical = canonical_opencode_provider(name)
    if canonical not in OPENCODE_PROVIDERS:
        return ()
    return (canonical, *(alias for alias, target in OPENCODE_ALIASES.items() if target == canonical))


def lookup_opencode_key(store: ApiKeyStore, provider: str, env_var: str | None = None) -> ApiKeyLookup:
    canonical = canonical_opencode_provider(provider)
    default_env = OPENCODE_KEY_ENVS[canonical]
    selected_env = env_var if env_var is not None else default_env
    lookup = store.get_api_key(canonical, selected_env)
    if lookup.value or canonical != "opencode-go" or selected_env != default_env:
        return _validated_lookup(lookup)
    # An explicitly stored Go key takes priority over the shared console env.
    # Never substitute a saved Zen account or a different billing endpoint.
    return _validated_lookup(store.get_api_key(canonical, "OPENCODE_API_KEY"))


def _validated_lookup(lookup: ApiKeyLookup) -> ApiKeyLookup:
    from libre_claw.auth.api_keys import ApiKeyLookup, KeyStorageError
    if lookup.value is None:
        return lookup
    key = lookup.value.strip()
    if not key or len(key) > 16384 or any(not 33 <= ord(character) <= 126 for character in key):
        raise KeyStorageError("OpenCode API keys must be non-empty, single-line tokens.")
    return ApiKeyLookup(value=key, source=lookup.source)
