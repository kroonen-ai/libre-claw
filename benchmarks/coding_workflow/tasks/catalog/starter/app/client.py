# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from app.catalog import available


def select(entries, model=None):
    chosen = model or available(entries)[0]
    if chosen != "old-model":
        raise ValueError("Unknown model")
    return chosen
