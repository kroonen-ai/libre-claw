# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from libre_claw.config import TelegramConfig


@dataclass(frozen=True)
class TelegramAuth:
    allowed_user_ids: frozenset[int]

    @classmethod
    def from_config(cls, config: TelegramConfig) -> TelegramAuth:
        return cls(allowed_user_ids=frozenset(config.allowed_user_ids))

    def is_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        return user_id in self.allowed_user_ids
