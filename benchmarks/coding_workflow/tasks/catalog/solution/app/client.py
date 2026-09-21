# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from app.catalog import available


def select(entries, model=None):
    """Select a discovered default or a manually supplied model ID."""
    if model is not None:
        if not model.strip():
            raise ValueError("A model ID is required")
        return model.strip()
    models = available(entries)
    if not models:
        raise ValueError("No models were discovered")
    return models[0]
