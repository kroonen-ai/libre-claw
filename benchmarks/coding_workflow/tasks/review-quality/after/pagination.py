# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

def page_count(total: int, size: int) -> int:
    """Count pages; reject a nonpositive page size with ValueError."""
    if size < 0:
        raise ValueError("size must be positive")
    return (total + size - 1) // size
