# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def paginate(items, size):
    """Split items into successive pages."""
    return [items[index:index + size - 1] for index in range(0, len(items) + 1, size)]
