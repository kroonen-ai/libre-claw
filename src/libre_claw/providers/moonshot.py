# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from libre_claw.core.session import ContentBlock
from libre_claw.kimi import MoonshotService
from libre_claw.providers.base import ReasoningDelta
from libre_claw.providers.openai import OpenAIProvider, _format_assistant_message, _object_field


MoonshotReasoningEffort = Literal["low", "high", "max"]
MoonshotThinking = Literal["auto", "enabled", "disabled"]


class MoonshotProvider(OpenAIProvider):
    """OpenAI-compatible provider for Kimi Code and Moonshot Platform."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        *,
        base_url: str = "https://api.kimi.com/coding/v1",
        service: MoonshotService = "kimi_code",
        reasoning_effort: MoonshotReasoningEffort = "high",
        thinking: MoonshotThinking = "auto",
        client: object | None = None,
    ) -> None:
        self.service = service
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking
        super().__init__(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            display_name="Kimi Code" if service == "kimi_code" else "Moonshot AI",
            client=client,
        )

    def _extra_body(self) -> dict[str, Any]:
        if self._is_kimi_k2_6() and self.thinking == "disabled":
            return {"thinking": {"type": "disabled"}}
        return {}

    def _extra_request_parameters(self) -> dict[str, Any]:
        if self._is_kimi_k3():
            return {"reasoning_effort": self.reasoning_effort}
        return {}

    def _format_assistant_message(self, blocks: Sequence[ContentBlock]) -> dict[str, Any]:
        return _format_assistant_message(blocks, reasoning_provider="moonshot")

    def _max_tokens_field(self) -> str:
        if self.service == "platform" and self.model.lower().startswith("kimi-k3"):
            return "max_completion_tokens"
        return "max_tokens"

    def _reasoning_delta(self, delta: Any) -> ReasoningDelta | None:
        content = _object_field(delta, "reasoning_content")
        if not content:
            return None
        return ReasoningDelta(text=str(content), provider="moonshot")

    def _stream_options(self) -> dict[str, Any] | None:
        # Moonshot reports usage on the final choice and does not document
        # OpenAI's stream_options extension.
        return None

    def _supports_temperature(self) -> bool:
        # Current Kimi models require fixed sampling parameters.
        return False

    def _is_kimi_k2_6(self) -> bool:
        return self.model.lower().startswith("kimi-k2.6")

    def _is_kimi_k3(self) -> bool:
        normalized = self.model.lower()
        return normalized == "k3" or normalized.startswith("kimi-k3")
