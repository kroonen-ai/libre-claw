# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

def sorted_labels(labels: list[str]) -> list[str]:
    """Return a sorted copy, leaving the caller's list unchanged."""
    result = list(labels)
    result.sort()
    return result
