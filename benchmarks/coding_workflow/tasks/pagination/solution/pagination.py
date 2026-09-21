# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def paginate(items, size):
    """Split items into successive pages."""
    if size < 1:
        raise ValueError("size must be positive")
    return [items[index:index + size] for index in range(0, len(items), size)]
