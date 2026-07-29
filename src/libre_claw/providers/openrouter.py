# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from libre_claw.core.session import ContentBlock
from libre_claw.providers.base import ReasoningDelta
from libre_claw.providers.openai import OpenAIProvider, _format_assistant_message, _object_field

OPENROUTER_HTTP_REFERER = "https://libreclaw.sh"
OPENROUTER_DOCS_URL = "https://libreclaw.sh/docs/"
OPENROUTER_APP_TITLE = "Libre Claw"
OPENROUTER_CATEGORIES = "cli-agent,personal-agent"
OPENROUTER_RANKING_TARGETS = (
    "Productivity",
    "Coding Agents",
    "Personal Agents",
    "CLI Agents",
)


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider using its OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str = "https://openrouter.ai/api/v1",
        client: object | None = None,
    ) -> None:
        headers = {
            "HTTP-Referer": OPENROUTER_HTTP_REFERER,
            "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
            "X-OpenRouter-Categories": OPENROUTER_CATEGORIES,
        }

        super().__init__(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            default_headers=headers,
            display_name="OpenRouter",
            client=client,
        )

    def _extra_body(self) -> dict[str, Any]:
        return {"usage": {"include": True}}

    def _format_assistant_message(self, blocks: Sequence[ContentBlock]) -> dict[str, Any]:
        return _format_assistant_message(blocks, reasoning_details_provider="openrouter")

    def _max_tokens_field(self) -> str:
        return "max_tokens"

    def _reasoning_delta(self, delta: Any) -> ReasoningDelta | None:
        raw_details = _object_field(delta, "reasoning_details")
        if not isinstance(raw_details, Sequence) or isinstance(raw_details, str | bytes):
            return None

        chunks: list[str] = []
        for detail in raw_details:
            payload = _reasoning_detail_payload(detail)
            if payload is not None:
                chunks.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        if not chunks:
            return None
        return ReasoningDelta(text="".join(chunks), provider="openrouter")

    def _supports_temperature(self) -> bool:
        normalized = self.model.lower()
        fixed_sampling_model = (
            normalized.startswith("anthropic/claude-opus-4.7")
            or normalized.startswith("anthropic/claude-opus-4.8")
            or normalized.startswith("anthropic/claude-opus-5")
            or normalized.startswith("anthropic/claude-sonnet-4.6")
            or normalized.startswith("anthropic/claude-sonnet-5")
        )
        return not fixed_sampling_model and super()._supports_temperature()


def _reasoning_detail_payload(detail: Any) -> dict[str, Any] | None:
    if isinstance(detail, Mapping):
        return dict(detail)

    model_dump = getattr(detail, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=False)
        return payload if isinstance(payload, dict) else None

    if hasattr(detail, "__dict__"):
        return dict(vars(detail))
    return None
