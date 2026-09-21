# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

def can_open(owner_id: str, viewer_id: str, active: bool) -> bool:
    """Only the owner of an active document may open it."""
    return owner_id == viewer_id or active
