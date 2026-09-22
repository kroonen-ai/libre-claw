# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Opaque identities for credential-scoped, in-memory provider caches."""

from __future__ import annotations

import hmac
import json
import secrets


# Never persisted: restarting the process invalidates both these identities and
# the caches that use them. A cache key alone cannot be used to test credentials
# offline or correlate the same account across independent Libre Claw runs.
_PROCESS_CACHE_KEY = secrets.token_bytes(32)


def cache_identity(value: str, *, purpose: str) -> str:
    """Partition a cache without retaining credentials or stable fingerprints.

    This is a keyed cache identifier, not a password verifier. Purpose separation
    prevents linking credentials between caches, and structured encoding avoids
    ambiguous boundaries between the purpose and its value.
    """
    message = json.dumps((purpose, value), ensure_ascii=True).encode("utf-8")
    return hmac.digest(_PROCESS_CACHE_KEY, message, "sha256").hex()
