# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def available(entries):
    """Return distinct discovered IDs in provider order."""
    models = []
    for entry in entries:
        model = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(model, str) and model.strip() and model.strip() not in models:
            models.append(model.strip())
    return models
